"""Small, private, durable memory store for Kim.

SQLite keeps this useful on a laptop and in the Hetzner container without
requiring another database. Records are local to the Kim runtime and bounded.
"""

import os
import sqlite3
import time
from pathlib import Path

from .registry import tool


def _db() -> sqlite3.Connection:
    path = Path(os.environ.get("KIM_MEMORY_PATH", "~/.aurora/memory.db")).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE IF NOT EXISTS memories (id INTEGER PRIMARY KEY, text TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'fact', source TEXT NOT NULL DEFAULT 'user', created_at REAL NOT NULL)")
    conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(text, kind, source, content='memories', content_rowid='id')")
    conn.execute("CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN INSERT INTO memories_fts(rowid,text,kind,source) VALUES (new.id,new.text,new.kind,new.source); END")
    conn.commit()
    return conn


@tool(
    "memory_save",
    "Save a durable technical fact, preference, architecture decision, incident, or project note for Kim. Never save passwords, API keys, or tokens.",
    {
        "text": {"type": "string", "description": "fact or note to remember, maximum 2000 characters", "required": True},
        "kind": {"type": "string", "description": "fact, decision, incident, project, or preference", "required": False},
        "source": {"type": "string", "description": "where it came from, default user", "required": False},
    },
    timeout=15,
)
def memory_save(text: str, kind: str = "fact", source: str = "user") -> str:
    text = text.strip()
    if not text or len(text) > 2000:
        return "memory text must be 1-2000 characters"
    lowered = text.lower()
    if any(token in lowered for token in ("api key", "secret", "password", "token", "private key")):
        return "not saved: secrets must not be stored in memory"
    conn = _db()
    try:
        cur = conn.execute("INSERT INTO memories(text,kind,source,created_at) VALUES (?,?,?,?)", (text, kind[:40], source[:80], time.time()))
        conn.commit()
        return f"remembered note {cur.lastrowid}"
    finally:
        conn.close()


@tool(
    "memory_search",
    "Search Kim's durable technical memory for architecture decisions, incidents, projects, and preferences.",
    {"query": {"type": "string", "description": "keywords or natural-language search", "required": True}, "limit": {"type": "integer", "description": "maximum results, default 8", "required": False}},
    timeout=15,
)
def memory_search(query: str, limit: int = 8) -> str:
    query = " ".join(query.split())
    if not query:
        return "memory query is required"
    conn = _db()
    try:
        try:
            rows = conn.execute("SELECT m.text,m.kind,m.source FROM memories_fts f JOIN memories m ON m.id=f.rowid WHERE memories_fts MATCH ? ORDER BY m.created_at DESC LIMIT ?", (query.replace('"', ""), max(1, min(int(limit), 30)))).fetchall()
        except sqlite3.OperationalError:
            words = [word for word in query.split() if word]
            where = " OR ".join("text LIKE ?" for _ in words)
            rows = conn.execute(f"SELECT text,kind,source FROM memories WHERE {where} ORDER BY created_at DESC LIMIT ?", tuple(f"%{word}%" for word in words) + (max(1, min(int(limit), 30)),)).fetchall()
        return "\n".join(f"- [{kind}] {text} ({source})" for text, kind, source in rows) or "no matching memory"
    finally:
        conn.close()
