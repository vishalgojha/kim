import asyncio
import os
import shutil
from datetime import datetime, timezone

from .registry import tool


@tool(
    "opencode_run",
    "Hand off a coding or multi-step software task to the opencode AI coding agent CLI and return its result. "
    "Use this for writing code, refactoring, complex git work, or any task that benefits from a dedicated coding agent. "
    "IMPORTANT: the caller only waits ~110s. For anything that may take longer, launch the command via start_task "
    "(e.g. opencode run '...' --dir /path) and poll it with task_log instead of using this tool.",
    {
        "task": {"type": "string", "description": "the instruction to give the coding agent", "required": True},
        "dir": {"type": "string", "description": "working directory (default current)", "required": False},
        "timeout": {"type": "integer", "description": "max seconds to wait, ≤110 (default 110)", "required": False},
    },
    timeout=115,
)
async def opencode_run(task: str, dir: str = "", timeout: int = 110) -> str:
    if not shutil.which("opencode"):
        return "opencode CLI not found on PATH"
    timeout = max(30, min(int(timeout), 110))
    cmd = ["opencode", "run", task, "--format", "default"]
    if dir:
        cmd += ["--dir", dir]
    logfile = os.path.expanduser("~/.aurora/opencode.log")
    start = datetime.now(timezone.utc)
    proc = await asyncio.create_subprocess_shell(
        " ".join(f'"{c}"' if " " in c else c for c in cmd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=os.environ.copy(),
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return f"opencode timed out after {timeout}s and was killed (see {logfile})"
    text = out.decode(errors="replace") if out else ""
    with open(logfile, "a") as f:
        f.write(f"\n[{start.isoformat()}] --- opencode task ---\n{task}\n--- result ---\n{text}\n")
    if len(text) > 30000:
        text = text[:30000] + "\n... (result truncated)"
    return text if text.strip() else f"(opencode produced no output, exit {proc.returncode})"