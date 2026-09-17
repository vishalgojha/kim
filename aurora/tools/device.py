"""Route actions from a remote client to a connected physical device."""

from __future__ import annotations

from typing import Any

from .context import get_ctx
from .registry import tool


@tool(
    "device_command",
"Execute a physical action on the user's connected Linux laptop and wait for its result. Use this from a phone, web, or desktop chat to control the laptop: open_app (launch an app), open_url (open a link in the browser), browser_action (click/type/key/scroll the visible browser window), computer_action (computer-agent desktop control: see/ocr the screen, click_label, mouse move/click/drag/scroll, type, keys, window list/activate/move), type_text, press_key, screenshot, or playwright_run. Never claim the action succeeded unless the returned result confirms it; the laptop must be online for an action to run.",
    {
        "device_id": {"type": "string", "description": "target device, normally 'laptop'", "required": False},
        "action": {"type": "string", "description": "open_app, open_url, browser_action, computer_action, type_text, press_key, screenshot, or playwright_run", "required": True},
        "parameters": {"type": "object", "description": "Action parameters; for open_app use name, for open_url use url, for browser_action use action/x/y/text/key/amount, for computer_action use action/x/y/dx/dy/button/text/key/title/window/amount/width/height", "required": False},
        "name": {"type": "string", "description": "For open_app: the app to launch (Chrome, Firefox, files, editor...). Convenience alias for parameters.name.", "required": False},
        "url": {"type": "string", "description": "For open_url: the URL to open in the browser. Convenience alias for parameters.url.", "required": False},
        "app_name": {"type": "string", "description": "Alternate alias for name in open_app. Convenience alias for parameters.name.", "required": False},
    },
    timeout=40,
)
async def device_command(
    action: str,
    device_id: str = "laptop",
    parameters: dict[str, Any] | None = None,
    name: str | None = None,
    url: str | None = None,
    app_name: str | None = None,
) -> str:
    queue = get_ctx().get("queue_device_command")
    if queue is None:
        return "ERROR: no connected device bridge is running; no action was executed"
    params = dict(parameters or {})
    params.setdefault("name", name or params.get("name") or app_name or params.get("app_name") or params.get("package") or "")
    params.setdefault("url", url or params.get("url") or "")
    return await queue(action.strip().lower(), device_id.strip() or "laptop", params)
