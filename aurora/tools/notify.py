from ..host import notify

from .registry import tool

_URGENCY = {"low": 0, "normal": 1, "critical": 2}


@tool(
    "desktop_notify",
    "Show a desktop notification bubble (does not interrupt by voice). Use for status updates the user might miss.",
    {
        "title": {"type": "string", "description": "short title", "required": True},
        "body": {"type": "string", "description": "message body", "required": True},
        "urgency": {"type": "string", "description": "low, normal, or critical", "required": False},
    },
    timeout=15,
)
def desktop_notify(title: str, body: str, urgency: str = "normal") -> str:
    return notify(title, body, (urgency or "normal").lower())
