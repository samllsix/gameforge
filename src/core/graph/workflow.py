"""GameForge - Multi-Agent工作流定义模块

基于LangGraph的状态图定义，管理游戏开发全流程。
"""

import asyncio
import copy
from pathlib import Path
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


class GameDevWorkflow:
    """游戏开发工作流管理器"""

    def __init__(self, config: Dict[str, Any]):
        """初始化工作流

        Args:
            config: 工作流配置
        """
        self.config = config
        self.orchestrator = OrchestratorAgent(config)
        self.game_designer = GameDesignerAgent(config)
        self.planner = PlannerAgent(config)
        self.code_generator = CodeGeneratorAgent(config)
        self.code_reviewer = CodeReviewerAgent(config)
        self.test_generator = TestGeneratorAgent(config)
        self.debugger = DebuggerAgent(config)
        self.scene_generator = SceneGeneratorAgent(config)

        self.workflow = self._build_workflow()

    def _build_workflow(self) -> StateGraph:
        """构建LangGraph状态图

        Returns:
            配置好的状态图
        """
        # 创建状态图
        workflow = StateGraph(GameDevState)

        # 添加节点（已移除refactor节点，代码质量由code_generator直接保证）
        workflow.add_node("planner", self._planner_node)
        workflow.add_node("code_generator", self._code_generator_node)
        workflow.add_node("code_reviewer", self._code_reviewer_node)
        workflow.add_node("test_generator", self._test_generator_node)
        workflow.add_node("orchestrator", self._orchestrator_node)
        workflow.add_node("debugger", self._debugger_node)

        # 设置入口点
        workflow.set_entry_point("planner")

        # 添加边（reviewer之后直接回到orchestrator，不再经过refactor）
        workflow.add_edge("planner", "orchestrator")
        workflow.add_edge("code_generator", "code_reviewer")
        workflow.add_edge("code_reviewer", "orchestrator")
        workflow.add_edge("test_generator", "orchestrator")
        workflow.add_edge("debugger", "code_generator")  # 修复后重新生成

        # 添加条件边
        workflow.add_conditional_edges(
            "orchestrator",
            self._route_next_task,
            {
                "code_generator": "code_generator",
                "test_generator": "test_generator",
                "debugger": "debugger",
                END: END,
            }
        )

        return workflow.compile()

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
        """规划节点 - 解析需求并生成任务计划"""
        try:
            task_plan = await self.planner.plan(state)
            return {
                "task_plan": task_plan,
                "current_phase": "planning_complete",
            }
        except Exception as e:
            return {"error_log": [f"Planner failed: {e}"], "current_phase": "error"}

    async def _code_generator_node(self, state: GameDevState) -> Dict[str, Any]:
        """代码生成节点 - 根据任务生成代码"""
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

            code_artifacts = await self.code_generator.generate(state, current_task)

            for task in task_plan:
                if task.get("id") == current_task_id:
                    task["status"] = TaskStatus.COMPLETED.value
                    break

            return {
                "code_generated": {
                    **state.get("code_generated", {}),
                    **{art["file_path"]: art["content"] for art in code_artifacts}
                },
                "code_artifacts": state.get("code_artifacts", []) + code_artifacts,
                "task_plan": task_plan,
                "current_phase": "code_generated",
            }
        except Exception as e:
            return {"error_log": [f"Code generator failed: {e}"], "current_phase": "error"}

    async def _code_reviewer_node(self, state: GameDevState) -> Dict[str, Any]:
        """代码审查节点 - 审查生成的代码"""
        try:
            review_result = await self.code_reviewer.review(state)
            return {"current_phase": "code_reviewed", "review_result": review_result}
        except Exception as e:
            return {"error_log": [f"Code reviewer failed: {e}"], "current_phase": "error"}

    async def _test_generator_node(self, state: GameDevState) -> Dict[str, Any]:
        """测试生成节点 - 为代码生成测试用例"""
        try:
            test_code = await self.test_generator.generate(state)
            task_plan = state.get("task_plan", [])
            current_task_id = state.get("current_task_id")
            for task in task_plan:
                if task.get("id") == current_task_id:
                    task["status"] = TaskStatus.COMPLETED.value
                    break
            return {
                "code_generated": {**state.get("code_generated", {}), **test_code},
                "task_plan": task_plan,
                "current_phase": "test_generated",
            }
        except Exception as e:
            return {"error_log": [f"Test generator failed: {e}"], "current_phase": "error"}

    async def _orchestrator_node(self, state: GameDevState) -> Dict[str, Any]:
        """编排节点 - 调度下一个任务（支持并行）"""
        try:
            task_plan = state.get("task_plan", [])
            fix_attempts = state.get("fix_attempts", 0)

            # 如果有错误日志，增加修复计数并路由到 debugger
            error_log = state.get("error_log", [])
            if error_log and fix_attempts < self.config.get("agents", {}).get("debugger", {}).get("max_fix_attempts", 5):
                return {
                    "current_phase": "needs_fix",
                    "fix_attempts": fix_attempts + 1,
                }

            ready_tasks = self._get_all_ready_tasks(task_plan)
            if not ready_tasks:
                return {"current_phase": "workflow_complete", "is_complete": True}

            ready_ids = [t.get("id") for t in ready_tasks]
            return {
                "current_task_id": ready_tasks[0].get("id"),
                "ready_task_ids": ready_ids,
                "current_phase": "task_assigned",
            }
        except Exception as e:
            return {"error_log": [f"Orchestrator failed: {e}"], "current_phase": "error"}

    async def _debugger_node(self, state: GameDevState) -> Dict[str, Any]:
        """调试节点 - 分析错误并生成修复"""
        try:
            error_log = state.get("error_log", [])
            fix_result = await self.debugger.analyze_and_fix(state, error_log)
            return {
                **fix_result,
                "current_phase": "fix_applied",
                "error_log": [],  # 清空错误日志
            }
        except Exception as e:
            return {"error_log": [f"Debugger failed: {e}"], "current_phase": "unrecoverable"}

    def _route_next_task(self, state: GameDevState) -> str:
        """路由到下一个任务节点"""
        current_phase = state.get("current_phase", "")

        if current_phase == "workflow_complete":
            return END
        if current_phase in ("error", "needs_fix"):
            return "debugger"

        task_plan = state.get("task_plan", [])
        current_task_id = state.get("current_task_id")

        if not current_task_id:
            return END

        current_task = None
        for task in task_plan:
            if task.get("id") == current_task_id:
                current_task = task
                break

        if not current_task:
            return END

        task_type = current_task.get("type", "code")
        if task_type == "code":
            return "code_generator"
        elif task_type == "test":
            return "test_generator"
        else:
            # scene/documentation/config/ui 任务由共享产物逻辑处理
            return END

    def _is_task_completed(self, task_plan: List[Dict], task_id: str) -> bool:
        """检查任务是否已完成"""
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

    # 非代码任务类型 — 由共享产物逻辑处理，不进入 CodeGenerator
    _NON_CODE_TASK_TYPES = {
        TaskType.SCENE.value,
        TaskType.DOCUMENTATION.value,
        TaskType.CONFIG.value,
        TaskType.UI.value,
        "scene", "documentation", "config", "ui",
    }

    async def _execute_tasks_parallel(self, state: GameDevState, tasks: List[Dict]) -> Dict[str, Any]:
        """并行执行多个独立任务"""
        async def _process_single_task(task):
            task_type = task.get("type", "code")
            if task_type == TaskType.TEST.value:
                return await self.test_generator.generate(state), task, "test"
            elif task_type in self._NON_CODE_TASK_TYPES:
                # 非代码任务直接返回空产物，标记完成
                return [], task, "artifact"
            else:
                return await self.code_generator.generate(state, task), task, "code"

        results = await asyncio.gather(
            *[_process_single_task(t) for t in tasks],
            return_exceptions=True,
        )

        merged_code = dict(state.get("code_generated", {}))
        merged_artifacts = list(state.get("code_artifacts", []))
        updated_plan = copy.deepcopy(state.get("task_plan", []))

        errors = []
        for result in results:
            if isinstance(result, Exception):
                errors.append(str(result))
                continue

            artifacts, task, _ = result
            if isinstance(artifacts, list):
                for art in artifacts:
                    merged_code[art["file_path"]] = art["content"]
                    merged_artifacts.append(art)
            elif isinstance(artifacts, dict):
                merged_code.update(artifacts)

            for t in updated_plan:
                if t.get("id") == task.get("id"):
                    t["status"] = TaskStatus.COMPLETED.value
                    break

        return {
            "code_generated": merged_code,
            "code_artifacts": merged_artifacts,
            "task_plan": updated_plan,
            "current_phase": "code_generated",
            "error_log": errors if errors else [],
        }

    async def _run_with_parallel_support(self, state: GameDevState) -> GameDevState:
        """运行工作流（支持并行任务执行）"""
        # Phase 0: 游戏设计
        design_result = await self._game_designer_node(state)
        state.update(design_result)

        # Phase 1: 规划
        plan_result = await self._planner_node(state)
        state.update(plan_result)

        max_iterations = self.config.get("agents", {}).get("orchestrator", {}).get("max_iterations", 10)

        for _ in range(max_iterations):
            # 编排器决定下一步
            orch_result = await self._orchestrator_node(state)
            state.update(orch_result)

            if state.get("is_complete") or state.get("current_phase") == "workflow_complete":
                break

            if state.get("current_phase") in ("error", "needs_fix"):
                debug_result = await self._debugger_node(state)
                state.update(debug_result)
                continue

            ready_ids = state.get("ready_task_ids", [])
            task_plan = state.get("task_plan", [])
            ready_tasks = [t for t in task_plan if t.get("id") in ready_ids]

            if len(ready_tasks) > 1:
                # 多个独立任务 → 并行执行
                parallel_result = await self._execute_tasks_parallel(state, ready_tasks)
                state.update(parallel_result)

                # 并行执行审查和测试生成
                review_result, test_result = await asyncio.gather(
                    self._code_reviewer_node(state),
                    self._test_generator_node(state),
                )
                state.update(review_result)
                state.update(test_result)
            elif ready_tasks:
                # 单个任务 → 完整流水线（reviewer + test_generator并行）
                task = ready_tasks[0]
                state["current_task_id"] = task.get("id")
                task_type = task.get("type", TaskType.CODE.value)

                if task_type == TaskType.TEST.value:
                    # 测试任务 → 直接生成测试
                    test_result = await self._test_generator_node(state)
                    state.update(test_result)
                elif task_type in self._NON_CODE_TASK_TYPES:
                    # scene/documentation/config/ui 任务 → 标记完成，由共享产物逻辑生成
                    task_plan = state.get("task_plan", [])
                    for t in task_plan:
                        if t.get("id") == task.get("id"):
                            t["status"] = TaskStatus.COMPLETED.value
                            break
                    state["task_plan"] = task_plan
                    continue
                else:
                    # 代码任务 → 生成 + 审查 + 测试（并行）
                    gen_result = await self._code_generator_node(state)
                    state.update(gen_result)

                    # 如果代码生成失败（如不支持的任务类型），标记任务完成避免死循环
                    error_log = state.get("error_log", [])
                    if error_log:
                        task_plan = state.get("task_plan", [])
                        for t in task_plan:
                            if t.get("id") == task.get("id"):
                                t["status"] = TaskStatus.COMPLETED.value
                                break
                        state["task_plan"] = task_plan
                        state["error_log"] = []
                        continue

                    # 并行执行代码审查和测试生成
                    review_result, test_result = await asyncio.gather(
                        self._code_reviewer_node(state),
                        self._test_generator_node(state),
                    )
                    state.update(review_result)
                    state.update(test_result)
            else:
                break

        return state

    async def _unity_compile_loop(self, state: GameDevState, event_callback, max_rounds: int = 3):
        """Unity编译闭环：导入→编译→读错误→自动修复→重编译

        Args:
            state: 当前状态
            event_callback: SSE事件回调
            max_rounds: 最大修复轮数
        """
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

        # 导入代码文件
        await event_callback("phase_start", {"phase": "compiling", "message": "正在导入代码到Unity..."})
        import_result = await client.import_files(cs_files)
        if import_result.get("status") == "error":
            await event_callback("compile_result", {
                "status": "error",
                "message": f"导入失败: {import_result.get('error', '')}",
            })
            return

        for round_num in range(max_rounds):
            # 编译
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

            # 有错误 → 通知前端
            await event_callback("compile_result", {
                "status": "error",
                "message": f"编译发现{len(errors)}个错误",
                "errors": errors[:10],  # 最多显示10个
                "round": round_num + 1,
            })

            # 自动修复
            await event_callback("phase_start", {
                "phase": "debugging",
                "message": f"正在自动修复编译错误 (第{round_num + 1}轮)...",
            })

            # 将错误转为error_log，调用debugger
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

            # 将修复后的代码重新导入
            updated_files = state.get("code_generated", {})
            updated_cs = {k: v for k, v in updated_files.items() if k.endswith(".cs")}
            if updated_cs != cs_files:
                cs_files = updated_cs
                await client.import_files(cs_files)

        # 超过最大轮数
        await event_callback("compile_result", {
            "status": "partial",
            "message": f"经过{max_rounds}轮修复仍有编译错误，可能需要人工介入",
        })

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

            # 保存编译结果（auto_build=true时）
            if result.get("compile_errors"):
                state.setdefault("scene_compile_errors", []).extend(result["compile_errors"])

            # 始终将场景描述JSON纳入code_generated输出
            scene_desc = result.get("scene_description")
            if scene_desc:
                import json
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

    def _sanitize_scene_scripts(self, state: GameDevState) -> None:
        """清理场景描述中引用的不存在脚本，替换为Unity内置组件或移除"""
        import re as _re
        scene_desc = state.get("scene_description")
        if not scene_desc:
            return

        code_generated = state.get("code_generated", {})
        # 提取所有生成的类名
        generated_classes = set()
        for fpath, content in code_generated.items():
            if fpath.endswith(".cs"):
                m = _re.search(r'public\s+(?:partial\s+)?(?:class|struct|interface)\s+(\w+)', content)
                if m:
                    generated_classes.add(m.group(1))

        from src.utils.consistency_validator import _is_unity_builtin
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
        """生成完整项目产物（共享逻辑，run() 和 run_with_streaming() 都调用）

        生成：GameDesignModel.json, CodeMetadata.json, ValidationReport.json,
              scene_description.json, README_Unity.md, ProjectSettings_Suggestions.md,
              GameForgeHttpServer.cs
        """
        import json as _json

        code_generated = state.setdefault("code_generated", {})

        # 清理场景中引用的不存在脚本
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

        # scene_description.json（使用清理后的版本）
        scene_desc = state.get("scene_description")
        if scene_desc:
            code_generated["Assets/Scenes/scene_description.json"] = _json.dumps(scene_desc, indent=2, ensure_ascii=False)

        # GameDesignModel.json — 完整游戏设计模型
        if "Assets/GameDesignModel.json" not in code_generated:
            full_gdm = state.get("game_design_model", {})
            full_gdm["_meta"] = {
                "project_name": state.get("project_context", {}).get("project_name", "GameForge"),
                "engine": state.get("project_context", {}).get("engine", "unity"),
                "task_count": len(state.get("task_plan", [])),
                "scene_status": state.get("scene_status", "pending"),
                "generated_at": __import__("datetime").datetime.now().isoformat(),
            }
            code_generated["Assets/GameDesignModel.json"] = _json.dumps(full_gdm, indent=2, ensure_ascii=False)

        # CodeMetadata.json
        if "Assets/CodeMetadata.json" not in code_generated:
            file_metadata = state.get("file_metadata", {})
            cm = {
                "files": list(code_generated.keys()),
                "file_metadata": file_metadata,
                "total_files": len(code_generated),
                "cs_files": len([f for f in code_generated if f.endswith(".cs")]),
            }
            code_generated["Assets/CodeMetadata.json"] = _json.dumps(cm, indent=2, ensure_ascii=False)

        # ValidationReport.json
        if "Assets/ValidationReport.json" not in code_generated:
            validation_result = state.get("validation_result")
            if validation_result:
                code_generated["Assets/ValidationReport.json"] = _json.dumps(validation_result, indent=2, ensure_ascii=False)

        # Unity Editor HTTP Server 插件（始终输出）
        if "Assets/Editor/GameForgeHttpServer.cs" not in code_generated:
            template_path = Path(__file__).parent.parent.parent.parent / "config" / "templates" / "unity" / "GameForgeHttpServer.cs.template"
            if template_path.exists():
                plugin_content = template_path.read_text(encoding="utf-8")
                code_generated["Assets/Editor/GameForgeHttpServer.cs"] = plugin_content

        # 生成完整 Unity 项目模板（Packages/manifest.json, ProjectSettings/*.asset, .meta）
        try:
            from src.engine.unity.project_generator import UnityProjectGenerator
            project_files = UnityProjectGenerator().generate_all(state)
            for path, content in project_files.items():
                if path not in code_generated:
                    code_generated[path] = content
        except Exception as e:
            state.setdefault("warnings", []).append(f"Unity项目模板生成失败: {e}")

    async def run(self, input_state: Dict[str, Any]) -> Dict[str, Any]:
        """运行工作流

        Args:
            input_state: 初始状态

        Returns:
            最终状态
        """
        initial_state: GameDevState = {
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

        # 生成场景描述（如果配置允许）
        async def record_scene_event(event_type: str, data: dict):
            if event_type == "scene_error":
                message = data.get("message", "Scene generation failed")
                initial_state["scene_error"] = message
                initial_state["scene_status"] = "error"
                initial_state.setdefault("warnings", []).append(message)
                initial_state.setdefault("error_log", []).append(message)
            elif event_type == "scene_skipped":
                message = data.get("message", "Scene generation skipped")
                initial_state.setdefault("warnings", []).append(message)

        scene_task = asyncio.create_task(
            self._run_scene_generation(initial_state, record_scene_event)
        )

        final_state = await self._run_with_parallel_support(initial_state)

        # 等待场景生成完成
        try:
            await asyncio.wait_for(scene_task, timeout=60)
        except asyncio.TimeoutError:
            final_state["scene_status"] = "error"
            final_state["scene_error"] = "Scene generation timed out"
            final_state.setdefault("warnings", []).append(final_state["scene_error"])
            final_state.setdefault("error_log", []).append(final_state["scene_error"])
        except Exception as e:
            final_state["scene_status"] = "error"
            final_state["scene_error"] = str(e)
            final_state.setdefault("warnings", []).append(f"Scene generation failed: {e}")
            final_state.setdefault("error_log", []).append(f"Scene generation failed: {e}")

        # 场景脚本清理（在一致性校验之前）
        self._sanitize_scene_scripts(final_state)

        # 一致性校验
        try:
            from src.utils.consistency_validator import validate_code_scene_consistency
            scene_desc = final_state.get("scene_description", {})
            if scene_desc:
                consistency = validate_code_scene_consistency(
                    code_files=final_state.get("code_generated", {}),
                    scene_desc=scene_desc,
                    gdm=final_state.get("game_design_model"),
                    file_metadata=final_state.get("file_metadata"),
                )
                final_state["validation_result"] = consistency.to_dict()
        except Exception:
            pass

        # 生成完整项目产物
        self._add_project_artifacts(final_state)
        return final_state

    async def run_with_streaming(
        self, input_state: Dict[str, Any], event_callback
    ) -> Dict[str, Any]:
        """运行工作流（带SSE事件回调）

        Args:
            input_state: 初始状态
            event_callback: 异步回调函数 async def callback(event_type: str, data: dict)

        Returns:
            最终状态
        """
        initial_state: GameDevState = {
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

        state = initial_state

        try:
            # Phase 0: 游戏设计 — 生成 Game Design Model
            await event_callback("phase_start", {"phase": "designing", "message": "正在分析需求，设计游戏整体结构..."})
            design_result = await self._game_designer_node(state)
            state.update(design_result)
            gdm = state.get("game_design_model", {})
            await event_callback("game_design", {
                "phase": "design_complete",
                "game_title": gdm.get("game_title", ""),
                "genre": gdm.get("genre", ""),
                "camera_mode": gdm.get("camera_mode", ""),
                "systems": [s.get("name", "") for s in gdm.get("main_systems", [])],
                "entities": [e.get("name", "") for e in gdm.get("entities", [])],
                "message": f"游戏设计完成：{gdm.get('game_title', '未命名')}，包含 {len(gdm.get('main_systems', []))} 个系统，{len(gdm.get('entities', []))} 个实体",
            })

            # Phase 1: 规划
            await event_callback("phase_start", {"phase": "planning", "message": "正在根据游戏设计生成任务计划..."})
            plan_result = await self._planner_node(state)
            state.update(plan_result)
            await event_callback("task_plan", {
                "phase": "planning_complete",
                "tasks": [
                    {"id": t.get("id"), "name": t.get("name"), "description": t.get("description")}
                    for t in state.get("task_plan", [])
                ],
                "message": f"任务计划生成完成，共{len(state.get('task_plan', []))}个任务",
            })

            # 并行启动场景生成（不阻塞代码生成）
            scene_task = asyncio.create_task(
                self._run_scene_generation(state, event_callback)
            )

            max_iterations = self.config.get("agents", {}).get("orchestrator", {}).get("max_iterations", 10)

            for iteration in range(max_iterations):
                orch_result = await self._orchestrator_node(state)
                state.update(orch_result)

                if state.get("is_complete") or state.get("current_phase") == "workflow_complete":
                    break

                if state.get("current_phase") in ("error", "needs_fix"):
                    await event_callback("phase_start", {"phase": "debugging", "message": "检测到错误，正在自动修复..."})
                    debug_result = await self._debugger_node(state)
                    state.update(debug_result)
                    continue

                ready_ids = state.get("ready_task_ids", [])
                task_plan = state.get("task_plan", [])
                ready_tasks = [t for t in task_plan if t.get("id") in ready_ids]

                if len(ready_tasks) > 1:
                    # 多个独立任务 → 并行执行
                    await event_callback("phase_start", {
                        "phase": "generating",
                        "message": f"正在并行生成{len(ready_tasks)}个任务...",
                    })
                    # 记录已发送的文件
                    sent_files = set(state.get("code_generated", {}).keys())
                    parallel_result = await self._execute_tasks_parallel(state, ready_tasks)
                    state.update(parallel_result)

                    # 只发送新生成的文件
                    for file_path, content in state.get("code_generated", {}).items():
                        if file_path not in sent_files:
                            await event_callback("code_file", {
                                "file_path": file_path,
                                "content": content,
                            })

                    # 并行执行审查和测试生成
                    review_result, test_result = await asyncio.gather(
                        self._code_reviewer_node(state),
                        self._test_generator_node(state),
                    )
                    state.update(review_result)
                    state.update(test_result)

                    # 发送审查结果事件
                    if review_result.get("review_result"):
                        await event_callback("review_result", review_result["review_result"])

                    # 发送测试生成的文件
                    for file_path, content in state.get("code_generated", {}).items():
                        if file_path not in sent_files:
                            await event_callback("code_file", {
                                "file_path": file_path,
                                "content": content,
                            })

                elif ready_tasks:
                    task = ready_tasks[0]
                    state["current_task_id"] = task.get("id")
                    task_type = task.get("type", TaskType.CODE.value)
                    await event_callback("phase_start", {
                        "phase": "generating",
                        "message": f"正在生成: {task.get('name', '')}...",
                    })

                    sent_files = set(state.get("code_generated", {}).keys())

                    if task_type == TaskType.TEST.value:
                        # 测试任务 → 直接生成测试
                        test_result = await self._test_generator_node(state)
                        state.update(test_result)
                    else:
                        # 代码任务 → 生成 + 审查 + 测试（并行）
                        gen_result = await self._code_generator_node(state)
                        state.update(gen_result)

                        # 如果代码生成失败，标记任务完成避免死循环
                        error_log = state.get("error_log", [])
                        if error_log:
                            task_plan = state.get("task_plan", [])
                            for t in task_plan:
                                if t.get("id") == task.get("id"):
                                    t["status"] = TaskStatus.COMPLETED.value
                                    break
                            state["task_plan"] = task_plan
                            state["error_log"] = []
                            continue

                        # 并行执行审查和测试
                        review_result, test_result = await asyncio.gather(
                            self._code_reviewer_node(state),
                            self._test_generator_node(state),
                        )
                        state.update(review_result)
                        state.update(test_result)

                        # 发送审查结果事件
                        if review_result.get("review_result"):
                            await event_callback("review_result", review_result["review_result"])

                    # 只发送新生成的文件
                    for file_path, content in state.get("code_generated", {}).items():
                        if file_path not in sent_files:
                            await event_callback("code_file", {
                                "file_path": file_path,
                                "content": content,
                            })
                else:
                    break

            # 等待场景生成完成（如果还没完成的话）
            try:
                await asyncio.wait_for(scene_task, timeout=60)
            except asyncio.TimeoutError:
                state.setdefault("warnings", []).append("场景生成超时")
                await event_callback("scene_error", {"message": "场景生成超时"})
            except Exception as e:
                state.setdefault("warnings", []).append(f"场景生成异常: {e}")

            # Unity编译闭环（仅在 auto_build_scene=true 时执行）
            unity_config = self.config.get("unity", {})
            auto_build = unity_config.get("auto_build_scene", False)
            auto_compile = unity_config.get("auto_compile", False)
            if auto_build or auto_compile:
                try:
                    await self._unity_compile_loop(state, event_callback, max_rounds=3)
                except Exception as e:
                    state.setdefault("warnings", []).append(f"Unity编译闭环异常: {e}")
                    await event_callback("warning", {"message": f"编译闭环跳过: {e}"})

            # 代码静态校验
            try:
                from src.utils.code_validator import validate_unity_code
                validation = validate_unity_code(state.get("code_generated", {}))
                if validation.has_issues:
                    for err in validation.errors[:5]:
                        state.setdefault("warnings", []).append(f"校验错误: {err['file']}:{err['line']} — {err['message']}")
                    for warn in validation.warnings[:5]:
                        state.setdefault("warnings", []).append(f"校验警告: {warn['file']}:{warn['line']} — {warn['message']}")
                    await event_callback("warning", {
                        "message": f"代码校验发现 {validation.to_dict()['error_count']} 个错误, {validation.to_dict()['warning_count']} 个警告",
                        "validation": validation.to_dict(),
                    })
            except Exception as e:
                state.setdefault("warnings", []).append(f"代码校验异常: {e}")

            # 场景脚本清理（在一致性校验之前）
            self._sanitize_scene_scripts(state)

            # 代码与场景一致性校验
            try:
                from src.utils.consistency_validator import validate_code_scene_consistency
                scene_desc = state.get("scene_description", {})
                if scene_desc:
                    consistency = validate_code_scene_consistency(
                        code_files=state.get("code_generated", {}),
                        scene_desc=scene_desc,
                        gdm=state.get("game_design_model"),
                        file_metadata=state.get("file_metadata"),
                    )
                    state["validation_result"] = consistency.to_dict()
                    if consistency.has_errors:
                        for err in consistency.errors[:5]:
                            state.setdefault("warnings", []).append(f"一致性错误: {err['message']}")
                    if consistency.warnings:
                        for warn in consistency.warnings[:5]:
                            state.setdefault("warnings", []).append(f"一致性警告: {warn['message']}")
                    if consistency.has_issues:
                        await event_callback("warning", {
                            "message": f"一致性校验发现 {len(consistency.errors)} 个错误, {len(consistency.warnings)} 个警告",
                            "validation": consistency.to_dict(),
                        })
            except Exception as e:
                state.setdefault("warnings", []).append(f"一致性校验异常: {e}")

            # 生成完整项目产物（共享逻辑）
            self._add_project_artifacts(state)

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

        except Exception as e:
            await event_callback("error", {"message": f"生成过程出错: {str(e)}"})

        return state


def create_workflow(config: Dict[str, Any]) -> GameDevWorkflow:
    """创建工作流实例

    Args:
        config: 配置信息

    Returns:
        工作流实例
    """
    return GameDevWorkflow(config)
