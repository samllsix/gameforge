"""Agent 角色权限策略：不同 Agent 只能动自己职责内的路径。"""

from src.sandbox.policy.permission import (
    ROLES,
    RolePolicies,
    PermissionDenied,
    check_read,
    check_write,
    resolve_role,
)

__all__ = [
    "ROLES",
    "RolePolicies",
    "PermissionDenied",
    "check_read",
    "check_write",
    "resolve_role",
]
