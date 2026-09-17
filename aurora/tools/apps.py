import asyncio
import os
import shutil
from ..host import open_default

from .registry import tool
from .win32 import IS_WINDOWS

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
    if IS_WINDOWS:
        return await _navigate_browser_windows(target)
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
    "browser_action",
    "Control the currently visible Chrome, Chromium, Firefox, or Brave window. Use action click with x/y, type with text, key with a key name, or scroll with amount. This operates the user's visible browser window.",
    {
        "action": {"type": "string", "description": "click, type, key, or scroll", "required": True},
        "x": {"type": "integer", "description": "screen x coordinate for click", "required": False},
        "y": {"type": "integer", "description": "screen y coordinate for click", "required": False},
        "text": {"type": "string", "description": "text to type", "required": False},
        "key": {"type": "string", "description": "key or hotkey, for example Return, Escape, ctrl+l", "required": False},
        "amount": {"type": "integer", "description": "scroll amount; positive up, negative down", "required": False},
    },
    timeout=20,
)
async def browser_action(action: str, x: int = 0, y: int = 0, text: str = "", key: str = "", amount: int = 0) -> str:
    if IS_WINDOWS:
        return await _browser_action_windows(action, x, y, text, key, amount)
    xdotool = shutil.which("xdotool")
    if not xdotool:
        return "browser actions require xdotool on the desktop"
    action = action.strip().lower()
    if action == "click":
        if x < 0 or y < 0:
            return "click requires non-negative x and y coordinates"
        args = [xdotool, "mousemove", "--sync", str(x), str(y), "click", "1"]
    elif action == "type":
        if not text:
            return "type requires text"
        args = [xdotool, "type", "--clearmodifiers", "--delay", "1", text[:4000]]
    elif action == "key":
        if not key:
            return "key requires a key name"
        args = [xdotool, "key", "--clearmodifiers", key]
    elif action == "scroll":
        button = 4 if amount > 0 else 5
        args = [xdotool, "click", "--repeat", str(min(abs(amount), 20) or 1), str(button)]
    else:
        return "browser_action supports click, type, key, or scroll"
    try:
        proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        _, err = await asyncio.wait_for(proc.communicate(), timeout=8)
        if proc.returncode:
            return f"browser action failed: {err.decode(errors='replace').strip()[:300]}"
        return f"browser {action} completed"
    except asyncio.TimeoutError:
        return "browser action timed out"


@tool(
    "launch_app",
    "Open an application or open a file/URL with its default handler. Supports common names (browser, editor, terminal, spotify...) or any program found on PATH, and falls back to xdg-open.",
    {
        "name": {"type": "string", "description": "app name, file path, or URL", "required": True},
    },
    timeout=30,
)
async def launch_app(name: str) -> str:
    from .win32 import launch as win_launch
    requested = name.strip()
    normalized = requested.lower()
    if normalized == "browser":
        if IS_WINDOWS:
            return "opened the default browser" if open_default("about:blank") else "failed to open the default browser"
        return "browser is already open; use navigate_browser to change the current tab" if shutil.which("xdotool") else ("opened the default browser" if open_default("about:blank") else "failed to open the default browser")

    target = ALIASES.get(normalized, requested)
    if IS_WINDOWS:
        ok, msg = await _run_sync(win_launch, target)
        return msg if ok else f"failed to launch {requested}: {msg}"
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


async def _run_sync(fn, *args):
    return await asyncio.to_thread(fn, *args)


async def _navigate_browser_windows(target: str) -> str:
    from . import win32
    ok, res = await _run_sync(win32.window_list)
    if ok:
        titles = [
            line for line in (res or "").splitlines()
            if any(browser in line.lower() for browser in ("chrome", "chromium", "firefox", "edge", "brave"))
        ]
        if titles:
            title = titles[0].split(" ")[0]
            await _run_sync(win32.window_activate, title)
            await _run_sync(win32.key, "ctrl+l")
            await _run_sync(win32.type_text, target)
            await _run_sync(win32.key, "enter")
            return f"navigated the existing {title} window"
    open_default(target)
    return "no open browser window was found; opened the URL with the default browser"


async def _browser_action_windows(action: str, x: int, y: int, text: str, key: str, amount: int) -> str:
    from . import win32
    action = action.strip().lower()
    if action == "click":
        if x < 0 or y < 0:
            return "click requires non-negative x and y coordinates"
        ok, msg = await _run_sync(win32.click, x, y, 1, "1", 50)
        return msg if ok else "browser action failed: " + msg
    if action == "type":
        if not text:
            return "type requires text"
        ok, msg = await _run_sync(win32.type_text, text[:4000])
        return msg if ok else "browser action failed: " + msg
    if action == "key":
        if not key:
            return "key requires a key name"
        ok, msg = await _run_sync(win32.key, key)
        return msg if ok else "browser action failed: " + msg
    if action == "scroll":
        ok, msg = await _run_sync(win32.scroll, amount, 0)
        return msg if ok else "browser action failed: " + msg
    return "browser_action supports click, type, key, or scroll"
