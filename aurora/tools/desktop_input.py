import asyncio
import platform
import shutil

from .registry import tool
from .win32 import IS_WINDOWS


_SAFE_KEYS = {
    "Return", "Escape", "Tab", "BackSpace", "Delete", "Home", "End",
    "Page_Up", "Page_Down", "Up", "Down", "Left", "Right", "space",
    "F1", "F2", "F3", "F4", "F5", "F6", "F7", "F8", "F9", "F10", "F11", "F12",
}


def _xdotool() -> str:
    path = shutil.which("xdotool")
    if not path:
        raise RuntimeError("desktop keyboard control needs xdotool installed on Linux")
    return path


@tool(
    "type_text",
    "Type a short, user-provided string into the currently focused Linux desktop field. "
    "Never use this for passwords, OTPs, or other secrets.",
    {
        "text": {"type": "string", "description": "text to type, maximum 500 characters", "required": True},
    },
    timeout=15,
)
async def type_text(text: str) -> str:
    if not text or len(text) > 500:
        raise ValueError("text must be 1-500 characters")
    if IS_WINDOWS:
        from .win32 import type_text as win_type
        ok, msg = win_type(text)
        if not ok:
            raise RuntimeError(msg)
        return f"typed {len(text)} characters"
    proc = await asyncio.create_subprocess_exec(
        _xdotool(), "type", "--clearmodifiers", "--delay", "8", "--", text,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    _, err = await proc.communicate()
    if proc.returncode:
        raise RuntimeError(err.decode(errors="replace").strip() or "xdotool failed")
    return f"typed {len(text)} characters"


@tool(
    "press_key",
    "Press one safe, named key on the Linux desktop. Allowed keys exclude arbitrary key combinations.",
    {
        "key": {"type": "string", "description": "Return, Escape, Tab, BackSpace, Delete, arrows, Home, End, space, or F1-F12", "required": True},
    },
    timeout=15,
)
async def press_key(key: str) -> str:
    normalized = key.strip()
    if normalized.lower() == "space":
        normalized = "space"
    if normalized not in _SAFE_KEYS:
        raise ValueError(f"key is not allow-listed: {key}")
    if IS_WINDOWS:
        from .win32 import key as win_key
        ok, msg = win_key(normalized)
        if not ok:
            raise RuntimeError(msg)
        return f"pressed {normalized}"
    proc = await asyncio.create_subprocess_exec(
        _xdotool(), "key", "--clearmodifiers", normalized,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    _, err = await proc.communicate()
    if proc.returncode:
        raise RuntimeError(err.decode(errors="replace").strip() or "xdotool failed")
    return f"pressed {normalized}"
