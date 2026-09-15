"""Route actions from a remote client to a connected physical device."""

from __future__ import annotations

from typing import Any

from .context import get_ctx
from .registry import tool


@tool(
    "device_command",
    "Execute an action on a connected physical device and wait for its result. On Android, use this for the connected Linux laptop with device_id=laptop; never use launch_app for a laptop action requested from a phone.",
    {
        "action": {"type": "string", "description": "open_app, open_url, type_text, press_key, screenshot, or playwright_run", "required": True},
        "device_id": {"type": "string", "description": "Connected target device id; use laptop for the Linux desktop", "required": False},
        "parameters": {"type": "object", "description": "Action parameters, such as name for open_app or url for open_url", "required": False},
    },
    timeout=40,
)
async def device_command(action: str, device_id: str = "laptop", parameters: dict[str, Any] | None = None) -> str:
    queue = get_ctx().get("queue_device_command")
    if queue is None:
        return "No connected device bridge is running."
    return await queue(action.strip().lower(), device_id.strip() or "laptop", parameters or {})
