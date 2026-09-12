"""Read-only adapter for lharries/whatsapp-mcp's local message database."""

import os
import sqlite3
from pathlib import Path

from .context import get_ctx
from .registry import tool


def _db_path() -> Path:
    configured = os.environ.get("WHATSAPP_DB_PATH", "").strip()
    if not configured:
        configured = str((get_ctx().get("cfg") or {}).get("whatsapp", {}).get("db_path", "")).strip()
    if configured:
        return Path(configured).expanduser()
    return Path.home() / "whatsapp-mcp" / "whatsapp-bridge" / "store" / "messages.db"


def _connect():
    path = _db_path()
    if not path.exists():
        return None, f"WhatsApp database not found at {path}. Run the linked WhatsApp bridge and set WHATSAPP_DB_PATH if needed."
    try:
        return sqlite3.connect(f"file:{path}?mode=ro", uri=True), None
    except sqlite3.Error as e:
        return None, f"could not open WhatsApp database read-only: {e}"


def _rows(sql: str, params: tuple = ()) -> str:
    conn, error = _connect()
    if error:
        return error
    try:
        cursor = conn.execute(sql, params)
        rows = cursor.fetchall()
        columns = [d[0] for d in cursor.description or []]
        return "\n".join(" | ".join(str(row[i] or "") for i in range(len(columns))) for row in rows) or "no WhatsApp messages found"
    except sqlite3.Error as e:
        return f"WhatsApp read failed: {e}"
    finally:
        conn.close()


@tool(
    "whatsapp_search",
    "Search personal WhatsApp messages read-only. Never sends or modifies WhatsApp data.",
    {
        "query": {"type": "string", "description": "text to search for in messages", "required": True},
        "chat": {"type": "string", "description": "optional chat name or JID filter", "required": False},
        "limit": {"type": "integer", "description": "maximum results, default 20", "required": False},
    },
    timeout=20,
)
def whatsapp_search(query: str, chat: str = "", limit: int = 20) -> str:
    clauses = ["LOWER(m.content) LIKE LOWER(?)"]
    params: list[object] = [f"%{query}%"]
    if chat:
        clauses.append("(LOWER(c.name) LIKE LOWER(?) OR LOWER(m.chat_jid) LIKE LOWER(?))")
        params.extend([f"%{chat}%", f"%{chat}%"])
    params.append(max(1, min(int(limit), 100)))
    return _rows(
        "SELECT m.timestamp, c.name, m.sender, m.content, m.is_from_me "
        "FROM messages m JOIN chats c ON c.jid=m.chat_jid "
        f"WHERE {' AND '.join(clauses)} ORDER BY m.timestamp DESC LIMIT ?",
        tuple(params),
    )


@tool(
    "whatsapp_recent",
    "Read the most recent personal WhatsApp messages, read-only.",
    {"limit": {"type": "integer", "description": "maximum results, default 20", "required": False}},
    timeout=20,
)
def whatsapp_recent(limit: int = 20) -> str:
    return _rows(
        "SELECT m.timestamp, c.name, m.sender, m.content, m.is_from_me "
        "FROM messages m JOIN chats c ON c.jid=m.chat_jid "
        "ORDER BY m.timestamp DESC LIMIT ?",
        (max(1, min(int(limit), 100)),),
    )


@tool(
    "whatsapp_chats",
    "List personal WhatsApp chats available in the local read-only message index.",
    {"query": {"type": "string", "description": "optional chat name or JID search", "required": False}},
    timeout=20,
)
def whatsapp_chats(query: str = "") -> str:
    if query:
        return _rows(
            "SELECT jid, name, last_message_time FROM chats WHERE LOWER(name) LIKE LOWER(?) OR LOWER(jid) LIKE LOWER(?) ORDER BY last_message_time DESC LIMIT 100",
            (f"%{query}%", f"%{query}%"),
        )
    return _rows("SELECT jid, name, last_message_time FROM chats ORDER BY last_message_time DESC LIMIT 100")
