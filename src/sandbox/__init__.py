"""GameForge Sandbox Platform — AI 游戏生产安全执行平台（Phase 1）

定位：AI 自动游戏生产线的执行底座。
Agent 不直接操作项目文件，一切修改经 SandboxController 走
工作区隔离 → 权限检查 → 快照留痕 → 受限执行 → 测试后合并/回滚。

分层：
  policy/     Agent 角色权限（谁能改哪些路径）
  workspace/  任务级项目隔离（workspace.py）
  snapshot/   修改前快照与回滚（snapshot.py）
  runtime/    进程资源笼（Job Object / prlimit）
  monitor/    运行资源采样
  controller/ 统一入口（controller.py）

Phase 1 不含 Docker 后端（backend/local 即宿主受限执行）。
"""

from src.sandbox.controller import SandboxController
from src.sandbox.policy.permission import RolePolicies, check_write, check_read
from src.sandbox.snapshot import SnapshotManager
from src.sandbox.workspace import WorkspaceManager
from src.sandbox.runtime.process import RuntimePolicy, run_isolated

__all__ = [
    "SandboxController",
    "RolePolicies",
    "check_write",
    "check_read",
    "SnapshotManager",
    "WorkspaceManager",
    "RuntimePolicy",
    "run_isolated",
]
