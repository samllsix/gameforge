"""GameForge - 反思 Agent（多智能体改造第二步）

Reflector 在 main_reviewer 之后运行，读取全程状态/记忆，判断当前结果是
「代码级问题」（交给 debugger）还是「计划/设计级问题」（需要回头重规划）。
若判定需要重规划，则在 state 中标记 replan_needed，由编排者路由回 planner，
形成真正的「反思→改主意」回环（从瀑布变螺旋）。

默认 fast_reflect=True：用启发式规则判断，不调 LLM，便于离线测试与护栏。
"""

from typing import Any, Dict, List, Optional

from src.agents.base import BaseAgent
from src.core.state.game_state import GameDevState, AgentType


class ReflectorAgent(BaseAgent):
    """反思 Agent：对一次完整运行做复盘，决定是否需要重规划。"""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(AgentType.REFLECTOR, config)
        self.fast_reflect = self.agent_config.get("fast_reflect", True)
        self.max_recommendations = self.agent_config.get("max_recommendations", 5)

    async def execute(self, state: GameDevState, **kwargs) -> Dict[str, Any]:
        self.log_action("reflect_start")
        if self.fast_reflect:
            result = self._fast_reflect(state)
        else:
            result = await self._llm_reflect(state)
        self.log_action("reflect_done", {"verdict": result.get("verdict")})
        return result

    # ---------- fast（启发式，无 LLM） ----------

    def _fast_reflect(self, state: GameDevState) -> Dict[str, Any]:
        error_log: List[str] = state.get("error_log", []) or []
        warnings: List[str] = state.get("warnings", []) or []
        main_review: Dict[str, Any] = state.get("main_review_result") or {}
        design_review: Dict[str, Any] = state.get("design_review_result") or {}
        validation: Dict[str, Any] = state.get("validation_result") or {}

        main_failed = main_review.get("passed") is False
        design_failed = design_review.get("passed") is False
        validation_failed = bool(validation.get("has_errors")) if validation else False

        recommendations: List[str] = []
        if error_log:
            recommendations.append(
                f"存在 {len(error_log)} 条错误/异常，说明实现与设计目标有偏差，"
                "建议重新审视任务拆解与实现方案"
            )
        if main_failed:
            recommendations.append("主审查未通过，需修正设计或重排任务计划")
        if design_failed:
            recommendations.append("设计审查未通过（场景/人物/环境），建议回 designer 修正设计")
        if validation_failed:
            recommendations.append("统一校验发现结构性错误，建议回到 planner 重新拆解")
        if len(warnings) > 5:
            recommendations.append(f"警告过多（{len(warnings)} 条），建议拆分任务或补充编码规范")

        replan_needed = bool(error_log) or main_failed or design_failed or validation_failed or len(warnings) > 5
        if not recommendations:
            recommendations.append("整体通过，无需重规划")

        return {
            "verdict": "replan" if replan_needed else "ok",
            "replan_needed": replan_needed,
            "summary": "基于运行结果的快速反思（fast 模式，未调用 LLM）",
            "recommendations": recommendations[: self.max_recommendations],
            "reflection_mode": "fast",
            "metrics": {
                "errors": len(error_log),
                "warnings": len(warnings),
                "main_review_passed": main_review.get("passed"),
                "design_review_passed": design_review.get("passed"),
                "validation_has_errors": validation_failed,
            },
        }

    # ---------- LLM 模式（需要真实 provider） ----------

    async def _llm_reflect(self, state: GameDevState) -> Dict[str, Any]:
        from src.utils.llm_client import get_llm_client

        summary = self.format_state_summary(state)
        prompt = (
            "你是 GameForge 的复盘专家。请基于一次游戏代码生成运行的汇总，"
            "判断结果是否达到发布标准；如果未达标，判断问题是『代码级』"
            "（可由 debugger 修复）还是『计划/设计级』（需要回到 planner/designer 重做）。\n\n"
            f"运行状态：{summary}\n"
            f"错误日志：{state.get('error_log', [])}\n"
            f"主审查：{state.get('main_review_result', {})}\n\n"
            "请严格输出 JSON：{\"verdict\":\"ok|replan\",\"replan_needed\":bool,"
            "\"summary\":str,\"recommendations\":[str]}"
        )
        try:
            llm = get_llm_client(self.config, provider=self.provider, model=self.model)
            out = await llm.chat(
                [{"role": "user", "content": prompt}],
                model=self.model,
                temperature=0.2,
            )
            import json as _json

            text = out.get("content", "") if isinstance(out, dict) else str(out)
            data = _json.loads(_extract_json(text))
            data.setdefault("reflection_mode", "llm")
            data.setdefault("replan_needed", data.get("verdict") == "replan")
            return data
        except Exception as e:
            self.log_error("reflector_llm_failed", {"error": str(e)})
            return self._fast_reflect(state)


def _extract_json(text: str) -> str:
    """从 LLM 输出中提取 JSON 片段。"""
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start : end + 1]
    return text
