import asyncio
import inspect
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

log = logging.getLogger("aurora.tools")


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
            if not isinstance(res, str):
                res = str(res)
            return res[:200_000], False
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