"""Extract useful text from common documents, with optional richer parsers."""

import csv
import io
import json
from pathlib import Path

from .registry import tool


@tool(
    "document_extract",
    "Extract readable text from a PDF, DOCX, PPTX, CSV, JSON, Markdown, or text file for Kim to analyze. Uses optional MarkItDown or pypdf when installed.",
    {"path": {"type": "string", "description": "document path", "required": True}, "max_chars": {"type": "integer", "description": "maximum extracted characters, default 20000", "required": False}},
    timeout=60,
)
def document_extract(path: str, max_chars: int = 20000) -> str:
    p = Path(path).expanduser()
    if not p.exists() or not p.is_file():
        return f"document not found: {p}"
    cap = max(1000, min(int(max_chars), 100000))
    try:
        try:
            from markitdown import MarkItDown
            result = MarkItDown().convert(str(p)).text_content
            return result[:cap]
        except ImportError:
            pass
        if p.suffix.lower() == ".pdf":
            try:
                from pypdf import PdfReader
                result = "\n".join(page.extract_text() or "" for page in PdfReader(str(p)).pages)
            except ImportError:
                return "PDF extraction requires optional dependency pypdf or markitdown"
        elif p.suffix.lower() == ".json":
            result = json.dumps(json.loads(p.read_text(errors="replace")), indent=2, ensure_ascii=False)
        elif p.suffix.lower() == ".csv":
            rows = csv.reader(io.StringIO(p.read_text(errors="replace")))
            result = "\n".join(" | ".join(row) for row in rows)
        else:
            result = p.read_text(errors="replace")
        return result[:cap] + ("\n... (truncated)" if len(result) > cap else "")
    except Exception as exc:
        return f"document extraction failed: {exc}"
