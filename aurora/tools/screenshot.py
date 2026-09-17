import asyncio
import shutil
import subprocess
import tempfile
from pathlib import Path

from .registry import tool
from .win32 import IS_WINDOWS


@tool(
    "screenshot",
    "Capture a screenshot of the screen to a PNG file and return its path. Best effort across X11/Wayland/Windows.",
    {"path": {"type": "string", "description": "optional output path (default: ~/Pictures)", "required": False}},
    timeout=30,
)
async def screenshot(path: str = "") -> str:
    out = Path(path).expanduser() if path else Path.home() / "Pictures"
    if out.is_dir():
        out = out / "aurora_screenshot.png"
    out.parent.mkdir(parents=True, exist_ok=True)

    if IS_WINDOWS:
        from .win32 import grab_png
        ok, msg = await asyncio.to_thread(grab_png, str(out))
        return msg if ok else "could not capture screenshot: " + msg

    strategies = []
    if shutil.which("gnome-screenshot"):
        strategies.append(["gnome-screenshot", "-f", str(out)])
    if shutil.which("grim"):
        strategies.append(["grim", str(out)])
    if shutil.which("scrot"):
        strategies.append(["scrot", str(out)])
    if shutil.which("import"):
        strategies.append(["import", "-window", "root", str(out)])
    if shutil.which("ffmpeg") and shutil.which("xdpyinfo"):
        strategies.append(
            ["ffmpeg", "-y", "-f", "x11grab", "-i", ":0", "-frames:v", "1", str(out)]
        )

    for cmd in strategies:
        try:
            r = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
            )
            await asyncio.wait_for(r.wait(), timeout=20)
            if out.exists() and out.stat().st_size > 0:
                return f"saved screenshot: {out} ({out.stat().st_size} bytes)"
        except Exception:
            continue
    return "could not capture screenshot (no gnome-screenshot/grim/scrot/imagemagick detected)"