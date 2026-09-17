import asyncio
import inspect
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

log = logging.getLogger("aurora.tools")

# Substrings that mark a tool's string result as a failure even though the
# tool returned without raising. Previously every non-exception result was
# treated as success, so offline relays, "could not find", and timeouts all
# read as green to the model and it confidently narrated actions that never
# ran. Tools may also raise, or return a (result, True) tuple that already
# marks the failure; the markers below are the safety net for string results.
_ERROR_MARKERS = (
    "error:",
    "could not",
    "couldn't",
    "failed",
    "failure",
    "is offline",
    "no action was executed",
    "did not report a result",
    "timed out",
    "unsupported",
    "unknown tool",
    "cannot find",
    "not installed",
    "not reachable",
    "not connected",
)


def looks_like_error(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in _ERROR_MARKERS)


@dataclass
class Tool:
    name: str
    description: str
    parameters: Dict[str, Any]
    fn: Callable[..., Any]
    timeout: int = 120

    def client_schema(self) -> Dict[str, Any]:
        props = {k: {kk: vv for kk, vv in v.items() if kk != "required"} for k, v in self.parameters.items()}
        required = [k for k, v in self.parameters.items() if v.get("required")]
        return {
            "type": "client",
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": props,
                **({"required": required} if required else {}),
            },
            "expects_response": True,
            "response_timeout_secs": min(120, max(1, self.timeout)),
        }


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: Dict[str, Tool] = {}

    def add(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"duplicate tool {tool.name}")
        self._tools[tool.name] = tool

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def get(self, name: str) -> Optional[Tool]:
        return self._tools.get(name)

    def names(self) -> List[str]:
        return sorted(self._tools)

    def client_schemas(self) -> List[Dict[str, Any]]:
        return [t.client_schema() for t in self._tools.values()]

    async def run(self, name: str, kwargs: Dict[str, Any]) -> Tuple[str, bool]:
        tool = self._tools.get(name)
        if tool is None:
            return f"Unknown tool '{name}'. Known tools: {', '.join(self.names())}.", True
        fargs = {k: v for k, v in kwargs.items() if k != "tool_call_id"}
        try:
            res = tool.fn(**fargs)
            if inspect.isawaitable(res):
                res = await asyncio.wait_for(res, timeout=tool.timeout)
            if isinstance(res, tuple) and len(res) == 2 and isinstance(res[0], str):
                res, is_error = res
                text = str(res)[:200_000]
                if is_error and not text.startswith("ERROR:"):
                    text = f"ERROR: {text}"
                return text, True if is_error else looks_like_error(text)
            if not isinstance(res, str):
                res = str(res)
            text = res[:200_000]
            if looks_like_error(text):
                return f"ERROR: {text}", True
            return text, False
        except asyncio.TimeoutError:
            return f"Tool {name} timed out after {tool.timeout}s.", True
        except Exception as e:  # noqa: BLE001
            log.exception("tool %s failed", name)
            return f"Tool {name} error: {e}", True


REGISTRY = ToolRegistry()


def tool(name: str, description: str, parameters: Optional[Dict[str, Any]] = None, timeout: int = 120) -> Callable:
    def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
        REGISTRY.add(Tool(name=name, description=description, parameters=parameters or {}, fn=fn, timeout=timeout))
        return fn

    return deco