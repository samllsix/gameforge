"""GameForge - Multi-Agent工作流定义模块

基于LangGraph的状态图定义，管理游戏开发全流程。
专注于 Godot 引擎，生成 GDScript 代码和 .tscn 场景文件。
"""

import time as _time
import os

import asyncio
import json
import re
import structlog
from pathlib import Path

logger = structlog.get_logger()
from typing import Any, Dict, List, Optional
from langgraph.graph import StateGraph, END

from src.core.state.game_state import GameDevState, TaskStatus, TaskType, AgentType
from src.agents.orchestrator import OrchestratorAgent
from src.agents.planner import PlannerAgent
from src.agents.code_generator import CodeGeneratorAgent
from src.agents.code_reviewer import CodeReviewerAgent
from src.agents.test_generator import TestGeneratorAgent
from src.agents.debugger import DebuggerAgent
from src.agents.scene_generator import SceneGeneratorAgent
from src.agents.game_designer import GameDesignerAgent
from src.agents.refactor import RefactorAgent
from src.agents.main_reviewer import MainReviewerAgent
from src.core.memory import MemoryManager
from src.core.recipes import RecipeStore
from src.sandbox.controller import SandboxController


class GameDevWorkflow:
    """游戏开发工作流管理器

    核心改进：
    - 图驱动执行：.ainvoke() 替代手写循环，LangGraph 管理状态合并和路由
    - Reducer 自动合并：error_log/warnings 追加，code_generated 字典合并
    - 统一运行模式：run() 和 run_with_streaming() 共享同一张图
    - 专注于 Godot 引擎
    """

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        # 本类不继承 BaseAgent，但异常兜底路径按 BaseAgent 风格使用
        # self.logger / self.log_error / self.log_action，这里补齐（否则 AttributeError）
        self.logger = logger.bind(component="workflow")
        self.orchestrator = OrchestratorAgent(config)
        self.game_designer = GameDesignerAgent(config)
        self.planner = PlannerAgent(config)
        self.code_generator = CodeGeneratorAgent(config)
        self.code_reviewer = CodeReviewerAgent(config)
        self.test_generator = TestGeneratorAgent(config)
        self.debugger = DebuggerAgent(config)
        self.scene_generator = SceneGeneratorAgent(config)
        self.refactor_agent = RefactorAgent(config)
        self.main_reviewer = MainReviewerAgent(config)

        # 记忆系统 — 让 Agent 有上下文记忆
        self.memory = MemoryManager()

        # P1 语义级复用：已验证配方库。命中则整体复用，绕过 LLM 主流水线。
        self.recipe_store = RecipeStore()
        self.recipe_enabled = config.get("recipes", {}).get("enabled", True)

        # Sandbox 平台集成（Phase 1）
        self.sandbox = SandboxController(config)
        self.sandbox_enabled = config.get("sandbox", {}).get("enabled", False)

        self.graph = self._build_graph()

    # 实时预览：把"项目名"规整为后端 /api/v1/preview/frame 接受的 project_id
    # （与 src/api/main.py 的 _PREVIEW_PROJECT_RE 完全对齐）
    _PREVIEW_ID_RE = re.compile(r"^[A-Za-z0-9_\-\.]{1,64}$")

    def _resolve_preview_project_id(self, state: "GameDevState") -> str:
        """从 state 推出当前项目的 preview project_id。

        优先级：
        1. state["preview_project_id"]（已由外部注入）
        2. project_name（前端输入，做 sanitize）
        3. requirements 取首个稳定 token（兜底）

        失败时返回 ""，调用方据此判断是否带 project_id 字段。
        """
        existing = state.get("preview_project_id")
        if isinstance(existing, str) and self._PREVIEW_ID_RE.match(existing):
            return existing

        ctx = state.get("project_context", {}) or {}
        name = ctx.get("project_name") or ctx.get("requirements") or ""
        if not isinstance(name, str):
            return ""

        sanitized = re.sub(r"[^A-Za-z0-9_\-\.]", "_", name).strip("._-")[:64]
        if sanitized and self._PREVIEW_ID_RE.match(sanitized):
            return sanitized
        return ""

    def _build_graph(self):
        """构建LangGraph状态图，返回编译后的可执行图"""
        workflow = StateGraph(GameDevState)

        # 添加所有节点
        workflow.add_node("game_designer", self._game_designer_node)
        workflow.add_node("planner", self._planner_node)
        workflow.add_node("orchestrator", self._orchestrator_node)
        workflow.add_node("code_generator", self._wrap_code_node("code_generator"))
        workflow.add_node("code_reviewer", self._code_reviewer_node)
        workflow.add_node("refactor", self._wrap_code_node("refactor"))
        workflow.add_node("test_generator", self._wrap_code_node("test_generator"))
        workflow.add_node("debugger", self._wrap_code_node("debugger"))
        workflow.add_node("main_reviewer", self._main_reviewer_node)

        # 入口点
        workflow.set_entry_point("game_designer")

        # 固定边：线性流水线段
        workflow.add_edge("game_designer", "planner")
        workflow.add_edge("planner", "orchestrator")
        workflow.add_edge("code_generator", "code_reviewer")
        workflow.add_edge("code_reviewer", "refactor")
        workflow.add_edge("refactor", "test_generator")
        workflow.add_edge("test_generator", "main_reviewer")
        workflow.add_edge("main_reviewer", "orchestrator")
        workflow.add_edge("debugger", "orchestrator")

        # 条件边：orchestrator 根据当前状态路由到不同节点
        workflow.add_conditional_edges(
            "orchestrator",
            self._route_next,
            {
                "code_generator": "code_generator",
                "test_generator": "test_generator",
                "debugger": "debugger",
                END: END,
            },
        )

        return workflow.compile()

    # ── Sandbox 自动同步 ──

    def _wrap_code_node(self, node_name: str):
        """包装代码生成类节点，执行后自动同步新增/修改文件到沙箱工作区。"""
        real_method = getattr(self, f"_{node_name}_node")

        async def wrapper(state: GameDevState) -> Dict[str, Any]:
            result = await real_method(state)
            self._sandbox_sync_code_generated(state, result)
            return result

        return wrapper

    def _sandbox_sync_code_generated(self, state: GameDevState, node_result: Dict[str, Any]) -> None:
        """若启用沙箱，将 node_result 中 code_generated 的变更同步到任务工作区。"""
        if not getattr(self, "sandbox_enabled", False):
            return
        task = (state.get("sandbox") or {}).get("task")
        if not task:
            return
        new_files = node_result.get("code_generated") or {}
        if not new_files:
            return
        for rel_path, content in new_files.items():
            try:
                self.sandbox.modify(task, rel_path, content)
            except Exception as e:
                self.logger.warning("sandbox_sync_failed", path=rel_path, error=str(e))

    # ========== 节点实现 ==========

    async def _game_designer_node(self, state: GameDevState) -> Dict[str, Any]:
        """游戏设计节点 — 生成 Game Design Model"""
        try:
            result = await self.game_designer.execute(state)
            return {
                "game_design_model": result.get("game_design_model"),
                "current_phase": "design_complete",
            }
        except Exception as e:
            return {"error_log": [f"GameDesigner failed: {e}"], "current_phase": "error"}

    async def _planner_node(self, state: GameDevState) -> Dict[str, Any]:
        """规划节点 — 解析需求并生成任务计划"""
        try:
            plan_result = await self.planner.plan(state)
            is_dict = isinstance(plan_result, dict)
            task_plan = plan_result.get("tasks", []) if is_dict else plan_result
            asset_plan = plan_result.get("asset_plan", {}) if is_dict else {}
            genre_match = {
                "genre": plan_result.get("genre"),
                "representative": plan_result.get("representative"),
                "difficulty": plan_result.get("difficulty"),
            } if is_dict else None
            return {
                "task_plan": task_plan,
                "asset_plan": asset_plan,
                "genre_match": genre_match,
                "current_phase": "planning_complete",
            }
        except Exception as e:
            return {"error_log": [f"Planner failed: {e}"], "current_phase": "error"}

    async def _code_generator_node(self, state: GameDevState) -> Dict[str, Any]:
        """代码生成节点 — 根据当前任务生成 GDScript 代码

        只返回新生成的代码，code_generated 的合并由 reducer 自动完成。
        """
        try:
            current_task_id = state.get("current_task_id")
            task_plan = state.get("task_plan", [])

            current_task = None
            for task in task_plan:
                if task.get("id") == current_task_id:
                    current_task = task
                    break

            if not current_task:
                return {"current_phase": "no_task"}

            # 注入记忆上下文
            memory_context = self.memory.get_context_for_agent(
                "code_generator", query=current_task.get("name", "")
            )
            if memory_context:
                state = {**state, "_memory_context": memory_context}

            code_artifacts = await self.code_generator.generate(state, current_task)

            # 标记任务完成
            updated_plan = [dict(t) for t in task_plan]
            for task in updated_plan:
                if task.get("id") == current_task_id:
                    task["status"] = TaskStatus.COMPLETED.value
                    break

            # 只返回新代码，reducer 自动合并到 code_generated
            new_code = {art["file_path"]: art["content"] for art in code_artifacts}

            # 统一验证 — GDScript 语法检查
            warnings = []
            try:
                from src.utils.unified_validator import validate_all
                validation = validate_all(new_code)
                if validation.has_errors:
                    warnings.append(
                        f"代码验证发现 {len(validation.errors)} 个错误: "
                        + "; ".join(e.get("message", "") for e in validation.errors[:3])
                    )
            except Exception:
                pass

            return {
                "code_generated": new_code,
                "code_artifacts": code_artifacts,
                "task_plan": updated_plan,
                "current_phase": "code_generated",
                "warnings": warnings,
            }
        except Exception as e:
            return {"error_log": [f"Code generator failed: {e}"], "current_phase": "error"}

    async def _code_reviewer_node(self, state: GameDevState) -> Dict[str, Any]:
        """代码审查节点 — 审查生成的 GDScript 代码"""
        try:
            review_result = await self.code_reviewer.review(state)
            return {"current_phase": "code_reviewed", "review_result": review_result}
        except Exception as e:
            return {"error_log": [f"Code reviewer failed: {e}"], "current_phase": "error"}

    async def _refactor_node(self, state: GameDevState) -> Dict[str, Any]:
        """重构节点 — 分析代码质量并重构"""
        try:
            result = await self.refactor_agent.execute(state)

            # 重构后验证
            refactored_code = result.get("code_generated", {})
            if refactored_code:
                try:
                    from src.utils.unified_validator import validate_all
                    validation = validate_all(refactored_code)
                    if validation.has_errors:
                        original_code = state.get("code_generated", {})
                        result["code_generated"] = original_code
                        result.setdefault("warnings", []).append(
                            f"重构后验证失败（{len(validation.errors)}个错误），已回退: "
                            + "; ".join(e.get("message", "") for e in validation.errors[:3])
                        )
                except Exception:
                    pass

            return result
        except Exception as e:
            return {"error_log": [f"Refactor failed: {e}"], "current_phase": "error"}

    async def _test_generator_node(self, state: GameDevState) -> Dict[str, Any]:
        """测试生成节点 — 为 GDScript 代码生成 GUT 测试用例"""
        try:
            current_task_id = state.get("current_task_id")
            task_plan = state.get("task_plan", [])
            test_code = await self.test_generator.generate(state)

            updated_plan = [dict(t) for t in task_plan]
            for task in updated_plan:
                if task.get("id") == current_task_id:
                    task["status"] = TaskStatus.COMPLETED.value
                    break

            return {
                "code_generated": test_code,
                "task_plan": updated_plan,
                "current_phase": "test_generated",
            }
        except Exception as e:
            return {"error_log": [f"Test generator failed: {e}"], "current_phase": "error"}

    async def _orchestrator_node(self, state: GameDevState) -> Dict[str, Any]:
        """编排节点 — 调度下一个任务"""
        try:
            task_plan = state.get("task_plan", [])
            current_phase = state.get("current_phase", "")
            fix_attempts = state.get("fix_attempts", 0)
            max_fix = self.config.get("agents", {}).get("debugger", {}).get("max_fix_attempts", 5)

            # 有错误且未超过修复次数限制 → 路由到 debugger
            error_log = state.get("error_log", [])
            if error_log and current_phase != "fix_applied" and fix_attempts < max_fix:
                return {
                    "current_phase": "needs_fix",
                    "fix_attempts": fix_attempts + 1,
                }

            # 选择下一个待执行任务
            ready_tasks = self._get_all_ready_tasks(task_plan)
            if not ready_tasks:
                return {"current_phase": "workflow_complete", "is_complete": True}

            next_task = ready_tasks[0]
            return {
                "current_task_id": next_task.get("id"),
                "ready_task_ids": [t.get("id") for t in ready_tasks],
                "current_phase": "task_assigned",
            }
        except Exception as e:
            return {"error_log": [f"Orchestrator failed: {e}"], "current_phase": "error"}

    async def _main_reviewer_node(self, state: GameDevState) -> Dict[str, Any]:
        """主审查节点：最终代码二次审查 + 场景/人物/环境设计审查。"""
        try:
            return await self.main_reviewer.execute(state)
        except Exception as e:
            return {
                "main_review_result": {"passed": False, "error": str(e)},
                "design_review_result": {"passed": False, "warnings": [str(e)]},
                "error_log": [f"Main reviewer failed: {e}"],
                "current_phase": "main_review_error",
            }

    async def _debugger_node(self, state: GameDevState) -> Dict[str, Any]:
        """调试节点 — 分析 GDScript 错误并生成修复（委托 code_generator 修复能力）"""
        try:
            error_log = state.get("error_log", [])
            fix_result = await self.debugger.analyze_and_fix(state, error_log)
            return {
                **fix_result,
                "current_phase": "fix_applied",
            }
        except Exception as e:
            return {"error_log": [f"Debugger failed: {e}"], "current_phase": "unrecoverable"}

    # ========== 路由逻辑 ==========

    def _route_next(self, state: GameDevState) -> str:
        """条件路由：根据当前状态决定下一个节点"""
        current_phase = state.get("current_phase", "")

        # 工作流完成
        if current_phase == "workflow_complete" or state.get("is_complete"):
            return END

        # 需要修复 → debugger
        if current_phase == "needs_fix":
            return "debugger"

        # 任务分配 → 根据任务类型路由
        current_task_id = state.get("current_task_id")
        if not current_task_id:
            return END

        task_plan = state.get("task_plan", [])
        current_task = None
        for task in task_plan:
            if task.get("id") == current_task_id:
                current_task = task
                break

        if not current_task:
            return END

        task_type = current_task.get("type", "code")
        if task_type == TaskType.TEST.value:
            return "test_generator"
        elif task_type in self._NON_CODE_TASK_TYPES:
            return END
        else:
            return "code_generator"

    # ========== 工具方法 ==========

    def _sandbox_project_config(self, state: GameDevState) -> Optional[Dict[str, Any]]:
        """若 Sandbox 启用，返回指向任务工作区的 Godot 配置副本；否则返回 None。"""
        if not getattr(self, "sandbox_enabled", False):
            return None
        task = (state.get("sandbox") or {}).get("task")
        if not task:
            return None
        task_dir = task.get("task_dir")
        if not task_dir:
            return None
        cfg = dict(self.config)
        godot_cfg = dict(cfg.get("godot") or {})
        godot_cfg["project_path"] = task_dir
        cfg["godot"] = godot_cfg
        return cfg

    def log_action(self, action: str, details: Optional[Dict] = None) -> None:
        """记录操作日志（与 BaseAgent.log_action 语义一致）"""
        self.logger.info("agent_action", action=action, **(details or {}))

    def log_error(self, error: str, details: Optional[Dict] = None) -> None:
        """记录错误日志（与 BaseAgent.log_error 语义一致）"""
        safe_details = {(k if k != "error" else "detail_error"): v for k, v in (details or {}).items()}
        self.logger.error("agent_error", error_message=error, **safe_details)

    _NON_CODE_TASK_TYPES = {
        TaskType.SCENE.value,
        TaskType.DOCUMENTATION.value,
        TaskType.CONFIG.value,
        TaskType.UI.value,
        "scene", "documentation", "config", "ui",
    }

    def _is_task_completed(self, task_plan: List[Dict], task_id: str) -> bool:
        for task in task_plan:
            if task.get("id") == task_id:
                return task.get("status") == TaskStatus.COMPLETED.value
        return False

    def _get_all_ready_tasks(self, task_plan: List[Dict]) -> List[Dict]:
        """获取所有依赖已满足的待执行任务"""
        ready = []
        for task in task_plan:
            if task.get("status") != TaskStatus.PENDING.value:
                continue
            dependencies = task.get("dependencies", [])
            all_deps_met = all(
                self._is_task_completed(task_plan, dep_id)
                for dep_id in dependencies
            )
            if all_deps_met:
                ready.append(task)
        return ready

    # ========== 场景生成（与主图并行） ==========

    def _persist_scene_ir(self, state: GameDevState, scene_ir) -> None:
        """把工作流生成的 Scene IR 落盘到 projects/<pid>/.scene_ir.json。

        预览端点（/api/v1/preview/frame）优先读取该文件构建场景，
        替代旧的硬编码 default_scene_ir 抢跑（P0-1）。
        requirements 一并落盘，供美术指导器按需求关键词匹配主题包。
        """
        if scene_ir is None:
            return
        pid = self._resolve_preview_project_id(state)
        if not pid:
            return
        try:
            ir_data = scene_ir.model_dump() if hasattr(scene_ir, "model_dump") else dict(scene_ir)
            payload = {
                "project_id": pid,
                "requirements": (state.get("project_context", {}) or {}).get("requirements", ""),
                "scene_ir": ir_data,
            }
            proj_dir = os.path.join("projects", pid)
            os.makedirs(proj_dir, exist_ok=True)
            ir_path = os.path.join(proj_dir, ".scene_ir.json")
            with open(ir_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            self.log_action("scene_ir_persisted", {"path": ir_path, "genre": ir_data.get("genre")})
        except Exception as e:  # noqa: BLE001
            self.log_error("scene_ir_persist_failed", {"error": str(e)})

    async def _run_scene_generation(self, state: GameDevState, event_callback):
        """并行运行场景生成（与代码生成同时进行）"""
        await event_callback("scene_start", {"message": "正在生成 Godot 场景..."})
        try:
            result = await self.scene_generator.execute(state)
            status = result.get("scene_status", "error")
            state["scene_status"] = status
            state["scene_description"] = result.get("scene_description")
            state["scene_path"] = result.get("scene_path", "")
            state["scene_error"] = result.get("scene_error")

            # IR 落盘须先于 scene_complete 事件：前端收到 project_id 才开始轮询预览
            scene_ir_obj = result.get("scene_ir")
            if scene_ir_obj is not None:
                await asyncio.to_thread(self._persist_scene_ir, state, scene_ir_obj)

            if result.get("compile_errors"):
                state.setdefault("scene_compile_errors", []).extend(result["compile_errors"])

            scene_desc = result.get("scene_description")
            if scene_desc:
                scene_json = json.dumps(scene_desc, indent=2, ensure_ascii=False)
                state["code_generated"]["scenes/scene_description.json"] = scene_json
                # Sandbox：同步场景描述到任务工作区
                self._sandbox_sync_code_generated(state, {
                    "code_generated": {"scenes/scene_description.json": scene_json}
                })

            # 场景生成后立即执行人物、环境和玩法闭环审查。
            design_review = self.main_reviewer.review_game_design(state)
            state["design_review_result"] = design_review
            await event_callback("design_review", design_review)

            if status == "built":
                msg = "Godot 场景已生成！请在 Godot Editor 中查看"
                pid = self._resolve_preview_project_id(state)
                await event_callback("scene_complete", {
                    "message": msg,
                    "scene_path": result.get("scene_path", ""),
                    "object_count": len(scene_desc.get("game_objects", [])) if scene_desc else 0,
                    **({"project_id": pid} if pid else {}),
                })
            elif status == "skipped":
                await event_callback("scene_skipped", {
                    "message": result.get("message", "场景描述已生成，Godot 未构建"),
                    "reason": result.get("scene_skip_reason", "unknown"),
                })
            else:
                await event_callback("scene_error", {
                    "message": result.get("scene_error", "场景生成失败"),
                })
        except Exception as e:
            state["scene_status"] = "error"
            state["scene_error"] = str(e)
            await event_callback("scene_error", {"message": str(e)})

    # ========== Godot 编译闭环 ==========

    async def _godot_compile_loop(self, state: GameDevState, event_callback, max_rounds: int = 3):
        """Godot 编译闭环：导入 → 编译 → 读错误 → 自动修复 → 重编译"""
        from src.engine.godot.godot_http_client import GodotHTTPClient

        sandbox_cfg = self._sandbox_project_config(state)
        use_sandbox = sandbox_cfg is not None

        # —— 编译闭环模式路由 ——
        # auto    : 配置了 Godot 引擎路径则走 headless（无需编辑器 GUI），否则退回 8765 HTTP
        # headless: 始终走 headless（未配置引擎路径则报错，不退回）
        # http    : 始终走 8765 编辑器插件（需打开 Godot 并启用插件）
        compile_mode = self.config.get("godot", {}).get("compile_mode", "auto")
        if compile_mode in ("auto", "headless"):
            from src.engine.godot import GodotEditor
            editor = GodotEditor(sandbox_cfg or self.config)
            valid, _editor_msg = editor.validate()
            if valid:
                await self._godot_compile_loop_headless(state, event_callback, max_rounds, editor)
                return
            if compile_mode == "headless":
                await event_callback("compile_result", {
                    "status": "error",
                    "message": "未配置 Godot 引擎路径（设置 godot.editor_path 或环境变量 GODOT_EDITOR_PATH），无法使用 headless 编译",
                })
                return
            if use_sandbox:
                # 沙箱模式下不可退回 HTTP（会污染主线），直接跳过
                await event_callback("compile_result", {
                    "status": "skipped",
                    "message": "沙箱模式需要 headless Godot（设置 godot.editor_path），HTTP 模式会修改主线，已跳过",
                })
                return
        # 以下为原 8765 编辑器插件路径（仅非沙箱模式）

        await event_callback("phase_start", {"phase": "compiling", "message": "正在导入代码到 Godot..."})
        import_result = await client.import_files(gd_files)
        if import_result.get("status") == "error":
            await event_callback("compile_result", {
                "status": "error",
                "message": f"导入失败: {import_result.get('error', '')}",
            })
            return

        for round_num in range(max_rounds):
            await event_callback("phase_start", {
                "phase": "compiling",
                "message": f"正在编译 (第{round_num + 1}轮)...",
            })

            compile_result = await client.compile_scripts()
            errors = compile_result.get("errors", [])

            if not errors:
                await event_callback("compile_result", {
                    "status": "success",
                    "message": f"编译成功！共{len(gd_files)}个文件",
                    "round": round_num + 1,
                })
                return

            await event_callback("compile_result", {
                "status": "error",
                "message": f"编译发现{len(errors)}个错误",
                "errors": errors[:10],
                "round": round_num + 1,
            })

            await event_callback("phase_start", {
                "phase": "debugging",
                "message": f"正在自动修复编译错误 (第{round_num + 1}轮)...",
            })

            error_log = []
            for err in errors:
                if isinstance(err, dict):
                    line = err.get("line", "")
                    file = err.get("file", "")
                    msg = err.get("message", "")
                    error_log.append(f"{file}:{line}: error: {msg}")
                else:
                    error_log.append(str(err))

            state["error_log"] = error_log
            debug_result = await self._debugger_node(state)
            state.update(debug_result)

            updated_files = state.get("code_generated", {})
            updated_gd = {k: v for k, v in updated_files.items() if k.endswith(".gd")}
            if updated_gd != gd_files:
                gd_files = updated_gd
                await client.import_files(gd_files)

        await event_callback("compile_result", {
            "status": "partial",
            "message": f"经过{max_rounds}轮修复仍有编译错误，可能需要人工介入",
        })

    async def _godot_compile_loop_headless(
        self, state: GameDevState, event_callback, max_rounds: int, editor
    ) -> None:
        """Headless 编译闭环（无需 Godot 编辑器 GUI）

        流程：将 AI 生成的 .gd 写入项目磁盘 → 用 Godot 引擎 headless 校验 →
        有错则 debugger 修复并重写 → 重新校验，最多 max_rounds 轮。
        同时把场景描述构建为合法 .tscn 落盘，完成"实现游戏"的落盘环节。
        """
        import asyncio

        code_files = state.get("code_generated", {})
        gd_files = {k: v for k, v in code_files.items() if k.endswith(".gd")}
        if not gd_files:
            return

        # 1) 写入 GDScript 到项目磁盘
        await event_callback("phase_start", {
            "phase": "compiling", "message": "正在写入 GDScript 到 Godot 项目...",
        })
        # import_files 同步写盘，放入线程池避免阻塞事件循环
        import_result = await asyncio.to_thread(editor.import_files, gd_files)
        if import_result.get("status") == "error":
            await event_callback("compile_result", {
                "status": "error",
                "message": f"写入文件失败: {import_result.get('error', '')}",
            })
            return

        # 2) 场景描述 -> 合法 .tscn 落盘（绕开 8765 插件端的类型错配）
        scene_desc = state.get("scene_description")
        if not scene_desc:
            scene_json_str = code_files.get("scenes/scene_description.json")
            if isinstance(scene_json_str, str):
                try:
                    scene_desc = json.loads(scene_json_str)
                except Exception:
                    scene_desc = None
        if scene_desc and isinstance(scene_desc, dict):
            try:
                from src.engine.godot.scene_builder import GodotSceneBuilder
                tscn_text = GodotSceneBuilder(godot_version=4).build_tscn(scene_desc)
                name = scene_desc.get("scene_name", "GameScene")
                # import_files 同步写盘，放入线程池避免阻塞事件循环
                await asyncio.to_thread(
                    editor.import_files, {f"scenes/{name}.tscn": tscn_text}
                )
                state["scene_status"] = "success"
                state["scene_path"] = f"res://scenes/{name}.tscn"
                pid = self._resolve_preview_project_id(state)
                await event_callback("scene_complete", {
                    "scene_name": name,
                    "scene_path": state["scene_path"],
                    "object_count": len(scene_desc.get("game_objects", [])),
                    "compile_status": "headless",
                    **({"project_id": pid} if pid else {}),
                })
            except Exception as e:
                self.log_error("headless_scene_build_failed", {"error": str(e)})

        # 3) 校验闭环
        import os as _os
        _sandbox_cfg = self._sandbox_project_config(state)
        _proj = (_sandbox_cfg or self.config).get("godot", {}).get("project_path", "") or _os.getcwd()
        res_paths = []
        for k in gd_files.keys():
            clean = k.removeprefix("res://").removeprefix("res:/")
            disk = _os.path.join(_proj, clean)
            if _os.path.isfile(disk):
                res_paths.append("res://" + clean)
            else:
                self.log_action("check_scripts_skip_missing", {"path": clean})
        for round_num in range(max_rounds):
            await event_callback("phase_start", {
                "phase": "compiling",
                "message": f"正在校验 GDScript (第{round_num + 1}轮)...",
            })
            # 同步 subprocess 包在 to_thread 中执行，避免阻塞事件循环
            result = await asyncio.to_thread(editor.check_scripts, res_paths)
            errors = result.errors

            if not errors:
                await event_callback("compile_result", {
                    "status": "success",
                    "message": f"编译成功！共 {len(gd_files)} 个文件",
                    "round": round_num + 1,
                })
                return

            await event_callback("compile_result", {
                "status": "error",
                "message": f"编译发现 {len(errors)} 个错误",
                "errors": errors[:10],
                "round": round_num + 1,
            })

            await event_callback("phase_start", {
                "phase": "debugging",
                "message": f"正在自动修复编译错误 (第{round_num + 1}轮)...",
            })

            error_log = []
            for err in errors:
                if isinstance(err, dict):
                    error_log.append(
                        f"{err.get('file', '')}:{err.get('line', '')}: error: {err.get('message', '')}"
                    )
                else:
                    error_log.append(str(err))

            state["error_log"] = error_log
            debug_result = await self._debugger_node(state)
            state.update(debug_result)

            updated_files = state.get("code_generated", {})
            updated_gd = {k: v for k, v in updated_files.items() if k.endswith(".gd")}
            if updated_gd != gd_files:
                gd_files = updated_gd
                await asyncio.to_thread(editor.import_files, gd_files)
                res_paths = ["res://" + k for k in gd_files.keys()]

        await event_callback("compile_result", {
            "status": "partial",
            "message": f"经过 {max_rounds} 轮修复仍有编译错误，可能需要人工介入",
        })

    async def _runtime_smoke_test(
        self,
        state: GameDevState,
        event_callback,
        max_fix_attempts: int = 2,
    ) -> Dict[str, Any]:
        """P0-2 运行时冒烟测试。

        在语法编译（check_scripts）后，捕获运行时能否真正跑通。
        失败时把 errors 喂给 debugger（最多 max_fix_attempts 轮），
        成功时把 ``runnable=True`` 写回 state。
        """
        scene_path = state.get("scene_path", "")
        # 必须有场景文件才值得冒烟；否则保持原状跳过
        if not scene_path:
            return {
                "runnable": None,
                "runtime_smoke_errors": [],
                "runtime_smoke_skipped": True,
            }

        sandbox_cfg = self._sandbox_project_config(state)
        use_sandbox = sandbox_cfg is not None
        runtime_config = sandbox_cfg or self.config
        from src.engine.godot.runtime_smoke import GodotRuntimeSmoke
        smoke = GodotRuntimeSmoke(runtime_config)
        for attempt in range(max_fix_attempts + 1):
            await event_callback("phase_start", {
                "phase": "runtime_smoke",
                "message": f"运行时冒烟测试（第{attempt + 1}次）...",
            })
            # 落盘的 res:// 路径直接交给 Godot 4 CLI
            raw = await asyncio.to_thread(smoke.run_scene, scene_path)
            # 兼容 RuntimeSmokeResult 与 dict（测试 mock 用 dict）
            if hasattr(raw, "to_dict"):
                result = raw
                result_dict = raw.to_dict()
            else:
                result_dict = dict(raw)
                from src.engine.godot.runtime_smoke import RuntimeSmokeResult
                result = RuntimeSmokeResult(**{
                    k: v for k, v in result_dict.items()
                    if k in RuntimeSmokeResult.__dataclass_fields__
                })

            await event_callback("runtime_smoke_result", {
                "runnable": result.runnable,
                "errors": result.errors[:5],
                "scene_path": scene_path,
                "elapsed_seconds": result.elapsed_seconds,
                "attempt": attempt + 1,
            })

            if result.runnable:
                return {
                    "runnable": True,
                    "runtime_smoke_errors": [],
                    "runtime_smoke_result": result_dict,
                    "runtime_smoke_attempts": attempt + 1,
                }

            # 尝试喂给 debugger 修复
            if attempt >= max_fix_attempts:
                break

            # 构造 error_log 格式给 debugger
            error_log = []
            for err in result.errors[:5]:
                snippet = err.get("snippet", "")
                error_log.append(f"{scene_path}: error: {snippet[:200]}")
            if not error_log:
                break
            state["error_log"] = error_log
            state.setdefault("warnings", []).append(
                f"运行时冒烟失败（第{attempt + 1}次），进入 debugger 修复"
            )
            debug_result = await self._debugger_node(state)
            state.update(debug_result)
            # 重新落盘 debugger 修改的脚本
            try:
                from src.engine.godot import GodotEditor
                editor = GodotEditor(runtime_config)
                updated_gd = {
                    k: v for k, v in state.get("code_generated", {}).items()
                    if k.endswith(".gd")
                }
                if updated_gd:
                    await asyncio.to_thread(editor.import_files, updated_gd)
            except Exception as e:
                self.log_error("runtime_smoke_reimport_failed", {"error": str(e)})

        # 达到 max_fix_attempts 仍跑不通
        return {
            "runnable": False,
            "runtime_smoke_errors": result_dict.get("errors", []),
            "runtime_smoke_result": result_dict,
            "runtime_smoke_attempts": max_fix_attempts + 1,
        }

    async def _try_godot_pipeline(self, state: GameDevState, event_callback) -> None:
        """Godot 一键构建：导入代码 → 编译 → 构建场景

        路由逻辑同 :meth:`_godot_compile_loop`：

        - ``auto`` / ``headless`` 且配置了 Godot 引擎路径 → 走 headless
          （无需打开编辑器 GUI，直接调用 Godot 引擎二进制落盘 .gd/.tscn 并校验）
        - 否则退回 8765 HTTP 编辑器插件
        """
        sandbox_cfg = self._sandbox_project_config(state)
        use_sandbox = sandbox_cfg is not None

        # —— 一键构建模式路由 ——
        compile_mode = self.config.get("godot", {}).get("compile_mode", "auto")
        if compile_mode in ("auto", "headless"):
            from src.engine.godot import GodotEditor
            editor = GodotEditor(sandbox_cfg or self.config)
            valid, _editor_msg = editor.validate()
            if valid:
                await self._try_godot_pipeline_headless(state, event_callback, editor, use_sandbox=use_sandbox)
                return
            if compile_mode == "headless":
                await event_callback("scene_skipped", {
                    "reason": "godot_unavailable",
                    "message": "未配置 Godot 引擎路径（设置 godot.editor_path 或环境变量 GODOT_EDITOR_PATH），无法使用 headless 构建",
                })
                return
            if use_sandbox:
                # 沙箱模式下不可退回 HTTP（会污染主线），直接跳过
                await event_callback("scene_skipped", {
                    "reason": "sandbox_headless_unavailable",
                    "message": "沙箱模式需要 headless Godot（设置 godot.editor_path），HTTP 模式会修改主线，已跳过",
                })
                return

        from src.engine.godot.godot_http_client import GodotHTTPClient

        client = GodotHTTPClient()
        if not await client.check_health():
            await event_callback("scene_skipped", {
                "reason": "godot_http_unavailable",
                "message": "Godot Editor HTTP Server 未运行，跳过自动构建",
            })
            return

        code_files = state.get("code_generated", {})
        gd_files = {k: v for k, v in code_files.items() if k.endswith(".gd")}
        if not gd_files:
            return

        # 非沙箱模式：原 HTTP 编辑器导入
        await event_callback("phase_start", {"phase": "compiling", "message": "正在导入代码到 Godot..."})
        import_result = await client.import_files(gd_files)
        if import_result.get("status") == "error":
            await event_callback("compile_result", {
                "status": "error",
                "message": f"导入失败: {import_result.get('error', '')}",
            })
            return

        # 第二步：编译
        await event_callback("phase_start", {"phase": "compiling", "message": "正在编译..."})
        compile_result = await client.compile_scripts()
        errors = compile_result.get("errors", [])

        if errors:
            await event_callback("compile_result", {
                "status": "error",
                "message": f"编译发现 {len(errors)} 个错误",
                "errors": errors[:10],
            })

        # 第三步：构建场景
        scene_desc = state.get("scene_description")
        if not scene_desc:
            scene_json_str = code_files.get("scenes/scene_description.json")
            if scene_json_str:
                try:
                    scene_desc = json.loads(scene_json_str)
                except Exception:
                    pass

        if scene_desc:
            # 由 Python 侧构建合法 .tscn 文本（绕开插件端类型错配）
            tscn_text = None
            try:
                from src.engine.godot.scene_builder import GodotSceneBuilder
                tscn_text = GodotSceneBuilder(godot_version=4).build_tscn(scene_desc)
            except Exception:
                tscn_text = None  # 失败时回退到插件端构建
            await event_callback("scene_start", {"message": "正在构建 Godot 场景..."})
            scene_result = await client.send_scene(scene_desc, tscn_text=tscn_text)
            if scene_result.get("status") == "success":
                state["scene_status"] = "success"
                state["scene_path"] = scene_result.get("scene_path", "")
                pid = self._resolve_preview_project_id(state)
                await event_callback("scene_complete", {
                    "scene_name": scene_desc.get("scene_name", "GameScene"),
                    "scene_path": scene_result.get("scene_path", ""),
                    "object_count": len(scene_desc.get("game_objects", [])),
                    "compile_status": "success" if not errors else "with_errors",
                    **({"project_id": pid} if pid else {}),
                })
            else:
                state["scene_status"] = "error"
                await event_callback("scene_error", {
                    "message": scene_result.get("error", "场景构建失败"),
                })

        if not errors:
            await event_callback("compile_result", {
                "status": "success",
                "message": f"编译成功！共 {len(gd_files)} 个文件",
            })

    async def _try_godot_pipeline_headless(
        self, state: GameDevState, event_callback, editor, use_sandbox: bool = False
    ) -> None:
        """一键构建的 headless 实现（无需 Godot 编辑器 GUI）

        流程：把 AI 生成的 .gd 写入项目磁盘 → 由 Python 侧构建合法 .tscn 落盘
        → 用 Godot 引擎 headless 校验 GDScript，一次性报告结果（不做自动修复闭环）。
        """
        import asyncio

        code_files = state.get("code_generated", {})
        gd_files = {k: v for k, v in code_files.items() if k.endswith(".gd")}
        if not gd_files:
            return
        # 1) 写入 GDScript 到项目磁盘
        await event_callback("phase_start", {
            "phase": "compiling",
            "message": "正在导入代码到 Godot 项目...",
        })
        # import_files 同步写盘，放入线程池避免阻塞事件循环
        import_result = await asyncio.to_thread(editor.import_files, gd_files)
        if import_result.get("status") == "error":
            await event_callback("compile_result", {
                "status": "error",
                "message": f"写入文件失败: {import_result.get('error', '')}",
            })
            return

        # 2) 场景描述 -> 合法 .tscn 落盘（绕开 8765 插件端的类型错配）
        scene_desc = state.get("scene_description")
        if not scene_desc:
            scene_json_str = code_files.get("scenes/scene_description.json")
            if isinstance(scene_json_str, str):
                try:
                    scene_desc = json.loads(scene_json_str)
                except Exception:
                    scene_desc = None
        if scene_desc and isinstance(scene_desc, dict):
            try:
                from src.engine.godot.scene_builder import GodotSceneBuilder
                tscn_text = GodotSceneBuilder(godot_version=4).build_tscn(scene_desc)
                name = scene_desc.get("scene_name", "GameScene")
                # import_files 同步写盘，放入线程池避免阻塞事件循环
                await asyncio.to_thread(
                    editor.import_files, {f"scenes/{name}.tscn": tscn_text}
                )
                state["scene_status"] = "success"
                state["scene_path"] = f"res://scenes/{name}.tscn"
                pid = self._resolve_preview_project_id(state)
                await event_callback("scene_complete", {
                    "scene_name": name,
                    "scene_path": state["scene_path"],
                    "object_count": len(scene_desc.get("game_objects", [])),
                    "compile_status": "headless",
                    **({"project_id": pid} if pid else {}),
                })
            except Exception as e:
                self.log_error("headless_scene_build_failed", {"error": str(e)})

        # 3) headless 一次性校验（不做自动修复闭环）
        import os as _os2
        _proj2 = self.config.get("godot", {}).get("project_path", "") or _os2.getcwd()
        res_paths = []
        for k in gd_files.keys():
            clean = k.removeprefix("res://").removeprefix("res:/")
            disk = _os2.path.join(_proj2, clean)
            if _os2.path.isfile(disk):
                res_paths.append("res://" + clean)
            else:
                self.log_action("check_scripts_skip_missing", {"path": clean})
        await event_callback("phase_start", {
            "phase": "compiling",
            "message": "正在校验 GDScript...",
        })
        result = await asyncio.to_thread(editor.check_scripts, res_paths)
        errors = result.errors
        if errors:
            await event_callback("compile_result", {
                "status": "error",
                "message": f"编译发现 {len(errors)} 个错误",
                "errors": errors[:10],
            })
        else:
            await event_callback("compile_result", {
                "status": "success",
                "message": f"编译成功！共 {len(gd_files)} 个文件",
            })

    # ========== 后处理（共享逻辑） ==========

    def _sanitize_scene_scripts(self, state: GameDevState) -> None:
        """清理场景描述中引用的不存在脚本"""
        scene_desc = state.get("scene_description")
        if not scene_desc:
            return

        code_generated = state.get("code_generated", {})
        generated_classes = set()
        for fpath, content in code_generated.items():
            if fpath.endswith(".gd"):
                # 提取 class_name
                import re
                m = re.search(r'^class_name\s+(\w+)', content, re.MULTILINE)
                if m:
                    generated_classes.add(m.group(1))

        from src.core.tools import is_godot_builtin
        removed_scripts = []

        def _clean_components(components):
            cleaned = []
            for comp in components:
                comp_type = comp.get("type", "")
                if not comp_type:
                    cleaned.append(comp)
                elif is_godot_builtin(comp_type) or comp_type in generated_classes:
                    cleaned.append(comp)
                else:
                    removed_scripts.append(comp_type)
            return cleaned

        def _clean_object(obj):
            obj["components"] = _clean_components(obj.get("components", []))
            for child in obj.get("children", []):
                _clean_object(child)

        for obj in scene_desc.get("game_objects", []):
            _clean_object(obj)

        if removed_scripts:
            state.setdefault("warnings", []).append(
                f"场景描述中引用了未生成的脚本（已移除）: {', '.join(set(removed_scripts))}"
            )

    def _add_project_artifacts(self, state: GameDevState) -> None:
        """生成 Godot 项目产物"""
        code_generated = state.setdefault("code_generated", {})

        self._sanitize_scene_scripts(state)

        # README 和项目配置建议
        try:
            from src.engine.godot.project_generator import GodotProjectGenerator
            generator = GodotProjectGenerator()
            project_files = generator.generate_all(state)
            for path, content in project_files.items():
                if path not in code_generated:
                    code_generated[path] = content
        except Exception as e:
            state.setdefault("warnings", []).append(f"Godot 项目模板生成失败: {e}")

        # scene_description.json
        scene_desc = state.get("scene_description")
        if scene_desc:
            code_generated["scenes/scene_description.json"] = json.dumps(scene_desc, indent=2, ensure_ascii=False)

        # GameDesignModel.json
        if "data/GameDesignModel.json" not in code_generated:
            full_gdm = state.get("game_design_model") or {}
            full_gdm["_meta"] = {
                "project_name": state.get("project_context", {}).get("project_name", "GameForge"),
                "engine": "godot",
                "task_count": len(state.get("task_plan", [])),
                "scene_status": state.get("scene_status", "pending"),
                "generated_at": __import__("datetime").datetime.now().isoformat(),
            }
            code_generated["data/GameDesignModel.json"] = json.dumps(full_gdm, indent=2, ensure_ascii=False)

        # CodeMetadata.json
        if "data/CodeMetadata.json" not in code_generated:
            file_metadata = state.get("file_metadata", {})
            cm = {
                "files": list(code_generated.keys()),
                "file_metadata": file_metadata,
                "total_files": len(code_generated),
                "gd_files": len([f for f in code_generated if f.endswith(".gd")]),
            }
            code_generated["data/CodeMetadata.json"] = json.dumps(cm, indent=2, ensure_ascii=False)

    def _make_initial_state(self, input_state: Dict[str, Any]) -> GameDevState:
        """构建初始状态"""
        return {
            "task_plan": [],
            "current_task_id": None,
            "ready_task_ids": None,
            "genre_match": None,
            "code_generated": {},
            "code_artifacts": [],
            "test_results": None,
            "test_report": None,
            "fix_history": [],
            "fix_attempts": 0,
            "current_phase": "initialized",
            "is_complete": False,
            "requires_human_input": False,
            "project_context": input_state.get("project_context", {}),
            "sandbox": input_state.get("sandbox"),
            "error_log": [],
            "scene_description": None,
            "scene_status": "pending",
            "scene_error": None,
            "game_design_model": None,
            "file_metadata": {},
            "validation_result": None,
            "warnings": [],
            "message_bus": [],
        }

    async def _post_process(self, state: GameDevState, scene_task, event_callback=None):
        """后处理：等待场景、校验、生成产物、保存记忆"""
        # 等待场景生成完成
        try:
            await asyncio.wait_for(scene_task, timeout=60)
        except asyncio.TimeoutError:
            state["scene_status"] = "error"
            state["scene_error"] = "Scene generation timed out"
            state.setdefault("warnings", []).append("场景生成超时")
        except Exception as e:
            state["scene_status"] = "error"
            state["scene_error"] = str(e)
            state.setdefault("warnings", []).append(f"场景生成失败: {e}")

        self._sanitize_scene_scripts(state)

        # 统一验证
        try:
            from src.utils.unified_validator import validate_all
            validation = validate_all(
                code_files=state.get("code_generated", {}),
                scene_desc=state.get("scene_description"),
                gdm=state.get("game_design_model"),
                file_metadata=state.get("file_metadata"),
            )
            state["validation_result"] = validation.to_dict()
            if validation.has_errors:
                for err in validation.errors[:5]:
                    state.setdefault("warnings", []).append(f"验证错误: {err.get('message', '')}")
        except Exception as e:
            logger.warning("code_validation_failed", error=str(e))

        self._add_project_artifacts(state)
        # Sandbox：后处理生成的产物也同步到任务工作区
        self._sandbox_sync_code_generated(state, state)

        # 评测系统
        try:
            from src.eval.metrics import run_evaluation
            project_name = state.get("project_context", {}).get("project_name", "GameForge")
            eval_report = run_evaluation(
                project_name=project_name,
                code_files=state.get("code_generated", {}),
                tasks=state.get("task_plan", []),
                fix_history=state.get("fix_history", []),
            )

            # Godot 兼容性评分
            try:
                from src.utils.godot_compatibility_validator import validate_godot_compatibility
                compat = validate_godot_compatibility(state.get("code_generated", {}))
                compat_score = 100.0 if not compat.has_errors else max(0, 100 - len(compat.errors) * 10)
                eval_report.add_metric(
                    "godot_compatibility", compat_score,
                    details={"errors": len(compat.errors), "warnings": len(compat.warnings)}
                )
            except Exception as e:
                logger.warning("godot_compat_score_failed", error=str(e))

            report_path = eval_report.save()
            state["eval_report"] = eval_report.to_dict()
            state.setdefault("warnings", []).append(f"评测报告已保存: {report_path}")
        except Exception as e:
            state.setdefault("warnings", []).append(f"评测系统异常: {e}")

        # 保存项目记忆
        try:
            for error in state.get("error_log", []):
                self.memory.project_memory.add_error(
                    error_type="generation_error",
                    solution="自动修复或跳过",
                    context=error[:200],
                )
            for warning in state.get("warnings", []):
                self.memory.project_memory.add_learning(
                    topic="generation_warning",
                    content=warning[:200],
                )
            project_name = state.get("project_context", {}).get("project_name", "default")
            self.memory.project_memory.save(project_name)
        except Exception as e:
            logger.warning("memory_save_failed", error=str(e))

    # ========== P1 语义级复用（Recipe） ==========

    async def _run_recipe(self, state: GameDevState, event_callback) -> Optional[Dict[str, Any]]:
        """命中已验证配方时的快速路径。

        直接复用配方内容（GDM/任务/代码/场景），跳过整个 LLM 主流水线，
        只做后处理 + Godot 一键构建。返回 final state；未命中返回 None。
        """
        if not self.recipe_enabled:
            return None
        requirements = state.get("project_context", {}).get("requirements", "")
        # search 会同步读取全部配方 JSON（每个含完整代码文件），放入线程池避免阻塞事件循环
        recipe = await asyncio.to_thread(self.recipe_store.search, requirements)
        if not recipe:
            return None

        RecipeStore.apply_recipe(state, recipe)
        # Sandbox：配方命中后，将复用代码同步到任务工作区
        self._sandbox_sync_code_generated(state, state)

        async def _noop(event_type, data):
            pass

        cb = event_callback or _noop
        await cb("recipe_hit", {
            "message": "命中已验证配方，跳过 LLM 主流水线直接复用",
            "title": state.get("recipe_title", ""),
            "task_count": len(state.get("task_plan", [])),
            "file_count": len(state.get("code_generated", {})),
        })

        from src.utils.metrics import record_workflow_run, set_active_workflows
        _start = _time.time()
        set_active_workflows(1)
        try:
            scene_task = asyncio.create_task(asyncio.sleep(0))
            await self._post_process(state, scene_task, cb)
            if state.get("scene_status") in (None, "pending", "skipped"):
                await self._try_godot_pipeline(state, cb)
        except Exception as e:
            record_workflow_run(False, _time.time() - _start)
            state.setdefault("warnings", []).append(f"配方复用后处理失败: {e}")
        else:
            # 成功只在未异常时记录一次；放在 finally 会把失败也计成 success
            record_workflow_run(True, _time.time() - _start)
        finally:
            set_active_workflows(0)

        pid = self._resolve_preview_project_id(state)
        await cb("complete", {
            "phase": "complete",
            "message": "已复用已验证配方",
            "files": state.get("code_generated", {}),
            "task_count": len(state.get("task_plan", [])),
            "scene_status": state.get("scene_status", "success"),
            "scene_path": state.get("scene_path", ""),
            "runnable": True,
            "recipe_reused": True,
            "warnings": state.get("warnings", []),
            **({"project_id": pid} if pid else {}),
        })
        return state

    def _bake_if_verified(self, state: GameDevState) -> None:
        """真机冒烟通过后，把成功方案沉淀为配方（供后续复用）。"""
        if not self.recipe_enabled:
            return
        try:
            if self.recipe_store.save_recipe(state):
                self.logger.info("recipe_baked", title=state.get("recipe_title", ""))
        except Exception as e:
            self.logger.warning("recipe_bake_failed", error=str(e))

    # ========== 运行入口 ==========

    async def run(self, input_state: Dict[str, Any]) -> Dict[str, Any]:
        """运行工作流（批处理模式）"""
        from src.utils.metrics import (
            record_workflow_run, record_task_completed, record_file_generated,
            record_fix_attempt, set_active_workflows,
        )

        state = self._make_initial_state(input_state)
        _start = _time.time()
        set_active_workflows(1)

        # Sandbox：创建任务工作区
        sandbox_task = None
        if self.sandbox_enabled:
            try:
                project_id = self._resolve_preview_project_id(state) or "default"
                sandbox_task = self.sandbox.create(project_id, role="director")
                state.setdefault("sandbox", {})["task"] = sandbox_task
            except Exception as e:
                self.logger.warning("sandbox_create_failed", error=str(e))

        # P1 语义级复用：命中已验证配方 → 快速路径直接返回
        recipe_state = await self._run_recipe(state, None)
        if recipe_state is not None:
            if sandbox_task and self.sandbox_enabled:
                try:
                    self.sandbox.merge(sandbox_task)
                except Exception as e:
                    self.logger.warning("sandbox_merge_failed", error=str(e))
            return recipe_state

        # 加载项目记忆
        project_name = state.get("project_context", {}).get("project_name", "default")
        self.memory.project_memory.load(project_name)

        # 场景生成与主图并行
        async def _noop_callback(event_type, data):
            pass

        scene_task = asyncio.create_task(
            self._run_scene_generation(state, _noop_callback)
        )

        max_iterations = self.config.get("agents", {}).get("orchestrator", {}).get("max_iterations", 10)
        recursion_limit = max(max_iterations * 12, 100)
        _success = True

        try:
            result = await self.graph.ainvoke(state, config={"recursion_limit": recursion_limit})
            # 场景生成任务在 LangGraph 之外并行运行，写入的是 ainvoke 前的初始 dict，
            # 而 ainvoke 返回的是通道快照；把场景字段同步回来（与 run_with_streaming 一致），
            # 否则批处理模式下 scene_description/scene_status 丢失、场景被重复构建。
            for _key in (
                "scene_status", "scene_description", "scene_path",
                "scene_error", "scene_compile_errors",
            ):
                if _key in state:
                    result[_key] = state[_key]
            state = result

            for task in state.get("task_plan", []):
                if task.get("status") == TaskStatus.COMPLETED.value:
                    record_task_completed(task.get("type", "unknown"))

            for file_path in state.get("code_generated", {}).keys():
                ext = file_path.rsplit(".", 1)[-1] if "." in file_path else "unknown"
                record_file_generated(ext)

            for fix in state.get("fix_history", []):
                record_fix_attempt(fix.get("success", False))

        except Exception:
            _success = False
            # 主图已失败，取消仍在后台运行的场景生成任务，避免任务泄漏
            scene_task.cancel()
            if sandbox_task and self.sandbox_enabled:
                try:
                    self.sandbox.rollback(sandbox_task)
                except Exception as e:
                    self.logger.warning("sandbox_rollback_failed", error=str(e))
            raise
        finally:
            record_workflow_run(_success, _time.time() - _start)
            set_active_workflows(0)

        await self._post_process(state, scene_task)

        # Godot 一键构建 pipeline
        if state.get("scene_status") in (None, "pending", "skipped"):
            await self._try_godot_pipeline(state, lambda *a, **kw: asyncio.sleep(0))

        # Sandbox：成功则合并回主线
        if sandbox_task and self.sandbox_enabled:
            try:
                self.sandbox.merge(sandbox_task)
            except Exception as e:
                self.logger.warning("sandbox_merge_failed", error=str(e))

        return state

    async def run_with_streaming(
        self, input_state: Dict[str, Any], event_callback
    ) -> Dict[str, Any]:
        """运行工作流（流式模式）"""
        from src.utils.metrics import (
            record_workflow_run, record_task_completed, record_file_generated,
            record_fix_attempt, set_active_workflows,
        )

        state = self._make_initial_state(input_state)
        _start = _time.time()
        _success = True
        set_active_workflows(1)

        # Sandbox：创建任务工作区
        sandbox_task = None
        if self.sandbox_enabled:
            try:
                project_id = self._resolve_preview_project_id(state) or "default"
                sandbox_task = self.sandbox.create(project_id, role="director")
                state.setdefault("sandbox", {})["task"] = sandbox_task
            except Exception as e:
                self.logger.warning("sandbox_create_failed", error=str(e))

        # P1 语义级复用：命中已验证配方 → 快速路径直接返回
        recipe_state = await self._run_recipe(state, event_callback)
        if recipe_state is not None:
            if sandbox_task and self.sandbox_enabled:
                try:
                    self.sandbox.merge(sandbox_task)
                except Exception as e:
                    self.logger.warning("sandbox_merge_failed", error=str(e))
            return recipe_state

        project_name = state.get("project_context", {}).get("project_name", "default")
        self.memory.project_memory.load(project_name)

        scene_task = asyncio.create_task(
            self._run_scene_generation(state, event_callback)
        )

        max_iterations = self.config.get("agents", {}).get("orchestrator", {}).get("max_iterations", 10)
        recursion_limit = max(max_iterations * 12, 100)

        try:
            await event_callback("phase_start", {
                "phase": "iterating",
                "message": "正在执行工作流...",
            })

            final_state = None
            async for event in self.graph.astream_events(state, version="v2", config={"recursion_limit": recursion_limit}):
                kind = event.get("event", "")
                node_name = event.get("name", "")

                if kind == "on_chain_end" and node_name == "LangGraph":
                    output = event.get("data", {}).get("output", {})
                    if output and isinstance(output, dict):
                        final_state = output
                    continue

                if kind == "on_chain_start" and node_name not in ("__start__", "__end__", "LangGraph"):
                    await event_callback("phase_start", {
                        "phase": node_name,
                        "message": f"正在执行: {node_name}...",
                    })
                elif kind == "on_chain_end" and node_name not in ("__start__", "__end__", "LangGraph", "_route_next"):
                    output = event.get("data", {}).get("output", {})
                    if output and isinstance(output, dict):
                        # P1-3 契约：补发前端 handler 期待但后端从未发的事件
                        gdm = output.get("game_design_model")
                        if gdm and isinstance(gdm, dict) and node_name == "game_designer":
                            await event_callback("game_design", {
                                "game_title": gdm.get("game_title") or gdm.get("title") or "",
                                "genre": gdm.get("genre", ""),
                                "camera_mode": gdm.get("camera_mode") or gdm.get("camera", {}).get("type", ""),
                                "objectives": gdm.get("objectives", []),
                                "mechanics": gdm.get("mechanics", []),
                            })
                        new_plan = output.get("task_plan")
                        if new_plan and node_name == "planner":
                            await event_callback("task_plan", {
                                "tasks": new_plan,
                                "message": f"任务计划生成完成，共 {len(new_plan)} 项",
                            })
                        # 品类智能匹配结果：SSE 推给前端展示（基款/难度）
                        genre_match = output.get("genre_match")
                        if genre_match and node_name == "planner":
                            await event_callback("genre", {
                                "genre": genre_match.get("genre") or "",
                                "representative": genre_match.get("representative") or "",
                                "difficulty": genre_match.get("difficulty") or "medium",
                                "message": f"品类匹配: {genre_match.get('genre') or '通用'} · 难度 {genre_match.get('difficulty') or 'medium'}",
                            })

                        new_code = output.get("code_generated", {})
                        if new_code:
                            for file_path, content in new_code.items():
                                await event_callback("code_file", {
                                    "file_path": file_path,
                                    "content": content,
                                })

                        review = output.get("review_result")
                        if review:
                            await event_callback("review_result", review)

            if final_state is not None:
                # 场景生成任务在 LangGraph 之外并行运行，其写入的字段不在 final_state 中。
                # 注意 final_state 含初始值（scene_status="pending"/scene_description=None），
                # 故不能以 "键不存在" 作为合并条件，否则 scene_description 永远合并不进来，
                # 末尾 _try_godot_pipeline 拿不到 scene_description，无法构建 .tscn。
                # 图内节点不写 scene_* 字段，这里用场景任务的写入值覆盖是安全的。
                for _key in (
                    "scene_status", "scene_description", "scene_path",
                    "scene_error", "scene_compile_errors",
                ):
                    if _key in state:
                        final_state[_key] = state[_key]
                state = final_state

        except Exception as e:
            _success = False
            await event_callback("error", {"message": f"生成过程出错: {str(e)}"})
            if sandbox_task and self.sandbox_enabled:
                try:
                    self.sandbox.rollback(sandbox_task)
                except Exception as rollback_error:
                    self.logger.warning("sandbox_rollback_failed", error=str(rollback_error))

        for task in state.get("task_plan", []):
            if task.get("status") == TaskStatus.COMPLETED.value:
                record_task_completed(task.get("type", "unknown"))
        for file_path in state.get("code_generated", {}).keys():
            ext = file_path.rsplit(".", 1)[-1] if "." in file_path else "unknown"
            record_file_generated(ext)
        for fix in state.get("fix_history", []):
            record_fix_attempt(fix.get("success", False))
        record_workflow_run(_success, _time.time() - _start)
        set_active_workflows(0)

        await self._post_process(state, scene_task, event_callback)

        # Godot 一键构建 pipeline
        if state.get("scene_status") in (None, "pending", "skipped"):
            await self._try_godot_pipeline(state, event_callback)

        # 安全闸门：gd-guard 扫描生成脚本（危险 API 一票否决，Rust 二进制缺失则跳过）
        try:
            from src.engine.godot.gd_guard import scan_project

            project_hint = self._resolve_preview_project_id(state)
            if project_hint:
                scan_dir = os.path.join("projects", project_hint)
                if os.path.isdir(scan_dir):
                    guard = scan_project(scan_dir)
                    if guard["available"] and guard["verdict"] == "block":
                        findings = guard["findings"][:5]
                        state["warnings"] = list(state.get("warnings", [])) + [
                            f"gd-guard 拦截: {f.get('file','')}:{f.get('line','')} {f.get('detail','')}" for f in findings
                        ]
                        await event_callback("scene_error", {"message": "gd-guard 安全闸门拦截了危险脚本，已阻止运行/出包"})
                        state["runnable"] = False
                        return state
        except Exception:  # noqa: BLE001
            pass  # 闸门缺失/异常不阻塞主流程(失败开放)

        # P0-2 运行时冒烟测试（"可运行"闭环）
        smoke_summary = await self._runtime_smoke_test(state, event_callback)
        state["runnable"] = smoke_summary.get("runnable")

        # P1 语义级复用：真机冒烟通过 → 沉淀为已验证配方，供后续同类需求复用
        # （save_recipe 同步写完整代码 JSON，放入线程池避免阻塞事件循环）
        if state.get("runnable") is True:
            await asyncio.to_thread(self._bake_if_verified, state)

        # P2 稳定性指标：本次生成的修改次数（按品类/难度，量化多智能体协作的首过率）
        try:
            from src.utils.metrics import record_generation_stability

            genre_match = state.get("genre_match") or {}
            record_generation_stability(
                genre_match.get("genre") or "unknown",
                genre_match.get("difficulty") or "medium",
                int(state.get("fix_attempts", 0)),
            )
        except Exception:  # noqa: BLE001
            pass

        pid = self._resolve_preview_project_id(state)
        await event_callback("complete", {
            "phase": "complete",
            "message": "代码生成完成！",
            "files": state.get("code_generated", {}),
            "task_count": len(state.get("task_plan", [])),
            "fix_count": len(state.get("fix_history", [])),
            "scene_status": state.get("scene_status", "pending"),
            "scene_path": state.get("scene_path", ""),
            "runnable": state.get("runnable"),
            "runtime_smoke_errors": smoke_summary.get("runtime_smoke_errors", [])[:5],
            "runtime_smoke_skipped": smoke_summary.get("runtime_smoke_skipped", False),
            "warnings": state.get("warnings", []),
            **({"project_id": pid} if pid else {}),
        })

        # Sandbox：成功则合并回主线
        if sandbox_task and self.sandbox_enabled:
            try:
                self.sandbox.merge(sandbox_task)
            except Exception as e:
                self.logger.warning("sandbox_merge_failed", error=str(e))

        return state


def create_workflow(config: Dict[str, Any]) -> GameDevWorkflow:
    """创建工作流实例"""
    return GameDevWorkflow(config)
