import asyncio
import pathlib
import shutil
import sys
import tempfile
import textwrap

from .registry import tool

PROJECT = pathlib.Path(__file__).resolve().parent.parent.parent
SCREENSHOTS = PROJECT / "screenshots"

DRIVER = textwrap.dedent(
    """\
import pathlib, sys, traceback
from playwright.sync_api import sync_playwright

script_path, url, shot_dir = sys.argv[1], sys.argv[2], sys.argv[3]
shot_dir = pathlib.Path(shot_dir)
user_script = pathlib.Path(script_path).read_text()

def run():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
    ctx = browser.new_context(viewport={"width": 1280, "height": 800}, user_agent="Kim/1.0")
        page = ctx.new_page()
        out = []
        if url:
            page.goto(url, timeout=25000, wait_until="load")
        if user_script.strip():
            scope = {"page": page, "browser": browser, "context": ctx, "out": out, "shot_dir": shot_dir}
            exec(compile(user_script, "<user-script>", "exec"), scope)
            val = scope.get("result")
            if val is not None:
                out.append(str(val))
            out.append("[out] " + "\\n".join(map(str, out)))
        else:
            text = page.locator("body").inner_text(timeout=5000)
            out.append(text[:12000])
            shot = shot_dir / "shot.png"
            page.screenshot(path=str(shot))
            out.append(f"[screenshot saved: {shot}]")
        browser.close()
        return "\\n".join(out)[:100000]

try:
    print(run())
except Exception:
    traceback.print_exc()
"""
)


@tool(
    "playwright_run",
    "Drive a real headless Chromium browser with Playwright. Pass a `url` to open a page first; optionally pass a `script` body of Python code that can use the bound `page` object: page.goto(), page.click(), page.fill(), page.inner_text(), page.locator('css').all_inner_texts(), page.keyboard, page.get_by_role(...), etc. Set a local variable `result` in your script to return text (a list/tuple is fine). Save screenshots with page.screenshot(path=f'{shot_dir}/x.png'). If only a `url` is given, Kim reads the page's text and saves a screenshot automatically. Use this for real web automation: login flows, form filling, scraping, checking dynamic pages, taking screenshots.",
    {
        "url": {"type": "string", "description": "URL to open first (optional)", "required": False},
        "script": {"type": "string", "description": "Python body executed with a live `page` object (optional)", "required": False},
        "timeout": {"type": "integer", "description": "max seconds ≤110 (default 60)", "required": False},
    },
    timeout=115,
)
async def playwright_run(url: str = "", script: str = "", timeout: int = 60) -> str:
    if not shutil.which("playwright"):
        return "playwright CLI not found (run: .venv/bin/python -m playwright install chromium)"
    timeout = max(10, min(int(timeout), 110))
    SCREENSHOTS.mkdir(parents=True, exist_ok=True)
    fd, spath = tempfile.mkstemp(suffix=".py")
    try:
        with open(fd, "w") as f:
            f.write(script or "")
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            DRIVER,
            spath,
            url,
            str(SCREENSHOTS),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            return "playwright timed out after {timeout}s (browser process killed)."
        stdout = (out or b"").decode(errors="replace").strip()
        stderr = (err or b"").decode(errors="replace").strip()
        if proc.returncode != 0:
            tail = stderr[-1500:] or "unknown error"
            return f"playwright error: {tail}"
        return stdout or "done"
    finally:
        pathlib.Path(spath).unlink(missing_ok=True)
