"""Nango-backed connector adapter using Nango's authenticated API proxy."""

import base64
import json
import os
from email.mime.text import MIMEText
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from typing import Any


def enabled() -> bool:
    return bool(os.environ.get("NANGO_SECRET_KEY", "").strip())


def _config(account_env: str) -> tuple[str, str]:
    prefix = "GMAIL" if "GMAIL" in account_env else "CALENDAR"
    integration = os.environ.get(f"NANGO_{prefix}_INTEGRATION_ID", "").strip()
    connection = os.environ.get(f"NANGO_{prefix}_CONNECTION_ID", "").strip()
    if not integration or not connection:
        raise RuntimeError(f"Nango connector IDs are missing for {prefix.lower()}")
    return integration, connection


def _request(account_env: str, method: str, endpoint: str, query: dict[str, Any] | None = None, body: Any = None) -> dict[str, Any]:
    integration, connection = _config(account_env)
    base = os.environ.get("NANGO_BASE_URL", "https://api.nango.dev").rstrip("/")
    url = f"{base}/proxy/{endpoint.lstrip('/')}"
    if query:
        url += "?" + urlencode({key: value for key, value in query.items() if value is not None})
    headers = {"Authorization": f"Bearer {os.environ['NANGO_SECRET_KEY'].strip()}", "Provider-Config-Key": integration, "Connection-Id": connection, "Content-Type": "application/json"}
    request = Request(url, headers=headers, method=method, data=None if body is None else json.dumps(body).encode())
    try:
        with urlopen(request, timeout=45) as response:
            raw = response.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw else {}
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"Nango proxy failed ({exc.code}): {detail}") from exc
    except (URLError, TimeoutError) as exc:
        raise RuntimeError(f"Nango proxy is unavailable: {exc}") from exc


def execute(slug: str, arguments: dict[str, Any], account_env: str) -> dict[str, Any]:
    if slug == "GMAIL_FETCH_EMAILS":
        return _request(account_env, "GET", "/gmail/v1/users/me/messages", {"q": arguments.get("query", ""), "maxResults": arguments.get("max_results", 10), "userId": "me"})
    if slug == "GMAIL_SEND_EMAIL":
        message = MIMEText(str(arguments.get("body", "")), "plain", "utf-8")
        message["To"] = str(arguments.get("recipient_email", ""))
        message["Subject"] = str(arguments.get("subject", ""))[:998]
        return _request(account_env, "POST", "/gmail/v1/users/me/messages/send", body={"raw": base64.urlsafe_b64encode(message.as_bytes()).decode()})
    if slug == "GOOGLECALENDAR_FIND_FREE_SLOTS":
        return _request(account_env, "GET", "/calendar/v3/calendars/primary/events", {"timeMin": arguments.get("time_min"), "timeMax": arguments.get("time_max"), "singleEvents": "true", "orderBy": "startTime", "maxResults": 100})
    if slug == "GOOGLECALENDAR_CREATE_EVENT":
        return _request(account_env, "POST", "/calendar/v3/calendars/primary/events", body={"summary": arguments.get("summary", ""), "description": arguments.get("description", ""), "start": {"dateTime": arguments.get("start_datetime")}, "end": {"dateTime": arguments.get("end_datetime")}})
    raise RuntimeError(f"unsupported Nango connector operation: {slug}")


def text(result: dict[str, Any]) -> str:
    return json.dumps(result.get("data", result), ensure_ascii=False, indent=2, default=str)[:200_000]
