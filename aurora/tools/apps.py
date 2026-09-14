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
    "navigate_browser",
    "Navigate the currently open Chrome, Chromium, Firefox, or Brave window in its active tab. Use this for URLs unless the user explicitly asks for a new window or tab.",
    {"url": {"type": "string", "description": "HTTP or HTTPS URL", "required": True}},
    timeout=15,
)
async def navigate_browser(url: str) -> str:
    target = url.strip()
    if len(target) > 2_000 or not target.startswith(("http://", "https://")):
        return "only http:// and https:// URLs are supported"
    xdotool = shutil.which("xdotool")
    if xdotool:
        for browser_class in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "firefox", "brave-browser"):
            try:
                found = await asyncio.create_subprocess_exec(
                    xdotool, "search", "--onlyvisible", "--class", browser_class,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                )
                out, _ = await asyncio.wait_for(found.communicate(), timeout=3)
                window_id = next((line.strip() for line in out.decode(errors="replace").splitlines() if line.strip()), "")
                if not window_id:
                    continue
                for args in (("windowactivate", "--sync", window_id), ("key", "--clearmodifiers", "ctrl+l"), ("type", "--clearmodifiers", "--delay", "1", target), ("key", "--clearmodifiers", "Return")):
                    proc = await asyncio.create_subprocess_exec(xdotool, *args, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
                    await asyncio.wait_for(proc.wait(), timeout=3)
                return f"navigated the existing {browser_class} window"
            except (asyncio.TimeoutError, OSError):
                continue
    open_default(target)
    return "no open browser window was found; opened the URL with the default browser"


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
        return "browser is already open; use navigate_browser to change the current tab" if shutil.which("xdotool") else ("opened the default browser" if open_default("about:blank") else "failed to open the default browser")

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
