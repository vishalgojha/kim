"""Narrow Gmail and Google Calendar tools using local desktop OAuth."""

import base64
import json
import os
import re
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any

from .registry import tool


ROOT = Path(__file__).resolve().parents[2]
CREDENTIALS = ROOT / "credentials.json"
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/calendar",
]


def _token_path() -> Path:
    return Path(os.environ.get("GOOGLE_TOKEN_PATH", str(Path.home() / ".kim" / "google_token.json"))).expanduser()


def _services():
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError:
        return None, "Google integration dependencies are missing; install requirements.txt"

    creds = None
    # A server deployment can use an already-authorized refresh token supplied
    # as a secret. Desktop OAuth remains the fallback for the laptop agent.
    token_json = os.environ.get("GOOGLE_TOKEN_JSON", "").strip()
    credentials_json = os.environ.get("GOOGLE_CREDENTIALS_JSON", "").strip()
    if token_json:
        try:
            creds = Credentials.from_authorized_user_info(json.loads(token_json), SCOPES)
        except (ValueError, json.JSONDecodeError):
            return None, "GOOGLE_TOKEN_JSON is not valid authorized-user JSON"
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    if not creds and credentials_json:
        try:
            json.loads(credentials_json)
        except (ValueError, json.JSONDecodeError):
            return None, "GOOGLE_CREDENTIALS_JSON is not valid OAuth client JSON"
        return None, "Google OAuth client is configured but GOOGLE_TOKEN_JSON is missing; authorize once on the laptop and upload the refresh-token JSON"
    token_path = _token_path()
    if not creds and token_path.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
        except Exception:
            creds = None
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    if not creds or not creds.valid:
        if not CREDENTIALS.exists():
            return None, f"Google setup required: download Desktop OAuth credentials to {CREDENTIALS}"
        token_path.parent.mkdir(parents=True, exist_ok=True)
        flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS), SCOPES)
        creds = flow.run_local_server(port=0)
        token_path.write_text(creds.to_json())

    return {
        "gmail": build("gmail", "v1", credentials=creds),
        "calendar": build("calendar", "v3", credentials=creds),
    }, None


@tool(
    "gmail_search",
    "Search Vishal's Gmail using Gmail search syntax, for example 'from:alice newer_than:7d'. Returns message ids and short subjects.",
    {
        "query": {"type": "string", "description": "Gmail search query", "required": True},
        "max_results": {"type": "integer", "description": "maximum messages to return, default 10", "required": False},
    },
    timeout=30,
)
def gmail_search(query: str, max_results: int = 10) -> str:
    services, error = _services()
    if error:
        return error
    result = services["gmail"].users().messages().list(
        userId="me", q=query, maxResults=max(1, min(int(max_results), 50))
    ).execute()
    rows = []
    for item in result.get("messages", []):
        msg = services["gmail"].users().messages().get(
            userId="me", id=item["id"], format="metadata", metadataHeaders=["Subject", "From", "Date"]
        ).execute()
        headers = {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}
        rows.append(f"{item['id']} | {headers.get('date', '')} | {headers.get('from', '')} | {headers.get('subject', '(no subject)')}")
    return "\n".join(rows) if rows else "no matching emails"


@tool(
    "gmail_read",
    "Read one Gmail message by id. Use gmail_search first to get the id.",
    {"message_id": {"type": "string", "description": "Gmail message id", "required": True}},
    timeout=30,
)
def gmail_read(message_id: str) -> str:
    services, error = _services()
    if error:
        return error
    msg = services["gmail"].users().messages().get(userId="me", id=message_id, format="full").execute()
    headers = {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}
    body = _body_text(msg.get("payload", {}))
    return f"From: {headers.get('from', '')}\nDate: {headers.get('date', '')}\nSubject: {headers.get('subject', '(no subject)')}\n\n{body[:12000]}"


@tool(
    "gmail_send",
    "Send an email from Vishal's Gmail. Always show the recipient, subject, and body and get explicit confirmation before calling with confirm='yes'.",
    {
        "to": {"type": "string", "description": "recipient email address", "required": True},
        "subject": {"type": "string", "description": "email subject", "required": True},
        "body": {"type": "string", "description": "plain-text email body", "required": True},
        "confirm": {"type": "string", "description": "must be yes after Vishal explicitly confirms sending", "required": False},
    },
    timeout=30,
)
def gmail_send(to: str, subject: str, body: str, confirm: str = "") -> str:
    if confirm.strip().lower() != "yes":
        return f"Sending is paused for confirmation. Draft recipient={to}, subject={subject!r}, body_length={len(body)}. Ask Vishal to confirm, then retry with confirm='yes'."
    services, error = _services()
    if error:
        return error
    message = MIMEText(body[:100_000], "plain", "utf-8")
    message["To"] = to
    message["Subject"] = subject[:998]
    encoded = base64.urlsafe_b64encode(message.as_bytes()).decode()
    sent = services["gmail"].users().messages().send(userId="me", body={"raw": encoded}).execute()
    return f"sent email to {to} (message id {sent.get('id', 'unknown')})"


def _body_text(part: dict[str, Any]) -> str:
    if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(part["body"]["data"] + "===").decode(errors="replace")
    return "\n".join(_body_text(p) for p in part.get("parts", []) if p)


@tool(
    "calendar_upcoming",
    "List upcoming Google Calendar events for the next N days.",
    {"days": {"type": "integer", "description": "days ahead, default 7", "required": False}},
    timeout=30,
)
def calendar_upcoming(days: int = 7) -> str:
    services, error = _services()
    if error:
        return error
    now = datetime.now(timezone.utc)
    events = services["calendar"].events().list(
        calendarId="primary",
        timeMin=now.isoformat(),
        timeMax=(now + timedelta(days=max(1, min(int(days), 90)))).isoformat(),
        singleEvents=True,
        orderBy="startTime",
        maxResults=100,
    ).execute().get("items", [])
    rows = []
    for event in events:
        start = event.get("start", {}).get("dateTime") or event.get("start", {}).get("date", "")
        rows.append(f"{start} | {event.get('summary', '(untitled)')} | {event.get('id', '')}")
    return "\n".join(rows) if rows else "no upcoming calendar events"


@tool(
    "calendar_create",
    "Create an event on Vishal's primary Google Calendar. Confirm the title/time with Vishal before calling this tool if anything is ambiguous.",
    {
        "title": {"type": "string", "description": "event title", "required": True},
        "start": {"type": "string", "description": "ISO datetime with timezone, e.g. 2026-09-15T10:00:00+05:30", "required": True},
        "end": {"type": "string", "description": "ISO datetime with timezone", "required": True},
        "description": {"type": "string", "description": "optional event details", "required": False},
    },
    timeout=30,
)
def calendar_create(title: str, start: str, end: str, description: str = "") -> str:
    services, error = _services()
    if error:
        return error
    event = services["calendar"].events().insert(
        calendarId="primary",
        body={"summary": title, "description": description, "start": {"dateTime": start}, "end": {"dateTime": end}},
    ).execute()
    return f"created calendar event: {event.get('summary', title)} ({event.get('htmlLink', '')})"


def _header(message: dict[str, Any], name: str) -> str:
    return next(
        (h.get("value", "") for h in message.get("payload", {}).get("headers", [])
         if h.get("name", "").lower() == name.lower()),
        "",
    )


def _email_addresses(value: str) -> list[str]:
    return [item.lower() for item in re.findall(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}", value, re.I)]


@tool(
    "gmail_today",
    "Give a compact daily briefing from today's Gmail messages and upcoming calendar events. Read-only.",
    {"max_emails": {"type": "integer", "description": "maximum email messages, default 20", "required": False}},
    timeout=45,
)
def gmail_today(max_emails: int = 20) -> str:
    services, error = _services()
    if error:
        return error
    gmail = services["gmail"]
    calendar = services["calendar"]
    day = datetime.now().astimezone().strftime("%Y/%m/%d")
    items = gmail.users().messages().list(userId="me", q=f"after:{day}", maxResults=max(1, min(int(max_emails), 50))).execute().get("messages", [])
    rows = ["TODAY'S EMAILS"]
    for item in items:
        msg = gmail.users().messages().get(userId="me", id=item["id"], format="metadata", metadataHeaders=["Subject", "From", "Date"]).execute()
        rows.append(f"- {_header(msg, 'subject') or '(no subject)'} | {_header(msg, 'from')} | {_header(msg, 'date')}")
    now = datetime.now(timezone.utc)
    events = calendar.events().list(
        calendarId="primary", timeMin=now.isoformat(), timeMax=(now + timedelta(days=1)).isoformat(),
        singleEvents=True, orderBy="startTime", maxResults=50,
    ).execute().get("items", [])
    rows.append("TODAY'S CALENDAR")
    for event in events:
        start = event.get("start", {}).get("dateTime") or event.get("start", {}).get("date", "")
        rows.append(f"- {start} | {event.get('summary', '(untitled)')}")
    return "\n".join(rows) if len(rows) > 2 else "nothing found for today"


@tool(
    "gmail_unanswered",
    "Find recent email threads where the latest message is from someone else and Vishal has not replied. Read-only.",
    {
        "days": {"type": "integer", "description": "look-back window, default 120 days", "required": False},
        "max_results": {"type": "integer", "description": "maximum threads, default 20", "required": False},
    },
    timeout=60,
)
def gmail_unanswered(days: int = 120, max_results: int = 20) -> str:
    services, error = _services()
    if error:
        return error
    gmail = services["gmail"]
    profile = gmail.users().getProfile(userId="me").execute()
    mine = str(profile.get("emailAddress", "")).lower()
    candidates = gmail.users().messages().list(
        userId="me", q=f"-from:me newer_than:{max(1, min(int(days), 365))}d",
        maxResults=max(1, min(int(max_results) * 4, 100)),
    ).execute().get("messages", [])
    seen: set[str] = set()
    rows = []
    for item in candidates:
        thread_id = item.get("threadId", "")
        if not thread_id or thread_id in seen:
            continue
        seen.add(thread_id)
        thread = gmail.users().threads().get(userId="me", id=thread_id, format="metadata", metadataHeaders=["Subject", "From", "Date"]).execute()
        messages = thread.get("messages", [])
        if not messages:
            continue
        latest = messages[-1]
        sender = _header(latest, "from")
        if mine and mine in _email_addresses(sender):
            continue
        rows.append(f"{thread_id} | {_header(latest, 'date')} | {sender} | {_header(latest, 'subject') or '(no subject)'}")
        if len(rows) >= max(1, min(int(max_results), 50)):
            break
    return "\n".join(rows) if rows else "no unanswered email threads found"


@tool(
    "gmail_contacts",
    "Extract the most frequent email contacts from recent Gmail headers. Read-only; no separate CRM database is created.",
    {
        "max_emails": {"type": "integer", "description": "messages to scan, default 200", "required": False},
        "max_contacts": {"type": "integer", "description": "contacts to return, default 25", "required": False},
    },
    timeout=60,
)
def gmail_contacts(max_emails: int = 200, max_contacts: int = 25) -> str:
    services, error = _services()
    if error:
        return error
    gmail = services["gmail"]
    mine = str(gmail.users().getProfile(userId="me").execute().get("emailAddress", "")).lower()
    messages = gmail.users().messages().list(userId="me", q="newer_than:365d", maxResults=max(1, min(int(max_emails), 500))).execute().get("messages", [])
    counts: dict[str, int] = {}
    for item in messages:
        msg = gmail.users().messages().get(userId="me", id=item["id"], format="metadata", metadataHeaders=["From", "To", "Cc"]).execute()
        for address in _email_addresses(" ".join(_header(msg, h) for h in ("from", "to", "cc"))):
            if address != mine:
                counts[address] = counts.get(address, 0) + 1
    rows = [f"{email} | {count} messages" for email, count in sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))[:max(1, min(int(max_contacts), 100))]]
    return "\n".join(rows) if rows else "no contacts found"
