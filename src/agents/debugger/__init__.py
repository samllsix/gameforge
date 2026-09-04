"""GameForge - 调试Agent模块（适配层）

Phase 1 精简：debugger 的 LLM 修复职能已并入 code_generator.fix_code()，
本模块保留 DebuggerAgent 类名与接口（analyze_and_fix / execute / fix /
delegate_to_research），内部委托 code_generator 执行，
避免改动 workflow 中 4 处编译/冒烟闭环的调用点。
"""

from typing import Any, Dict, List

from src.agents.base import BaseAgent
from src.agents.code_generator import CodeGeneratorAgent
from src.core.state.game_state import GameDevState, AgentType


class DebuggerAgent(BaseAgent):
    """调试Agent（适配层）— 修复逻辑由 code_generator 承担"""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(AgentType.DEBUGGER, config)
        self._fixer = CodeGeneratorAgent(config)
        self.max_fix_attempts = self.agent_config.get("max_fix_attempts", 5)
        self.enable_delegation = self.agent_config.get("enable_delegation", True)

    def delegate_to_research(self, error: str, top_k: int = 3) -> Dict[str, Any]:
        """遇到陌生报错时，委派一个临时调研子 agent 查 Godot 知识库并回收结论。"""
        if not self.enable_delegation or not error:
            return {"delegated": False, "query": error, "findings": [], "summary": "委派已禁用或未提供错误"}

        from src.core.knowledge.lookup import lookup_godot_knowledge

        findings = lookup_godot_knowledge(error, top_k=top_k)
        summary = (
            f"调研子 agent 从 Godot 知识库检索到 {len(findings)} 条相关知识"
            if findings
            else "知识库中未找到直接匹配，建议结合 Godot 官方文档进一步排查"
        )
        return {
            "delegated": True,
            "query": error,
            "findings": [
                {
                    "title": f.get("title", ""),
                    "content": (f.get("content") or f.get("description") or "")[:300],
                    "tags": f.get("tags", []),
                }
                for f in findings
            ],
            "summary": summary,
        }

    async def execute(self, state: GameDevState, **kwargs) -> Dict[str, Any]:
        self.log_action("debugger_execute")

        error_log = kwargs.get("error_log", state.get("error_log", []))
        fix_result = await self.analyze_and_fix(state, error_log)

        return {
            **fix_result,
            "current_phase": "fix_applied",
            "error_log": [],
        }

    async def analyze_and_fix(self, state: GameDevState, error_log: List[str]) -> Dict[str, Any]:
        """分析错误并生成修复（委托 code_generator.fix_code）"""
        self.log_action("analyze_and_fix")
        return await self._fixer.fix_code(state, error_log)

    async def fix(self, state: GameDevState) -> Dict[str, Any]:
        """兼容旧接口"""
        error_log = state.get("error_log", [])
        return await self.analyze_and_fix(state, error_log)
