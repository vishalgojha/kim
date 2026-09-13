"""Small server-side adapter for Composio-connected accounts.

The cloud deployment uses Composio's REST API so it does not need local Google
OAuth files. Secrets are read only from environment variables.
"""

import json
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def enabled() -> bool:
    return bool(os.environ.get("COMPOSIO_API_KEY", "").strip())


def execute(slug: str, arguments: dict[str, Any], account_env: str) -> dict[str, Any]:
    api_key = os.environ.get("COMPOSIO_API_KEY", "").strip()
    account = os.environ.get(account_env, "").strip()
    if not api_key:
        raise RuntimeError("Composio is not configured; add COMPOSIO_API_KEY to the server")
    if not account:
        raise RuntimeError(f"Composio account is not configured; add {account_env} to the server")
    payload = json.dumps({
        "connected_account_id": account,
        "arguments": arguments,
        "version": os.environ.get(f"{account_env}_VERSION", "latest"),
    }).encode("utf-8")
    request = Request(
        f"https://backend.composio.dev/api/v3.1/tools/execute/{slug}",
        data=payload,
        headers={"x-api-key": api_key, "content-type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=45) as response:
            result = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"Composio {slug} failed ({exc.code}): {detail}") from exc
    except (URLError, TimeoutError) as exc:
        raise RuntimeError(f"Composio {slug} is unavailable: {exc}") from exc
    if isinstance(result, dict) and result.get("error"):
        raise RuntimeError(str(result["error"])[:500])
    return result if isinstance(result, dict) else {"data": result}


def text(result: dict[str, Any]) -> str:
    """Return a bounded, readable result without leaking the API key."""
    return json.dumps(result.get("data", result), ensure_ascii=False, indent=2, default=str)[:200_000]
