"""Agent 角色权限系统。

每个 Agent 角色绑定一组路径规则：
  allow  前缀白名单（相对项目根，如 "scripts/"）
  deny   前缀黑名单（优先级高于 allow）
  read_only 角色只能读

QA Agent 只读；Art Agent 不碰 scripts/ 和 project.godot；
Code Agent 不碰 release/。规则与项目目录约定
（scripts/ scenes/ addons/ assets/ autoload/ project.godot）对齐。
"""

import fnmatch
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

# 项目关键文件的固定写法（非目录前缀）
_PROJECT_ROOT_FILES = {"project.godot", "export_presets.cfg", "icon.svg", "icon.svg.import"}


@dataclass(frozen=True)
class RolePolicy:
    """单个角色的路径权限。"""

    allow: tuple = ()  # 允许写的前缀/glob
    deny: tuple = ()  # 明确拒绝（覆盖 allow）
    read_only: bool = False

    def can_write(self, rel_path: str) -> bool:
        if self.read_only:
            return False
        rp = rel_path.replace("\\", "/").lstrip("/")
        if self._match(self.deny, rp):
            return False
        return bool(self.allow) and self._match(self.allow, rp)

    def can_read(self, rel_path: str) -> bool:
        # Phase 1 读不限制（审查/测试需要读全项目），仅记录语义
        return True

    @staticmethod
    def _match(patterns: tuple, rel_path: str) -> bool:
        for p in patterns:
            if rel_path == p or rel_path.startswith(p.rstrip("/") + "/"):
                return True
            if fnmatch.fnmatch(rel_path, p):
                return True
        return False


ROLES: Dict[str, RolePolicy] = {
    "code_agent": RolePolicy(
        allow=("scripts/", "scenes/", "autoload/", "*.gd", "*.tscn"),
        deny=("release/", "export/", "addons/gameforge/runtime/"),
    ),
    "asset_agent": RolePolicy(
        allow=("assets/", "textures/", "animations/", "*.png", "*.svg", "*.wav"),
        deny=("scripts/", "scenes/", "project.godot", "addons/"),
    ),
    "scene_agent": RolePolicy(
        allow=("scenes/", "*.tscn", "project.godot"),
        deny=("release/", "addons/gameforge/runtime/"),
    ),
    "qa_agent": RolePolicy(read_only=True),
    "repair_agent": RolePolicy(
        allow=("scripts/", "scenes/", "autoload/", "*.gd", "*.tscn"),
        deny=("release/", "export/"),
    ),
    "director": RolePolicy(  # 编排者全权（不直接改文件，只建沙箱）
        allow=("*",),
    ),
}


class PermissionDenied(PermissionError):
    """角色对目标路径无写权限。"""

    def __init__(self, role: str, rel_path: str):
        self.role = role
        self.rel_path = rel_path
        super().__init__(f"role '{role}' 无权写入 {rel_path}")


class RolePolicies:
    """按角色名查策略；未知角色默认只读（fail-closed）。"""

    @staticmethod
    def get(role: str) -> RolePolicy:
        return ROLES.get(role, RolePolicy(read_only=True))

    @staticmethod
    def resolve_role(agent_name: str) -> str:
        """把 Agent 类名/实例名映射到角色。

        约定：CodeGeneratorAgent → code_agent；无映射的未知 Agent → 只读。
        大小写/下划线不敏感（TestGeneratorAgent 与 test_generator 等价）。
        """
        name = (agent_name or "").lower().replace("_", "")
        mapping = {
            "codegenerator": "code_agent",
            "code": "code_agent",
            "asset": "asset_agent",
            "scenegenerator": "scene_agent",
            "scene": "scene_agent",
            "qa": "qa_agent",
            "testgenerator": "qa_agent",
            "reviewer": "qa_agent",
            "debugger": "repair_agent",
            "refactor": "repair_agent",
            "orchestrator": "director",
            "director": "director",
        }
        for key, role in mapping.items():
            if key in name:
                return role
        return "unknown"


def check_write(role: str, rel_path: str) -> None:
    """写权限检查，拒绝时抛 PermissionDenied。"""
    policy = RolePolicies.get(role)
    if not policy.can_write(rel_path):
        raise PermissionDenied(role, rel_path)


# 模块级别名：controller / __init__ 直接导入
resolve_role = RolePolicies.resolve_role


def check_read(role: str, rel_path: str) -> None:
    """读权限检查（Phase 1 全放行）。"""
    return None


def describe_role(role: str) -> Dict[str, object]:
    p = RolePolicies.get(role)
    return {
        "role": role,
        "read_only": p.read_only,
        "allow": list(p.allow),
        "deny": list(p.deny),
    }
