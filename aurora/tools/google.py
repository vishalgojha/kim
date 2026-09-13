"""Narrow Gmail and Google Calendar tools using local desktop OAuth."""

import base64
import json
import os
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any

from .registry import tool


ROOT = Path(__file__).resolve().parents[2]
CREDENTIALS = ROOT / "credentials.json"
TOKEN = Path.home() / ".kim" / "google_token.json"
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/calendar",
]


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
    if not creds and TOKEN.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(TOKEN), SCOPES)
        except Exception:
            creds = None
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    if not creds or not creds.valid:
        if not CREDENTIALS.exists():
            return None, f"Google setup required: download Desktop OAuth credentials to {CREDENTIALS}"
        TOKEN.parent.mkdir(parents=True, exist_ok=True)
        flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS), SCOPES)
        creds = flow.run_local_server(port=0)
        TOKEN.write_text(creds.to_json())

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
