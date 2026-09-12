import asyncio
import os
import shutil
from ..host import open_default

from .registry import tool

ALIASES = {
    # Keep "browser" on the desktop's configured default handler. Explicit
    # browser names below remain available when the user asks for one.
    "browser": None,
    "firefox": "firefox",
    "chrome": "google-chrome",
    "vs code": "code",
    "vscode": "code",
    "code": "code",
    "editor": "code",
    "codium": "codium",
    "terminal": "gnome-terminal",
    "shell": "gnome-terminal",
    "spotify": "spotify",
    "slack": "slack",
    "discord": "discord",
    "telegram": "telegram-desktop",
    "whatsapp": "whatsapp-desktop",
    "obsidian": "obsidian",
    "files": "nautilus",
    "file manager": "nautilus",
    "settings": "gnome-control-center",
    "calculator": "gnome-calculator",
    "clocks": "gnome-clocks",
    "notes": "gnome-text-editor",
}


@tool(
    "launch_app",
    "Open an application or open a file/URL with its default handler. Supports common names (browser, editor, terminal, spotify...) or any program found on PATH, and falls back to xdg-open.",
    {
        "name": {"type": "string", "description": "app name, file path, or URL", "required": True},
    },
    timeout=30,
)
async def launch_app(name: str) -> str:
    requested = name.strip()
    normalized = requested.lower()
    if normalized == "browser":
        return "opened the default browser" if open_default("about:blank") else "failed to open the default browser"

    target = ALIASES.get(normalized, requested)
    executable = shutil.which(target)
    if executable:
        try:
            proc = await asyncio.create_subprocess_exec(
                executable,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                start_new_session=True,
            )
            return f"launched {target}"
        except Exception as e:
            return f"failed to launch {target}: {e}"
    xdg = shutil.which("xdg-open")
    if xdg:
        try:
            await asyncio.create_subprocess_exec(
                xdg, target, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
            )
            return f"opened {name} with default handler"
        except Exception as e:
            return f"failed xdg-open: {e}"
    if open_default(target):
        return f"opened {name} with the default handler"
    return f"could not find application '{name}'"


@tool(
    "known_apps",
    "List the application aliases the launch_app tool understands.",
    {},
    timeout=5,
)
def known_apps() -> str:
    return ", ".join(sorted({value for value in ALIASES.values() if value}))
