"""GameForge - Multi-Agent工作流定义模块

基于LangGraph的状态图定义，管理游戏开发全流程。
专注于 Godot 引擎，生成 GDScript 代码和 .tscn 场景文件。
"""

import asyncio
import json
import os
import re
import time as _time

import structlog

logger = structlog.get_logger()
from typing import Any, Dict, List, Optional, Tuple

from langgraph.graph import END, StateGraph

from src.agents.code_generator import CodeGeneratorAgent
from src.agents.game_designer import GameDesignerAgent
from src.agents.orchestrator import OrchestratorAgent
from src.agents.planner import PlannerAgent
from src.agents.requirement_analyzer import RequirementAnalyzerAgent
from src.agents.scene_generator import SceneGeneratorAgent
from src.core import paths
from src.core.memory import MemoryManager
from src.core.recipes import RecipeStore
from src.core.state.game_state import GameDevState, TaskStatus, TaskType
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
        self.scene_generator = SceneGeneratorAgent(config)
        self.requirement_analyzer = RequirementAnalyzerAgent(config)

        # 记忆系统 — 让 Agent 有上下文记忆
        self.memory = MemoryManager()

        # P1 语义级复用：已验证配方库。命中则整体复用，绕过 LLM 主流水线。
        self.recipe_store = RecipeStore()
        self.recipe_enabled = config.get("recipes", {}).get("enabled", True)

        # 增量生成：基于影响分析只重新生成变化的文件
        self.incremental_enabled = config.get("incremental", {}).get("enabled", False)
        self.incremental = None
        if self.incremental_enabled:
            from src.core.incremental import IncrementalGenerator
            self.incremental = IncrementalGenerator()

        # Sandbox 平台集成（Phase 1）
        self.sandbox = SandboxController(config)
        sandbox_cfg = config.get("sandbox", {})
        self.sandbox_enabled = sandbox_cfg.get("enabled", False)
        self.sandbox_auto_merge = sandbox_cfg.get("auto_merge", True)
        self.sandbox_auto_rollback = sandbox_cfg.get("auto_rollback", True)

        self.graph = self._build_graph()

    def _resolve_preview_project_id(self, state: "GameDevState") -> str:
        """从 state 推出当前项目的 preview project_id。

        实现已收口到 ``paths.resolve_project_id``（M6-06 单一事实源）：
        workflow / scene_generator / api 预览端点共用同一解析，
        不再有第二份 sanitize 规则。
        """
        return paths.resolve_project_id(state)

    def _resolve_preview_task_id(self, state: "GameDevState") -> Optional[str]:
        """若当前处于沙箱任务工作区，返回 task_id；否则返回 None。"""
        task = (state.get("sandbox") or {}).get("task")
        if not task:
            return None
        task_id = task.get("task_id")
        if isinstance(task_id, str) and task_id:
            return task_id
        return None

    def _preview_meta(self, state: "GameDevState") -> Dict[str, str]:
        """构造预览事件共用的 project_id/task_id 字典。"""
        pid = self._resolve_preview_project_id(state)
        tid = self._resolve_preview_task_id(state)
        out: Dict[str, str] = {}
        if pid:
            out["project_id"] = pid
        if tid:
            out["task_id"] = tid
        return out

    async def _gd_guard_scan(self, state: GameDevState, event_callback) -> bool:
        """安全闸门：gd-guard 扫描生成脚本。

        返回 True 表示被拦截（调用方应提前结束工作流），False 表示继续。
        """
        try:
            import src.engine.godot.gd_guard as _gd_guard

            task = (state.get("sandbox") or {}).get("task")
            scan_dir = None
            if task and task.get("task_dir"):
                scan_dir = task["task_dir"]
            else:
                project_hint = self._resolve_preview_project_id(state)
                if project_hint:
                    scan_dir = str(paths.project_dir(project_hint))
            if scan_dir and os.path.isdir(scan_dir):
                # 项目尚未落盘（无 project.godot 且无任何 .gd）时没有可扫对象，
                # 跳过本次扫描；导出阶段的发布门禁会对完整项目再做一次 gd-guard 扫描。
                # 否则会把"缺少 project.godot"误判为 block，提前终止整条工作流。
                has_project = os.path.isfile(os.path.join(scan_dir, "project.godot"))
                has_gd = False
                for _root, _dirs, files in os.walk(scan_dir):
                    if any(f.endswith(".gd") for f in files):
                        has_gd = True
                        break
                if not has_project and not has_gd:
                    logger.info("gd_guard.skipped_unmaterialized", scan_dir=scan_dir)
                    return False
                guard = _gd_guard.scan_project(scan_dir)
                if guard["available"] and guard["verdict"] == "block":
                    # 有界反馈回路：findings 喂给修复代理重生成一次 → 写盘 → 重扫；
                    # 仍拦截才终判失败（多智能体协作减少修改次数的核心闭环）
                    findings = guard["findings"][:5]
                    error_lines = [
                        f"{f.get('file', '')}:{f.get('line', '')} 使用了被禁止的 "
                        f"{f.get('rule', '')}（{f.get('detail', '')}），请改用节点/物理/Input 等"
                        f"允许的 API 重写该脚本"
                        for f in findings
                    ]
                    try:
                        fix_result = await self.code_generator.fix_code(state, error_lines)
                        new_code = fix_result.get("code_generated") or {}
                        for rel_path, content in new_code.items():
                            if not rel_path.endswith(".gd"):
                                continue
                            rel = rel_path
                            if rel.startswith("res://"):
                                rel = rel[len("res://"):]
                            disk = os.path.join(scan_dir, rel.replace("/", os.sep))
                            os.makedirs(os.path.dirname(disk) or scan_dir, exist_ok=True)
                            with open(disk, "w", encoding="utf-8") as f:
                                f.write(content)
                        state["warnings"] = list(state.get("warnings", [])) + [
                            "gd-guard 拦截后已自动重生成一轮，复检结果见后续扫描"
                        ]
                        guard = _gd_guard.scan_project(scan_dir)
                    except Exception as fix_err:  # noqa: BLE001
                        logger.warning("gd_guard.feedback_loop_failed", error=str(fix_err))
                    if guard["available"] and guard["verdict"] == "block":
                        findings = guard["findings"][:5]
                        state["warnings"] = list(state.get("warnings", [])) + [
                            f"gd-guard 重生成后仍拦截: {f.get('file','')}:{f.get('line','')} {f.get('detail','')}" for f in findings
                        ]
                        await event_callback("scene_error", {"message": "gd-guard 安全闸门拦截了危险脚本（重生成后仍未通过），已阻止运行/出包"})
                        state["runnable"] = False
                        return True
        except Exception:  # noqa: BLE001
            pass  # 闸门缺失/异常不阻塞主流程(失败开放)
        return False

    def _build_graph(self):
        """构建LangGraph状态图，返回编译后的可执行图"""
        workflow = StateGraph(GameDevState)

        # 添加所有节点（精简为 6 个核心节点）
        workflow.add_node("requirement_analyzer", self._requirement_analyzer_node)
        workflow.add_node("game_designer", self._game_designer_node)
        workflow.add_node("planner", self._planner_node)
        workflow.add_node("orchestrator", self._orchestrator_node)
        workflow.add_node("code_generator", self._code_generator_node)

        # 入口点：需求解析 → 游戏设计
        workflow.set_entry_point("requirement_analyzer")
        workflow.add_edge("requirement_analyzer", "game_designer")

        # 固定边：线性流水线段
        workflow.add_edge("game_designer", "planner")
        workflow.add_edge("planner", "orchestrator")
        workflow.add_edge("orchestrator", "code_generator")

        # 条件边：code_generator 完成后回到 orchestrator，orchestrator 决定继续还是结束
        workflow.add_conditional_edges(
            "code_generator",
            self._route_after_code_generator,
            {
                "continue": "orchestrator",
                END: END,
            },
        )

        return workflow.compile()

    # ── Sandbox 自动同步 ──

    def _wrap_code_node(self, node_name: str):
        """包装代码生成类节点，执行后自动同步新增/修改文件到沙箱工作区。"""
        real_method = getattr(self, f"_{node_name}_node")

        async def wrapper(state: GameDevState) -> Dict[str, Any]:
            """执行真实节点并同步产出文件到沙箱工作区。"""
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

    # ========== 增量生成 ==========

    def _apply_incremental_scope(self, state: GameDevState) -> None:
        """若启用增量生成且检测到 GDM 变更，过滤任务计划与代码生成范围。"""
        if not getattr(self, "incremental_enabled", False) or not self.incremental:
            return

        project_name = state.get("project_context", {}).get("project_name", "default")
        try:
            previous_context = self.memory.project_memory.get_relevant_context(project_name)
        except Exception:
            previous_context = {}

        old_gdm = (previous_context.get("game_design_model") or {}) if isinstance(previous_context, dict) else {}
        new_gdm = state.get("game_design_model") or {}

        if not old_gdm or old_gdm == new_gdm:
            return

        needed, changed = self.incremental.should_regenerate(
            {"game_design_model": old_gdm},
            {"game_design_model": new_gdm},
        )
        if not needed or not changed:
            return

        impacted = self.incremental.compute_impact(list(changed))
        state["incremental_scope"] = {
            "impacted_files": sorted(impacted),
            "changed_files": sorted(changed),
        }

        filtered_tasks = self.incremental.filter_task_plan(state.get("task_plan", []), impacted)
        if filtered_tasks:
            state["task_plan"] = filtered_tasks

        filtered_code = self.incremental.filter_code_generated(state.get("code_generated", {}), impacted)
        if filtered_code:
            state["code_generated"] = filtered_code

        self.logger.info(
            "incremental_scope_applied",
            changed=len(changed),
            impacted=len(impacted),
            tasks=len(filtered_tasks),
        )

    # ========== 节点实现 ==========

    async def _requirement_analyzer_node(self, state: GameDevState) -> Dict[str, Any]:
        """需求解析节点 — 将自然语言需求转化为结构化 Game Spec"""
        try:
            result = await self.requirement_analyzer.execute(state)
            spec = result.get("game_spec")
            if spec:
                self.log_action("requirement_analyzed", {"genre": spec.get("game", {}).get("genre", "")})
            return {
                "game_spec": spec,
                "current_phase": "requirement_analyzed",
            }
        except Exception as e:
            self.logger.warning("requirement_analyzer_failed", error=str(e))
            return {"current_phase": "requirement_analyzed"}

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
            self._apply_incremental_scope(state)
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
        """代码生成节点 — 执行完整 Pipeline：generate → review → refactor → test → fix → final_check

        内部由 CodeGeneratorAgent.run_pipeline() 协调各 Phase，
        只返回一个汇总结果，code_generated 的合并由 reducer 自动完成。
        """
        try:
            pipeline_result = await self.code_generator.run_pipeline(state)
            return pipeline_result
        except Exception as e:
            return {"error_log": [f"Code generator pipeline failed: {e}"], "current_phase": "error"}

    async def _orchestrator_node(self, state: GameDevState) -> Dict[str, Any]:
        """编排节点 — 调度下一个任务"""
        try:
            task_plan = state.get("task_plan", [])

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

    # ========== 路由逻辑 ==========

    def _route_after_code_generator(self, state: GameDevState) -> str:
        """code_generator 完成后的路由：继续下一个任务或结束。"""
        if state.get("is_complete"):
            return END
        return "continue"

    def _route_next(self, state: GameDevState) -> str:
        """条件路由：根据当前状态决定下一个节点"""
        if state.get("is_complete"):
            return END
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

    def _godot_project_config(self, state: GameDevState) -> Optional[Dict[str, Any]]:
        """Godot 流水线（写盘/编译校验/冒烟/playtest）应使用的配置。

        优先级：Sandbox 任务工作区 > projects/<project_id>/。

        非沙箱模式此前直接沿用全局 ``godot.project_path``（缺省取环境变量
        ``GODOT_PROJECT_PATH``），于是生成物会写到一个与项目无关的固定目录；
        指向仓库根时会**覆盖 scripts/、scenes/ 下的已跟踪文件**（实测发生过）。
        这与 paths 契约（生成项目位于 projects/<project_id>/）以及 eval 的
        project_completeness 指标（检查 projects/<pid>/ 下的 project.godot、
        *.tscn、*.gd）都矛盾——非沙箱模式那条指标因此永远不可能通过。
        Sandbox 模式本就按任务覆盖 project_path，这里把同一模式推广到主线。

        返回 None 表示无法确定目标（project_id 非法等），调用方退回 self.config。
        """
        sandbox_cfg = self._sandbox_project_config(state)
        if sandbox_cfg is not None:
            return sandbox_cfg

        pid = self._resolve_preview_project_id(state)
        if not pid:
            return None
        try:
            project_dir = paths.ensure_project_dir(pid)
        except (ValueError, OSError) as e:
            self.log_error("godot_project_dir_failed", {"error": str(e)})
            return None

        self._ensure_project_skeleton(project_dir, state)
        cfg = dict(self.config)
        godot_cfg = dict(cfg.get("godot") or {})
        godot_cfg["project_path"] = str(project_dir)
        cfg["godot"] = godot_cfg
        return cfg

    def _ensure_project_skeleton(self, project_dir, state: GameDevState) -> None:
        """确保目标目录内有 project.godot。

        ``GodotEditor.validate()`` 要求 project_path 下有 project.godot，而真正的
        写盘发生在 validate() 之后；缺这一步非沙箱模式会直接判"未配置项目"跳过。
        优先用已生成的内容，其次退回最小工程模板。
        """
        project_godot = project_dir / "project.godot"
        if project_godot.is_file():
            return
        content = (state.get("code_generated") or {}).get("project.godot")
        if not content:
            try:
                from src.engine.godot.scene_to_godot import _minimal_project_godot

                scene_desc = state.get("scene_description") or {}
                scene_name = scene_desc.get("scene_name") or "GameScene"
                content = _minimal_project_godot(scene_name)
            except Exception as e:  # noqa: BLE001
                self.log_error("project_skeleton_failed", {"error": str(e)})
                return
        try:
            project_godot.write_text(content, encoding="utf-8")
        except OSError as e:
            self.log_error("project_skeleton_write_failed", {"error": str(e)})

    def _project_materialized(self, state: GameDevState) -> bool:
        """目标项目目录里是否已有场景文件（判定是否需要真正落盘构建）。

        只读判断，不触发 _godot_project_config 的建目录副作用。
        """
        task = (state.get("sandbox") or {}).get("task")
        if task and task.get("task_dir"):
            base = task["task_dir"]
        else:
            pid = self._resolve_preview_project_id(state)
            if not pid:
                return False
            try:
                base = str(paths.project_dir(pid))
            except ValueError:
                return False
        scene_path = state.get("scene_path") or "res://scenes/main.tscn"
        rel = scene_path.removeprefix("res://").replace("/", os.sep)
        return os.path.isfile(os.path.join(base, rel))

    def _sandbox_cleanup_if_needed(self, project_id: str) -> None:
        """当项目沙箱任务数超过阈值时，自动清理最旧的任务。"""
        if not getattr(self, "sandbox_enabled", False) or not getattr(self, "sandbox", None):
            return
        try:
            tasks = self.sandbox.workspace.list_tasks(project_id)
            threshold = int((self.config.get("sandbox") or {}).get("cleanup_threshold", 20))
            if len(tasks) <= threshold:
                return
            keep_last = int((self.config.get("sandbox") or {}).get("cleanup_keep_last", 10))
            max_age_hours = int((self.config.get("sandbox") or {}).get("cleanup_max_age_hours", 168))
            result = self.sandbox.cleanup(project_id, keep_last=keep_last, max_age_hours=max_age_hours)
            if result.get("removed"):
                self.log_action("sandbox_auto_cleanup", result)
        except Exception as e:  # noqa: BLE001
            self.logger.warning("sandbox_cleanup_failed", error=str(e))

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
        """把工作流生成的 Scene IR 落盘到 projects/<pid>/.scene_ir.json 或沙箱任务工作区。

        预览端点（/api/v1/preview/frame?task_id=...）优先读取该文件构建场景，
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
            # 沙箱优先：写入任务工作区，便于预览端点按 task_id 直接读取
            task = (state.get("sandbox") or {}).get("task")
            if task and task.get("task_dir"):
                ir_path = os.path.join(task["task_dir"], ".scene_ir.json")
            else:
                proj_dir = paths.ensure_project_dir(pid)
                ir_path = str(paths.scene_ir_path(pid))
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
                # 存进 state 供配方沉淀（save_recipe）复用：配方命中时不跑场景生成，
                # 若配方不带 IR，预览端点会回退到通用 platformer 主题（品类错配）
                state["scene_ir"] = (
                    scene_ir_obj.model_dump()
                    if hasattr(scene_ir_obj, "model_dump")
                    else scene_ir_obj
                )
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

            if status == "built":
                msg = "Godot 场景已生成！请在 Godot Editor 中查看"
                await event_callback("scene_complete", {
                    "message": msg,
                    "scene_path": result.get("scene_path", ""),
                    "object_count": len(scene_desc.get("game_objects", [])) if scene_desc else 0,
                    **self._preview_meta(state),
                })
            elif status == "skipped":
                await event_callback("scene_skipped", {
                    "message": result.get("message", "场景描述已生成，Godot 未构建"),
                    "reason": result.get("scene_skip_reason", "unknown"),
                    **self._preview_meta(state),
                })
            else:
                await event_callback("scene_error", {
                    "message": result.get("scene_error", "场景生成失败"),
                    **self._preview_meta(state),
                })
        except Exception as e:
            state["scene_status"] = "error"
            state["scene_error"] = str(e)
            await event_callback("scene_error", {"message": str(e)})

    # ========== Godot 编译闭环 ==========

    async def _godot_compile_loop(self, state: GameDevState, event_callback, max_rounds: int = 3):
        """Godot 编译闭环：导入 → 编译 → 读错误 → 自动修复 → 重编译"""

        sandbox_cfg = self._sandbox_project_config(state)
        use_sandbox = sandbox_cfg is not None
        project_cfg = self._godot_project_config(state) or self.config

        # —— 编译闭环模式路由 ——
        # auto    : 配置了 Godot 引擎路径则走 headless（无需编辑器 GUI），否则退回 8765 HTTP
        # headless: 始终走 headless（未配置引擎路径则报错，不退回）
        # http    : 始终走 8765 编辑器插件（需打开 Godot 并启用插件）
        compile_mode = self.config.get("godot", {}).get("compile_mode", "auto")
        if compile_mode in ("auto", "headless"):
            from src.engine.godot import GodotEditor
            editor = GodotEditor(project_cfg)
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
            debug_result = await self.code_generator.fix_code(state, error_log)
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
                # 固定写 scenes/main.tscn：project.godot 的 run/main_scene、
                # 预览端 _need_build、基线检查与导出端点都只认这个路径
                # （与 scene_to_godot.write_project 的约定一致）
                # import_files 同步写盘，放入线程池避免阻塞事件循环
                await asyncio.to_thread(
                    editor.import_files, {"scenes/main.tscn": tscn_text}
                )
                state["scene_status"] = "success"
                state["scene_path"] = "res://scenes/main.tscn"
                await event_callback("scene_complete", {
                    "scene_name": name,
                    "scene_path": state["scene_path"],
                    "object_count": len(scene_desc.get("game_objects", [])),
                    "compile_status": "headless",
                    **self._preview_meta(state),
                })
            except Exception as e:
                self.log_error("headless_scene_build_failed", {"error": str(e)})

        # 3) 校验闭环
        import os as _os
        _proj = (self._godot_project_config(state) or self.config).get("godot", {}).get(
            "project_path", ""
        ) or _os.getcwd()
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
            debug_result = await self.code_generator.fix_code(state, error_log)
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

        runtime_config = self._godot_project_config(state) or self.config
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
                f"运行时冒烟失败（第{attempt + 1}次），进入 code_generator 修复"
            )
            debug_result = await self.code_generator.fix_code(state, error_log)
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

    # ── P1 playtest：输入回放 + 帧证据 ──────────────────────────────────

    async def _playtest(self, state: GameDevState, event_callback) -> Optional[Dict[str, Any]]:
        """P1 playtest：真正玩游戏——验证"能玩"而非仅"能启动"。

        "能启动"不算验证——用声明式动作脚本驱动玩家操作（移动/跳跃/交互），
        进程内抓帧，产出 report.json 证据并评分。产物按 paths 约定落在
        ``<project>/.gameforge/<run_id>/playtest/``。

        配置 ``playtest.enabled``（默认关）开启；失败时把 findings 喂给
        code_generator 修复一轮并重放一次（有界反馈）。
        """
        pt_cfg = (self.config.get("playtest", {}) or {})
        if not pt_cfg.get("enabled", False):
            return None
        pid = self._resolve_preview_project_id(state)
        if not pid:
            return None

        from src.engine.godot.playtest import PlaytestRunner, build_default_action_plan

        if event_callback is None:
            async def event_callback(_event_type, _data=None):
                pass

        runtime_config = self._godot_project_config(state) or self.config
        runner = PlaytestRunner(runtime_config)
        actions = pt_cfg.get("actions") or build_default_action_plan()
        run_id = paths.new_run_id()
        state["playtest_run_id"] = run_id

        # run 级元数据：本 run 的全部产物（playtest/视觉审查/评测）共用该 run_id
        gdm = state.get("game_design_model")
        await asyncio.to_thread(
            paths.write_run_meta, pid, run_id,
            meta={
                "requirements": (state.get("project_context", {}) or {}).get("requirements", ""),
                "genre": (gdm or {}).get("genre", "") if isinstance(gdm, dict) else "",
                "scene_path": state.get("scene_path", ""),
            },
        )
        # 冒烟结果同样落盘为产物，供产物级评测读取（eval 只读不重跑）
        if state.get("runtime_smoke_result"):
            await asyncio.to_thread(
                paths.write_json,
                paths.run_dir(pid, run_id) / "runtime_smoke.json",
                state["runtime_smoke_result"],
            )

        scene_path = state.get("scene_path") or None
        max_rounds = int(pt_cfg.get("max_fix_attempts", 1)) + 1
        summary: Dict[str, Any] = {}
        for attempt in range(1, max_rounds + 1):
            await event_callback("phase_start", {
                "phase": "playtest",
                "message": f"Playtest 输入回放（第{attempt}次）...",
            })
            summary = await asyncio.to_thread(
                runner.run, actions,
                **{"project_id": pid, "run_id": run_id, "scene_path": scene_path},
            )

            # P1 视觉审查：帧证据交给多模态模型按失败模式清单打分；
            # 高严重度问题把 playtest 判为未通过，进入下方有界修复
            if summary.get("ok") and not summary.get("skipped"):
                review = await self._visual_review(state, pid, run_id, summary)
                if review:
                    summary["visual_review"] = review
                    if review.get("ok") is False:
                        summary["ok"] = False
                        for issue in review.get("issues", []):
                            if str(issue.get("severity", "")).lower() == "high":
                                summary.setdefault("findings", []).append(
                                    f"视觉审查[{issue.get('area', '?')}]: {issue.get('description', '')}"
                                )

            await event_callback("playtest_result", {
                "ok": summary.get("ok"),
                "skipped": summary.get("skipped", False),
                "checks": summary.get("checks", {}),
                "findings": summary.get("findings", [])[:5],
                "run_id": run_id,
                "attempt": attempt,
            })
            if summary.get("ok") or summary.get("skipped") or attempt >= max_rounds:
                break

            # 有界修复：findings → code_generator 修复 → 重放
            findings = summary.get("findings") or []
            if not findings:
                break
            state.setdefault("warnings", []).append(
                f"playtest 未通过（第{attempt}次），进入 code_generator 修复"
            )
            state["error_log"] = [f"playtest: {f}" for f in findings[:5]]
            fix_result = await self.code_generator.fix_code(state, state["error_log"])
            state.update(fix_result)
            # 重新落盘修复后的脚本（与 runtime smoke 修复路径一致）
            try:
                from src.engine.godot import GodotEditor
                editor = GodotEditor(runtime_config)
                updated_gd = {
                    k: v for k, v in state.get("code_generated", {}).items()
                    if k.endswith(".gd")
                }
                if updated_gd:
                    await asyncio.to_thread(editor.import_files, updated_gd)
            except Exception as e:  # noqa: BLE001
                self.log_error("playtest_reimport_failed", {"error": str(e)})

        # P0-4 产物级评测：聚合本 run 的全部证据 → eval/summary.json
        try:
            from src.eval.artifacts import evaluate_run

            summary["eval"] = await asyncio.to_thread(evaluate_run, pid, run_id)
        except Exception as e:  # noqa: BLE001
            self.log_error("eval_run_failed", {"error": str(e)})

        return summary

    async def _visual_review(
        self, state: GameDevState, pid: str, run_id: str, playtest_summary: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """P1 视觉审查：把 playtest 帧交给多模态模型按清单打分。

        未启用/无 key/模型异常时返回 None 或 skipped 结果，不阻塞主流程。
        """
        from src.engine.godot.visual_review import VisualReviewer

        reviewer = VisualReviewer(self.config)
        if not reviewer.enabled:
            return None
        frames_dir = paths.playtest_frames_dir(pid, run_id)
        frame_names = playtest_summary.get("frames") or []
        if not frames_dir.is_dir() or not frame_names:
            return None
        requirements = (state.get("project_context", {}) or {}).get("requirements", "")
        return await reviewer.review(
            frames_dir, frame_names,
            project_id=pid, run_id=run_id,
            context=f"需求：{requirements}",
        )

    async def _try_godot_pipeline(self, state: GameDevState, event_callback) -> None:
        """Godot 一键构建：导入代码 → 编译 → 构建场景

        路由逻辑同 :meth:`_godot_compile_loop`：

        - ``auto`` / ``headless`` 且配置了 Godot 引擎路径 → 走 headless
          （无需打开编辑器 GUI，直接调用 Godot 引擎二进制落盘 .gd/.tscn 并校验）
        - 否则退回 8765 HTTP 编辑器插件
        """
        sandbox_cfg = self._sandbox_project_config(state)
        use_sandbox = sandbox_cfg is not None
        project_cfg = self._godot_project_config(state) or self.config

        # —— 一键构建模式路由 ——
        compile_mode = self.config.get("godot", {}).get("compile_mode", "auto")
        if compile_mode in ("auto", "headless"):
            from src.engine.godot import GodotEditor
            editor = GodotEditor(project_cfg)
            valid, _editor_msg = editor.validate()
            if valid:
                await self._try_godot_pipeline_headless(state, event_callback, editor)
                return
            if compile_mode == "headless":
                await event_callback("scene_skipped", {
                    "reason": "godot_unavailable",
                    "message": "未配置 Godot 引擎路径（设置 godot.editor_path 或环境变量 GODOT_EDITOR_PATH），无法使用 headless 构建",
                    **self._preview_meta(state),
                })
                return
            if use_sandbox:
                # 沙箱模式下不可退回 HTTP（会污染主线），直接跳过
                await event_callback("scene_skipped", {
                    "reason": "sandbox_headless_unavailable",
                    "message": "沙箱模式需要 headless Godot（设置 godot.editor_path），HTTP 模式会修改主线，已跳过",
                    **self._preview_meta(state),
                })
                return

        from src.engine.godot.godot_http_client import GodotHTTPClient

        client = GodotHTTPClient()
        if not await client.check_health():
            await event_callback("scene_skipped", {
                "reason": "godot_http_unavailable",
                "message": "Godot Editor HTTP Server 未运行，跳过自动构建",
                **self._preview_meta(state),
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
                await event_callback("scene_complete", {
                    "scene_name": scene_desc.get("scene_name", "GameScene"),
                    "scene_path": scene_result.get("scene_path", ""),
                    "object_count": len(scene_desc.get("game_objects", [])),
                    "compile_status": "success" if not errors else "with_errors",
                    **self._preview_meta(state),
                })
            else:
                state["scene_status"] = "error"
                await event_callback("scene_error", {
                    "message": scene_result.get("error", "场景构建失败"),
                    **self._preview_meta(state),
                })

        if not errors:
            await event_callback("compile_result", {
                "status": "success",
                "message": f"编译成功！共 {len(gd_files)} 个文件",
            })

    async def _try_godot_pipeline_headless(
        self, state: GameDevState, event_callback, editor
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
                # 固定写 scenes/main.tscn：project.godot 的 run/main_scene、
                # 预览端 _need_build、基线检查与导出端点都只认这个路径
                # （与 scene_to_godot.write_project 的约定一致）
                # import_files 同步写盘，放入线程池避免阻塞事件循环
                await asyncio.to_thread(
                    editor.import_files, {"scenes/main.tscn": tscn_text}
                )
                state["scene_status"] = "success"
                state["scene_path"] = "res://scenes/main.tscn"
                await event_callback("scene_complete", {
                    "scene_name": name,
                    "scene_path": state["scene_path"],
                    "object_count": len(scene_desc.get("game_objects", [])),
                    "compile_status": "headless",
                    **self._preview_meta(state),
                })
            except Exception as e:
                self.log_error("headless_scene_build_failed", {"error": str(e)})

        # 3) headless 一次性校验（不做自动修复闭环）
        import os as _os2
        _proj2 = (self._godot_project_config(state) or self.config).get("godot", {}).get(
            "project_path", ""
        ) or _os2.getcwd()
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
        except TimeoutError:
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
                from src.utils.godot_compatibility_validator import (
                    validate_godot_compatibility,
                )
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
            # Scene IR 落盘：配方命中时不跑场景生成，IR 必须随配方复用并补写磁盘，
            # 否则预览端点读不到 IR 而回退到通用 platformer 主题（品类错配），
            # eval 的 project_completeness 也拿不到 has_scene_ir。
            # 历史配方没有 scene_ir 字段时 _persist_scene_ir 收到 None 直接跳过。
            scene_ir = state.get("scene_ir")
            if scene_ir:
                await asyncio.to_thread(self._persist_scene_ir, state, scene_ir)
            if not self._project_materialized(state) or state.get("scene_status") in (
                None, "pending", "skipped",
            ):
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

    async def _run_verification_chain(
        self,
        state: GameDevState,
        event_callback,
    ) -> Tuple[bool, Dict[str, Any]]:
        """跑完整验证闭环：安全闸门 → 运行时冒烟 → 配方沉淀 → playtest → 稳定性指标。

        ``run()``（批处理）与 ``run_with_streaming()``（SSE）共用同一实现。
        此前这段逻辑只写在流式入口里，批处理/同步接口在
        ``_try_godot_pipeline`` 之后就返回了——gd-guard 闸门、运行时冒烟、
        playtest 全不跑，等于 batch 调用方拿到的是未经任何验证的产物。

        Returns:
            (blocked, smoke_summary)：blocked=True 表示 gd-guard 拦截
            （调用方应提前收尾，且不要合并回主线）；smoke_summary 供
            调用方拼装 complete 事件。
        """
        # 安全闸门：gd-guard 扫描生成脚本（危险 API 一票否决，Rust 二进制缺失则跳过）
        if await self._gd_guard_scan(state, event_callback):
            return True, {}

        # P0-2 运行时冒烟测试（"可运行"闭环）
        smoke_summary = await self._runtime_smoke_test(state, event_callback)
        state["runnable"] = smoke_summary.get("runnable")

        # P1 语义级复用：真机冒烟通过 → 沉淀为已验证配方，供后续同类需求复用
        # （save_recipe 同步写完整代码 JSON，放入线程池避免阻塞事件循环）
        if state.get("runnable") is True:
            await asyncio.to_thread(self._bake_if_verified, state)

        # P1 playtest：真正玩游戏（输入回放 + 帧证据）——编译通过 ≠ 可玩
        playtest_summary = await self._playtest(state, event_callback)
        if playtest_summary:
            state["playtest"] = playtest_summary

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

        return False, smoke_summary

    # ========== 运行入口 ==========

    async def run(self, input_state: Dict[str, Any]) -> Dict[str, Any]:
        """运行工作流（批处理模式）"""
        from src.utils.metrics import (
            record_file_generated,
            record_fix_attempt,
            record_task_completed,
            record_workflow_run,
            set_active_workflows,
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
                self._sandbox_cleanup_if_needed(project_id)
            except Exception as e:
                self.logger.warning("sandbox_create_failed", error=str(e))

        # P1 语义级复用：命中已验证配方 → 快速路径直接返回
        recipe_state = await self._run_recipe(state, None)
        if recipe_state is not None:
            if sandbox_task and self.sandbox_enabled and self.sandbox_auto_merge:
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
                # scene_ir：场景任务在 LangGraph 之外写，必须和图内快照合并，
                # 否则配方沉淀拿不到它（预览端会回退到通用主题）
                "scene_ir",
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
            if sandbox_task and self.sandbox_enabled and self.sandbox_auto_rollback:
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

        # 验证闭环（与 run_with_streaming 共用同一实现）：gd-guard → 冒烟 → 配方 → playtest。
        # 此前批处理入口在这里就返回了，gd-guard 闸门/运行时冒烟/playtest 全不跑，
        # 批量调用方拿到的是未经任何验证的产物。
        blocked, _smoke_summary = await self._run_verification_chain(
            state, lambda *a, **kw: asyncio.sleep(0)
        )
        if blocked:
            # gd-guard 拦截：不合并回主线，runnable/warnings 已在链内置位
            return state

        # Sandbox：成功则合并回主线
        if sandbox_task and self.sandbox_enabled and self.sandbox_auto_merge:
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
            record_file_generated,
            record_fix_attempt,
            record_task_completed,
            record_workflow_run,
            set_active_workflows,
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
                self._sandbox_cleanup_if_needed(project_id)
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
                    "scene_ir",
                ):
                    if _key in state:
                        final_state[_key] = state[_key]
                state = final_state

        except Exception as e:
            _success = False
            await event_callback("error", {"message": f"生成过程出错: {str(e)}"})
            if sandbox_task and self.sandbox_enabled and self.sandbox_auto_rollback:
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

        # 验证闭环（与 run 共用同一实现）：gd-guard → 冒烟 → 配方沉淀 → playtest → 稳定性指标
        blocked, smoke_summary = await self._run_verification_chain(state, event_callback)
        if blocked:
            return state

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
            "playtest": {
                "ok": (state.get("playtest") or {}).get("ok"),
                "skipped": (state.get("playtest") or {}).get("skipped", False),
                "run_id": (state.get("playtest") or {}).get("run_id"),
            },
            "warnings": state.get("warnings", []),
            **self._preview_meta(state),
        })

        # Sandbox：成功则合并回主线
        if sandbox_task and self.sandbox_enabled and self.sandbox_auto_merge:
            try:
                self.sandbox.merge(sandbox_task)
            except Exception as e:
                self.logger.warning("sandbox_merge_failed", error=str(e))

        return state


def create_workflow(config: Dict[str, Any]) -> GameDevWorkflow:
    """创建工作流实例"""
    return GameDevWorkflow(config)
