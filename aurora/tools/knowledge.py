"""Private knowledge spaces for Kim: ingest, index, and search sources."""

from __future__ import annotations

import hashlib
import os
import sqlite3
import time
from pathlib import Path
from typing import Iterable

from .documents import document_extract
from .registry import tool
from .web import web_fetch


def _db() -> sqlite3.Connection:
    path = Path(os.environ.get("KIM_KNOWLEDGE_PATH", "~/.aurora/knowledge.db")).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE IF NOT EXISTS sources (id TEXT PRIMARY KEY, space TEXT NOT NULL, locator TEXT NOT NULL, title TEXT NOT NULL, created_at REAL NOT NULL, content_hash TEXT NOT NULL)")
    db.execute("CREATE TABLE IF NOT EXISTS chunks (id INTEGER PRIMARY KEY, source_id TEXT NOT NULL, space TEXT NOT NULL, content TEXT NOT NULL, FOREIGN KEY(source_id) REFERENCES sources(id))")
    db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(content, space, source_id UNINDEXED, content='chunks', content_rowid='id')")
    db.execute("CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN INSERT INTO chunks_fts(rowid,content,space,source_id) VALUES(new.id,new.content,new.space,new.source_id); END")
    db.commit()
    return db


def _chunks(text: str, size: int = 1400, overlap: int = 180) -> Iterable[str]:
    text = " ".join(text.split())
    for start in range(0, len(text), max(1, size - overlap)):
        part = text[start : start + size].strip()
        if part:
            yield part
        if start + size >= len(text):
            break


@tool(
    "knowledge_ingest",
    "Add a local document or web URL to a named private knowledge space. Use before repeated research on a project or document set.",
    {"locator": {"type": "string", "description": "local file path or http(s) URL", "required": True}, "space": {"type": "string", "description": "knowledge space name, e.g. propai", "required": False}, "title": {"type": "string", "description": "human title, optional", "required": False}},
    timeout=90,
)
def knowledge_ingest(locator: str, space: str = "default", title: str = "") -> str:
    locator = locator.strip()
    space = "-".join(space.lower().split())[:80] or "default"
    if locator.startswith(("http://", "https://")):
        text = web_fetch(locator, max_chars=100_000)
    else:
        text = document_extract(locator, max_chars=100_000)
    if text.startswith(("document not found:", "document extraction failed:", "fetched ")) and len(text) < 300:
        return text
    source_id = hashlib.sha256(f"{space}\n{locator}".encode()).hexdigest()[:24]
    digest = hashlib.sha256(text.encode()).hexdigest()
    db = _db()
    try:
        db.execute("DELETE FROM chunks WHERE source_id=?", (source_id,))
        db.execute("INSERT OR REPLACE INTO sources(id,space,locator,title,created_at,content_hash) VALUES(?,?,?,?,?,?)", (source_id, space, locator, title.strip()[:200] or Path(locator).name or locator[:200], time.time(), digest))
        db.executemany("INSERT INTO chunks(source_id,space,content) VALUES(?,?,?)", ((source_id, space, chunk) for chunk in _chunks(text)))
        db.commit()
        count = db.execute("SELECT count(*) FROM chunks WHERE source_id=?", (source_id,)).fetchone()[0]
        return f"indexed {count} chunks in knowledge space '{space}' from {locator}"
    finally:
        db.close()


@tool(
    "knowledge_search",
    "Search Kim's private indexed knowledge spaces. Return relevant passages with source locators for grounded answers.",
    {"query": {"type": "string", "description": "keywords or natural-language query", "required": True}, "space": {"type": "string", "description": "optional space filter", "required": False}, "limit": {"type": "integer", "description": "maximum passages, default 6", "required": False}},
    timeout=20,
)
def knowledge_search(query: str, space: str = "", limit: int = 6) -> str:
    query = " ".join(query.split())
    if not query:
        return "knowledge query is required"
    db = _db()
    try:
        filt = "AND c.space=?" if space.strip() else ""
        args = [query.replace('"', "")]
        if space.strip():
            args.append(space.strip().lower())
        args.append(max(1, min(int(limit), 20)))
        rows = db.execute(f"SELECT c.content,s.title,s.locator FROM chunks_fts f JOIN chunks c ON c.id=f.rowid JOIN sources s ON s.id=c.source_id WHERE chunks_fts MATCH ? {filt} ORDER BY rank LIMIT ?", tuple(args)).fetchall()
        if not rows:
            return "no matching passages in Kim's knowledge spaces"
        return "\n\n".join(f"[{i}] {title} — {locator}\n{content}" for i, (content, title, locator) in enumerate(rows, 1))
    except sqlite3.OperationalError:
        return "no matching passages in Kim's knowledge spaces"
    finally:
        db.close()


@tool("knowledge_sources", "List private knowledge spaces and indexed sources available to Kim.", {"space": {"type": "string", "description": "optional space filter", "required": False}}, timeout=15)
def knowledge_sources(space: str = "") -> str:
    db = _db()
    try:
        if space.strip():
            rows = db.execute("SELECT space,title,locator FROM sources WHERE space=? ORDER BY created_at DESC", (space.strip().lower(),)).fetchall()
        else:
            rows = db.execute("SELECT space,title,locator FROM sources ORDER BY created_at DESC").fetchall()
        return "\n".join(f"- [{item_space}] {title} — {locator}" for item_space, title, locator in rows) or "no knowledge sources indexed"
    finally:
        db.close()
