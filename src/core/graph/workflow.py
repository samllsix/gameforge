"""GameForge - Multi-Agent工作流定义模块

基于LangGraph的状态图定义，管理游戏开发全流程。
使用 .ainvoke() / .astream_events() 驱动执行，LangGraph 负责状态合并（reducer）和路由。
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
        """代码生成节点 — 根据当前任务生成代码

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

            # 注入记忆上下文 — 让 CodeGenerator 参考历史经验
            memory_context = self.memory.get_context_for_agent(
                "code_generator", query=current_task.get("name", "")
            )
            if memory_context:
                state = {**state, "_memory_context": memory_context}

            code_artifacts = await self.code_generator.generate(state, current_task)

            # 标记任务完成（修改 task_plan 副本）
            updated_plan = [dict(t) for t in task_plan]
            for task in updated_plan:
                if task.get("id") == current_task_id:
                    task["status"] = TaskStatus.COMPLETED.value
                    break

            # 只返回新代码，reducer 自动合并到 code_generated
            new_code = {art["file_path"]: art["content"] for art in code_artifacts}

            # 统一验证 — 语法 + Unity兼容性 + 沙箱安全，一次完成
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

            # 沙箱安全检查
            try:
                from src.engine.sandbox import SandboxExecutor
                sandbox = SandboxExecutor(self.config)
                for path, content in new_code.items():
                    if path.endswith(".cs"):
                        is_safe, issues = sandbox.validate_code(content)
                        if not is_safe:
                            warnings.append(f"安全问题 {path}: {'; '.join(issues[:2])}")
            except Exception as e:
                logger.warning("sandbox_check_failed", error=str(e))

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
        """代码审查节点 — 审查生成的代码"""
        try:
            review_result = await self.code_reviewer.review(state)
            return {"current_phase": "code_reviewed", "review_result": review_result}
        except Exception as e:
            return {"error_log": [f"Code reviewer failed: {e}"], "current_phase": "error"}

    async def _refactor_node(self, state: GameDevState) -> Dict[str, Any]:
        """重构节点 — 分析代码质量并重构，重构后统一验证"""
        try:
            result = await self.refactor_agent.execute(state)

            # 重构后统一验证 — 语法 + Unity兼容性，防止 LLM 重构破坏代码
            refactored_code = result.get("code_generated", {})
            if refactored_code:
                try:
                    from src.utils.unified_validator import validate_all
                    validation = validate_all(refactored_code)
                    if validation.has_errors:
                        # 重构引入了问题，回退到重构前的代码
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
        """测试生成节点 — 为代码生成测试用例"""
        try:
            test_code = await self.test_generator.generate(state)
            return {
                "code_generated": test_code,
                "current_phase": "test_generated",
            }
        except Exception as e:
            return {"error_log": [f"Test generator failed: {e}"], "current_phase": "error"}

    async def _orchestrator_node(self, state: GameDevState) -> Dict[str, Any]:
        """编排节点 — 调度下一个任务

        职责：
        1. 检查错误状态 → 路由到 debugger
        2. 选择下一个待执行任务
        3. 判断工作流是否完成
        """
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
        """调试节点 — 分析错误并生成修复"""
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
            # 非代码任务直接标记完成，回到 orchestrator
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
        await event_callback("scene_start", {"message": "正在生成Unity场景..."})
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
                state["code_generated"]["Assets/Scenes/scene_description.json"] = scene_json

            if status == "built":
                compile_status = result.get("compile_status", "unknown")
                msg = "Unity场景已生成！请在Unity Editor中查看"
                if compile_status == "error":
                    msg = f"Unity场景已生成，但有编译错误（{len(result.get('compile_errors', []))}个）"
                await event_callback("scene_complete", {
                    "message": msg,
                    "scene_path": result.get("scene_path", ""),
                    "object_count": result.get("object_count", 0),
                    "compile_status": compile_status,
                    "compile_errors": result.get("compile_errors", []),
                })
            elif status == "skipped":
                await event_callback("scene_skipped", {
                    "message": result.get("message", "场景描述已生成，Unity未构建"),
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

    # ========== Unity编译闭环 ==========

    async def _unity_compile_loop(self, state: GameDevState, event_callback, max_rounds: int = 3):
        """Unity编译闭环：导入→编译→读错误→自动修复→重编译"""
        from src.engine.unity.unity_http_client import UnityHTTPClient

        client = UnityHTTPClient()
        if not await client.check_health():
            await event_callback("compile_result", {
                "status": "skipped",
                "message": "Unity Editor未启动，跳过编译闭环",
            })
            return

        code_files = state.get("code_generated", {})
        cs_files = {k: v for k, v in code_files.items() if k.endswith(".cs")}
        if not cs_files:
            return

        await event_callback("phase_start", {"phase": "compiling", "message": "正在导入代码到Unity..."})
        import_result = await client.import_files(cs_files)
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
                    "message": f"编译成功！共{len(cs_files)}个文件",
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
                    code = err.get("code", "")
                    error_log.append(f"{file}({line}): error {code}: {msg}")
                else:
                    error_log.append(str(err))

            state["error_log"] = error_log
            debug_result = await self._debugger_node(state)
            state.update(debug_result)

            updated_files = state.get("code_generated", {})
            updated_cs = {k: v for k, v in updated_files.items() if k.endswith(".cs")}
            if updated_cs != cs_files:
                cs_files = updated_cs
                await client.import_files(cs_files)

        await event_callback("compile_result", {
            "status": "partial",
            "message": f"经过{max_rounds}轮修复仍有编译错误，可能需要人工介入",
        })

    # ========== 后处理（共享逻辑） ==========

    def _sanitize_scene_scripts(self, state: GameDevState) -> None:
        """清理场景描述中引用的不存在脚本"""
        import re as _re
        scene_desc = state.get("scene_description")
        if not scene_desc:
            return

        code_generated = state.get("code_generated", {})
        generated_classes = set()
        for fpath, content in code_generated.items():
            if fpath.endswith(".cs"):
                m = _re.search(r'public\s+(?:partial\s+)?(?:class|struct|interface)\s+(\w+)', content)
                if m:
                    generated_classes.add(m.group(1))

        from src.core.tools import is_unity_builtin as _is_unity_builtin
        removed_scripts = []

        def _clean_components(components):
            cleaned = []
            for comp in components:
                comp_type = comp.get("type", "")
                if not comp_type:
                    cleaned.append(comp)
                elif _is_unity_builtin(comp_type) or comp_type in generated_classes:
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
        """生成完整项目产物（README、元数据、Unity项目模板等）"""
        code_generated = state.setdefault("code_generated", {})

        self._sanitize_scene_scripts(state)

        # README 和项目配置建议
        try:
            from src.agents.code_generator import CodeGeneratorAgent
            code_gen = CodeGeneratorAgent(self.config)
            project_name = state.get("project_context", {}).get("project_name", "GameForge")
            code_files = state.get("code_generated", {})
            task_plan = state.get("task_plan", [])

            if "Assets/README_Unity.md" not in code_generated:
                readme = code_gen.generate_readme(project_name, task_plan, code_files)
                code_generated["Assets/README_Unity.md"] = readme

            if "Assets/ProjectSettings_Suggestions.md" not in code_generated:
                settings = code_gen.generate_project_settings(code_files)
                code_generated["Assets/ProjectSettings_Suggestions.md"] = settings
        except Exception as e:
            state.setdefault("warnings", []).append(f"README/设置文档生成失败: {e}")

        # scene_description.json
        scene_desc = state.get("scene_description")
        if scene_desc:
            code_generated["Assets/Scenes/scene_description.json"] = json.dumps(scene_desc, indent=2, ensure_ascii=False)

        # GameDesignModel.json
        if "Assets/GameDesignModel.json" not in code_generated:
            full_gdm = state.get("game_design_model") or {}
            full_gdm["_meta"] = {
                "project_name": state.get("project_context", {}).get("project_name", "GameForge"),
                "engine": state.get("project_context", {}).get("engine", "unity"),
                "task_count": len(state.get("task_plan", [])),
                "scene_status": state.get("scene_status", "pending"),
                "generated_at": __import__("datetime").datetime.now().isoformat(),
            }
            code_generated["Assets/GameDesignModel.json"] = json.dumps(full_gdm, indent=2, ensure_ascii=False)

        # CodeMetadata.json
        if "Assets/CodeMetadata.json" not in code_generated:
            file_metadata = state.get("file_metadata", {})
            cm = {
                "files": list(code_generated.keys()),
                "file_metadata": file_metadata,
                "total_files": len(code_generated),
                "cs_files": len([f for f in code_generated if f.endswith(".cs")]),
            }
            code_generated["Assets/CodeMetadata.json"] = json.dumps(cm, indent=2, ensure_ascii=False)

        # ValidationReport.json
        if "Assets/ValidationReport.json" not in code_generated:
            validation_result = state.get("validation_result")
            if validation_result:
                code_generated["Assets/ValidationReport.json"] = json.dumps(validation_result, indent=2, ensure_ascii=False)

        # Unity Editor HTTP Server 插件
        if "Assets/Editor/GameForgeHttpServer.cs" not in code_generated:
            template_path = Path(__file__).parent.parent.parent.parent / "config" / "templates" / "unity" / "GameForgeHttpServer.cs.template"
            if template_path.exists():
                plugin_content = template_path.read_text(encoding="utf-8")
                code_generated["Assets/Editor/GameForgeHttpServer.cs"] = plugin_content

        # Unity 项目模板
        try:
            from src.engine.unity.project_generator import UnityProjectGenerator
            project_files = UnityProjectGenerator().generate_all(state)
            for path, content in project_files.items():
                if path not in code_generated:
                    code_generated[path] = content
        except Exception as e:
            state.setdefault("warnings", []).append(f"Unity项目模板生成失败: {e}")

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

        # 统一验证 — 语法 + Unity兼容性 + 一致性，一次完成
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

        # ========== P1: Eval 评测系统 — 自动生成评测报告 ==========
        try:
            from src.eval.metrics import run_evaluation
            project_name = state.get("project_context", {}).get("project_name", "GameForge")
            eval_report = run_evaluation(
                project_name=project_name,
                code_files=state.get("code_generated", {}),
                tasks=state.get("task_plan", []),
                fix_history=state.get("fix_history", []),
            )

            # Unity 兼容性评分
            try:
                from src.utils.unity_compatibility_validator import validate_unity_compatibility
                compat = validate_unity_compatibility(state.get("code_generated", {}))
                compat_score = 100.0 if not compat.has_errors else max(0, 100 - len(compat.errors) * 10)
                eval_report.add_metric(
                    "unity_compatibility", compat_score,
                    details={"errors": len(compat.errors), "warnings": len(compat.warnings)}
                )
            except Exception as e:
                logger.warning("unity_compat_score_failed", error=str(e))

            # 保存评测报告
            report_path = eval_report.save()
            state["eval_report"] = eval_report.to_dict()
            state.setdefault("warnings", []).append(f"评测报告已保存: {report_path}")
        except Exception as e:
            state.setdefault("warnings", []).append(f"评测系统异常: {e}")

        # ========== P1: Sandbox 沙箱验证 — C# 代码安全性检查 ==========
        try:
            from src.engine.sandbox import SandboxExecutor
            sandbox = SandboxExecutor(self.config)
            cs_files = {p: c for p, c in state.get("code_generated", {}).items() if p.endswith(".cs")}
            if cs_files:
                sandbox_issues = []
                for path, content in cs_files.items():
                    is_safe, issues = sandbox.validate_code(content)
                    if not is_safe:
                        sandbox_issues.extend([f"{path}: {i}" for i in issues])
                if sandbox_issues:
                    state.setdefault("warnings", []).append(
                        f"沙箱安全检查发现 {len(sandbox_issues)} 个问题: {'; '.join(sandbox_issues[:5])}"
                    )
        except Exception as e:
            logger.warning("sandbox_validation_skipped", error=str(e))

        # 保存项目记忆 — 记录本次生成的错误和经验
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
        """运行工作流（批处理模式）

        使用 .ainvoke() 驱动图执行，LangGraph 负责状态合并和路由。
        图内部通过 orchestrator 的条件边实现多任务循环调度，
        无需外层循环——ainvoke 会一直运行到图到达 END 节点。
        """
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

        # 递归限制：防止图内部无限循环（每个节点调用算 1 次递归）
        max_iterations = self.config.get("agents", {}).get("orchestrator", {}).get("max_iterations", 10)
        recursion_limit = max(max_iterations * 12, 100)  # 每轮约 8-10 个节点调用
        _success = True

        try:
            # 单次 ainvoke —— 图内部 orchestrator 条件边负责多任务循环调度
            # game_designer → planner → orchestrator → [code_gen/review/refactor/test] → orchestrator → ... → END
            result = await self.graph.ainvoke(state, config={"recursion_limit": recursion_limit})
            state = result

            # 记录任务完成指标
            for task in state.get("task_plan", []):
                if task.get("status") == TaskStatus.COMPLETED.value:
                    record_task_completed(task.get("type", "unknown"))

            # 记录文件生成指标
            for file_path in state.get("code_generated", {}).keys():
                ext = file_path.rsplit(".", 1)[-1] if "." in file_path else "unknown"
                record_file_generated(ext)

            # 记录修复指标
            for fix in state.get("fix_history", []):
                record_fix_attempt(fix.get("success", False))

        except Exception:
            _success = False
            raise
        finally:
            record_workflow_run(_success, _time.time() - _start)
            set_active_workflows(0)

        await self._post_process(state, scene_task)
        return state

    async def run_with_streaming(
        self, input_state: Dict[str, Any], event_callback
    ) -> Dict[str, Any]:
        """运行工作流（流式模式）

        使用 .astream_events() 驱动图执行，自动推送每个节点的执行事件。
        """
        from src.utils.metrics import (
            record_workflow_run, record_task_completed, record_file_generated,
            record_fix_attempt, set_active_workflows,
        )

        state = self._make_initial_state(input_state)
        _start = _time.time()
        _success = True
        set_active_workflows(1)

        # 加载项目记忆
        project_name = state.get("project_context", {}).get("project_name", "default")
        self.memory.project_memory.load(project_name)

        scene_task = asyncio.create_task(
            self._run_scene_generation(state, event_callback)
        )

        max_iterations = self.config.get("agents", {}).get("orchestrator", {}).get("max_iterations", 10)
        recursion_limit = max(max_iterations * 12, 100)

        try:
            # 发送迭代开始事件
            await event_callback("phase_start", {
                "phase": "iterating",
                "message": "正在执行工作流...",
            })

            # 单次 ainvoke —— 图内部 orchestrator 条件边负责多任务循环调度
            # 使用 astream_events 获取图执行的实时事件
            async for event in self.graph.astream_events(state, version="v2", config={"recursion_limit": recursion_limit}):
                kind = event.get("event", "")
                node_name = event.get("name", "")

                if kind == "on_chain_start" and node_name not in ("__start__", "__end__", "LangGraph"):
                    await event_callback("phase_start", {
                        "phase": node_name,
                        "message": f"正在执行: {node_name}...",
                    })
                elif kind == "on_chain_end" and node_name not in ("__start__", "__end__", "LangGraph", "_route_next"):
                    output = event.get("data", {}).get("output", {})
                    if output and isinstance(output, dict):
                        # 发送代码文件事件
                        new_code = output.get("code_generated", {})
                        if new_code:
                            for file_path, content in new_code.items():
                                await event_callback("code_file", {
                                    "file_path": file_path,
                                    "content": content,
                                })

                        # 发送审查结果事件
                        review = output.get("review_result")
                        if review:
                            await event_callback("review_result", review)

            # 读取图执行后的最终状态
            # astream_events 不直接返回最终状态，需要重新 ainvoke 获取
            state = await self.graph.ainvoke(state, config={"recursion_limit": recursion_limit})

        except Exception as e:
            _success = False
            await event_callback("error", {"message": f"生成过程出错: {str(e)}"})

        # 记录指标
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
