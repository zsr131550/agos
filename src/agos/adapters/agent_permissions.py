"""Provider-specific argv for AGOS agent permission policy."""
from __future__ import annotations


def codex_permission_args(*, dangerously_bypass_permissions: bool) -> list[str]:
    """Return explicit Codex sandbox and non-interactive approval arguments."""

    if dangerously_bypass_permissions:
        return ["--dangerously-bypass-approvals-and-sandbox"]
    return ["--sandbox", "workspace-write", "-c", 'approval_policy="never"']


def claude_permission_args(*, dangerously_bypass_permissions: bool) -> list[str]:
    """Return explicit Claude Code safe-mode and approval arguments."""

    permission_mode = "bypassPermissions" if dangerously_bypass_permissions else "dontAsk"
    return ["--safe-mode", "--permission-mode", permission_mode]
