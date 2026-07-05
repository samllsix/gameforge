"""GameForge - Multi-Agent工作流定义模块

基于LangGraph的状态图定义，管理游戏开发全流程。
专注于 Godot 引擎，生成 GDScript 代码和 .tscn 场景文件。
"""

import time as _time

import asyncio
import json
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
from src.core.memory import MemoryManager


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
        self.orchestrator = OrchestratorAgent(config)
        self.game_designer = GameDesignerAgent(config)
        self.planner = PlannerAgent(config)
        self.code_generator = CodeGeneratorAgent(config)
        self.code_reviewer = CodeReviewerAgent(config)
        self.test_generator = TestGeneratorAgent(config)
        self.debugger = DebuggerAgent(config)
        self.scene_generator = SceneGeneratorAgent(config)
        self.refactor_agent = RefactorAgent(config)

        # 记忆系统 — 让 Agent 有上下文记忆
        self.memory = MemoryManager()

        self.graph = self._build_graph()

    def _build_graph(self):
        """构建LangGraph状态图，返回编译后的可执行图"""
        workflow = StateGraph(GameDevState)

        # 添加所有节点
        workflow.add_node("game_designer", self._game_designer_node)
        workflow.add_node("planner", self._planner_node)
        workflow.add_node("orchestrator", self._orchestrator_node)
        workflow.add_node("code_generator", self._code_generator_node)
        workflow.add_node("code_reviewer", self._code_reviewer_node)
        workflow.add_node("refactor", self._refactor_node)
        workflow.add_node("test_generator", self._test_generator_node)
        workflow.add_node("debugger", self._debugger_node)

        # 入口点
        workflow.set_entry_point("game_designer")

        # 固定边：线性流水线段
        workflow.add_edge("game_designer", "planner")
        workflow.add_edge("planner", "orchestrator")
        workflow.add_edge("code_generator", "code_reviewer")
        workflow.add_edge("code_reviewer", "refactor")
        workflow.add_edge("refactor", "test_generator")
        workflow.add_edge("test_generator", "orchestrator")
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
            task_plan = await self.planner.plan(state)
            return {
                "task_plan": task_plan,
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

    async def _debugger_node(self, state: GameDevState) -> Dict[str, Any]:
        """调试节点 — 分析 GDScript 错误并生成修复"""
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

            if result.get("compile_errors"):
                state.setdefault("scene_compile_errors", []).extend(result["compile_errors"])

            scene_desc = result.get("scene_description")
            if scene_desc:
                scene_json = json.dumps(scene_desc, indent=2, ensure_ascii=False)
                state["code_generated"]["scenes/scene_description.json"] = scene_json

            if status == "built":
                msg = "Godot 场景已生成！请在 Godot Editor 中查看"
                await event_callback("scene_complete", {
                    "message": msg,
                    "scene_path": result.get("scene_path", ""),
                    "object_count": len(scene_desc.get("game_objects", [])) if scene_desc else 0,
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

        client = GodotHTTPClient()
        if not await client.check_health():
            await event_callback("compile_result", {
                "status": "skipped",
                "message": "Godot Editor 未启动，跳过编译闭环",
            })
            return

        code_files = state.get("code_generated", {})
        gd_files = {k: v for k, v in code_files.items() if k.endswith(".gd")}
        if not gd_files:
            return

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

    async def _try_godot_pipeline(self, state: GameDevState, event_callback) -> None:
        """Godot 一键构建：导入代码 → 编译 → 构建场景"""
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

        # 第一步：导入所有代码文件
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
            await event_callback("scene_start", {"message": "正在构建 Godot 场景..."})
            scene_result = await client.send_scene(scene_desc)
            if scene_result.get("status") == "success":
                state["scene_status"] = "success"
                state["scene_path"] = scene_result.get("scene_path", "")
                await event_callback("scene_complete", {
                    "scene_name": scene_desc.get("scene_name", "GameScene"),
                    "scene_path": scene_result.get("scene_path", ""),
                    "object_count": len(scene_desc.get("game_objects", [])),
                    "compile_status": "success" if not errors else "with_errors",
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
            "error_log": [],
            "scene_description": None,
            "scene_status": "pending",
            "scene_error": None,
            "game_design_model": None,
            "file_metadata": {},
            "validation_result": None,
            "warnings": [],
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
            raise
        finally:
            record_workflow_run(_success, _time.time() - _start)
            set_active_workflows(0)

        await self._post_process(state, scene_task)

        # Godot 一键构建 pipeline
        if state.get("scene_status") in (None, "pending", "skipped"):
            await self._try_godot_pipeline(state, lambda *a, **kw: asyncio.sleep(0))

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
                state = final_state

        except Exception as e:
            _success = False
            await event_callback("error", {"message": f"生成过程出错: {str(e)}"})

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

        await event_callback("complete", {
            "phase": "complete",
            "message": "代码生成完成！",
            "files": state.get("code_generated", {}),
            "task_count": len(state.get("task_plan", [])),
            "fix_count": len(state.get("fix_history", [])),
            "scene_status": state.get("scene_status", "pending"),
            "scene_path": state.get("scene_path", ""),
            "warnings": state.get("warnings", []),
        })

        return state


def create_workflow(config: Dict[str, Any]) -> GameDevWorkflow:
    """创建工作流实例"""
    return GameDevWorkflow(config)
