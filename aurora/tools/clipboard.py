import shutil
import subprocess

from ..tools.context import get_ctx
from .registry import tool


def _tool(names: list[str]) -> str | None:
    for n in names:
        if shutil.which(n):
            return n
    return None


@tool("clipboard_get", "Read the current clipboard text.", {}, timeout=15)
def clipboard_get() -> str:
    prog = _tool(["wl-paste", "xclip", "xsel"])
    if not prog:
        return "no clipboard tool available (install wl-clipboard, xclip, or xsel)"
    try:
        out = subprocess.run(
            [prog, "-o"] if prog == "wl-paste" else ([prog, "-o", "-selection", "clipboard"] if prog == "xclip" else [prog, "-b"]),
            capture_output=True,
            text=True,
            timeout=10,
        )
        return out.stdout or "(empty clipboard)"
    except Exception as e:
        return f"clipboard read failed: {e}"


@tool(
    "clipboard_set",
    "Replace the clipboard contents with text. Use for 'copy this to clipboard' requests.",
    {"text": {"type": "string", "description": "text to copy", "required": True}},
    timeout=15,
)
def clipboard_set(text: str) -> str:
    prog = _tool(["wl-copy", "xclip", "xsel"])
    if not prog:
        return "no clipboard tool available (install wl-clipboard, xclip, or xsel)"
    try:
        args = [prog, "-i", "-selection", "clipboard"] if prog == "xclip" else ([prog, "-b"] if prog == "xsel" else [prog])
        subprocess.run(args, input=text, capture_output=True, timeout=10)
        return f"copied {len(text)} chars to clipboard"
    except Exception as e:
        return f"clipboard write failed: {e}"