"""Scoped PropAI MCP bridge for Kim.

Kim receives a PropAI MCP connector token, never the Supabase service key. The
MCP server resolves the authenticated PropAI user, broker, and tenant scope.
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx

from .registry import tool

_KNOWN_TOOLS = {
    "market_search", "market_summary", "market_trends", "listing_get", "listing_similar",
    "listing_history", "listing_contact_broker", "requirement_search", "requirement_match",
    "broker_search", "broker_profile", "broker_activity", "broker_inventory", "building_search",
    "building_profile", "building_inventory", "location_search", "conversation_search",
    "conversation_timeline", "conversation_summarize", "intel_ask", "intel_explain",
    "intel_compare", "contact_search", "search", "fetch", "triage_hot_leads", "price_estimate",
    "building_intel", "save_listing", "create_requirement", "set_follow_up", "qualify_lead",
    "draft_broadcast", "match_requirement_to_broker", "pricing_negotiation_brief",
    "stale_lead_reactivation", "summarise_thread",
}


def _endpoint() -> str:
    return os.environ.get("PROPAI_MCP_URL", "https://mcp.propai.live/mcp").strip()


def _token() -> str:
    return os.environ.get("PROPAI_MCP_TOKEN", "").strip()


def _jsonrpc(response: httpx.Response) -> dict[str, Any]:
    if response.status_code >= 400:
        raise RuntimeError(f"PropAI MCP HTTP {response.status_code}: {response.text[:500]}")
    content_type = response.headers.get("content-type", "")
    if "json" in content_type:
        return response.json()
    for line in response.text.splitlines():
        if line.startswith("data:"):
            try:
                return json.loads(line[5:].strip())
            except json.JSONDecodeError:
                continue
    raise RuntimeError("PropAI MCP returned no JSON-RPC response")


def call_mcp(name: str, arguments: dict[str, Any]) -> str:
    token = _token()
    if not token:
        return "PropAI MCP is not connected. Configure PROPAI_MCP_TOKEN for Kim's scoped PropAI account."
    if name not in _KNOWN_TOOLS:
        return f"PropAI MCP tool is not allowed: {name}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        "MCP-Protocol-Version": "2025-06-18",
    }
    with httpx.Client(timeout=45, follow_redirects=True) as client:
        init = client.post(_endpoint(), headers=headers, json={
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "kim", "version": "0.1"}},
        })
        init_payload = _jsonrpc(init)
        if "error" in init_payload:
            raise RuntimeError(str(init_payload["error"]))
        session_id = init.headers.get("mcp-session-id")
        if session_id:
            headers["Mcp-Session-Id"] = session_id
        client.post(_endpoint(), headers=headers, json={"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
        result = client.post(_endpoint(), headers=headers, json={
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}},
        })
        payload = _jsonrpc(result)
    if "error" in payload:
        raise RuntimeError(str(payload["error"]))
    content = (payload.get("result") or {}).get("content") or []
    return "\n".join(str(item.get("text", item)) for item in content if isinstance(item, dict)) or json.dumps(payload.get("result"), ensure_ascii=False)


@tool(
    "propai_mcp",
    "Use PropAI's scoped MCP server for real-estate listings, requirements, brokers, buildings, conversations, market intelligence, and CRM actions. Choose a PropAI MCP tool and pass its JSON arguments. This uses the connected user's PropAI permissions, not raw database credentials.",
    {
        "tool_name": {"type": "string", "description": "PropAI MCP tool name, for example market_search, listing_get, broker_inventory, or building_profile", "required": True},
        "arguments": {"type": "object", "description": "Arguments required by the selected PropAI MCP tool", "required": False},
    },
    timeout=55,
)
def propai_mcp(tool_name: str, arguments: dict[str, Any] | None = None) -> str:
    return call_mcp(tool_name.strip(), arguments or {})
