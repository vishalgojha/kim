import re
from typing import Dict, List

BLOCKED_MSG = "This command is blocked by the safety policy and cannot be run. Tell the user why in plain language."
CONFIRM_MSG = (
    "This action needs explicit user confirmation. Ask the user for a clear verbal "
    "'yes', and only if they confirm, retry with confirm='yes'. Do not retry otherwise."
)


class PermissionDenied(Exception):
    pass


class Policy:
    def __init__(self, cfg: Dict):
        perms = cfg.get("permissions", {})
        self.default = perms.get("default", "allow")
        self.ask_for = [a.strip().lower() for a in perms.get("ask_for", [])]
        self.block_patterns = [re.compile(p) for p in perms.get("block_patterns", [])]
        self.timeout_secs = perms.get("timeout_secs", 60)
        self.max_output_chars = perms.get("max_output_chars", 6000)

    def check(self, command: str, confirm: bool = False) -> None:
        cmd = command.strip()
        lowered = cmd.lower()
        if not cmd:
            raise PermissionDenied("empty command")

        for pat in self.block_patterns:
            if pat.search(lowered):
                raise PermissionDenied(BLOCKED_MSG)

        for token in self.ask_for:
            if lowered.startswith(token):
                if not confirm:
                    raise PermissionDenied(CONFIRM_MSG)
                break

    def is_ask_for(self, command: str) -> bool:
        lowered = command.strip().lower()
        return any(lowered.startswith(t) for t in self.ask_for)

    def summarize(self) -> List[str]:
        out = [f"default: {self.default}"]
        if self.ask_for:
            out.append("needs-confirmation: " + ", ".join(self.ask_for))
        if self.block_patterns:
            out.append("blocked: " + ", ".join(p.pattern for p in self.block_patterns))
        return out