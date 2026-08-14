"""Godot 项目主审查 Agent。

对最终代码执行两轮审查，并对游戏设计模型与场景描述执行确定性的
通用游戏设计检查。该 Agent 不执行或写入用户代码，只产出审查报告。
"""

import json
from typing import Any, Dict, List

from src.agents.base import BaseAgent
from src.core.state.game_state import AgentType, GameDevState
from src.utils.llm_client import get_llm_client


class MainReviewerAgent(BaseAgent):
    """主审查 Agent：review -> re-review -> design review。"""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(AgentType.MAIN_REVIEWER, config)
        try:
            self.llm = get_llm_client(config, provider=self.provider, model=self.model)
        except Exception:
            # 主审查以确定性审查为兜底，LLM 不可用时不影响主流程
            self.llm = None

    async def execute(self, state: GameDevState, **kwargs) -> Dict[str, Any]:
        first_review = self.review(state)
        # 第二轮审查以第一轮发现的问题为输入，确保每个问题都有复核结论。
        second_review = self.rereview(state, first_review)
        design_review = self.review_game_design(state)

        # 可选 LLM 深度审查（spec §6）：不阻断既有确定性审查，失败则跳过。
        llm_review = await self._llm_review(state)
        llm_blocking = llm_review.get("blocking_issues") or []
        llm_warnings = llm_review.get("warnings") or []

        passed = second_review["passed"] and design_review["passed"]
        if llm_blocking:
            passed = False

        all_warnings = (
            first_review["warnings"]
            + second_review["warnings"]
            + design_review["warnings"]
            + [f"LLM审查: {w}" for w in llm_warnings]
        )

        main_review_result = {
            "passed": passed,
            "first_review": first_review,
            "rereview": second_review,
        }
        if llm_review:
            main_review_result["llm_review"] = llm_review

        return {
            "main_review_result": main_review_result,
            "design_review_result": design_review,
            "current_phase": "main_review_passed" if passed else "main_review_failed",
            "warnings": all_warnings,
        }

    async def _llm_review(self, state: GameDevState) -> Dict[str, Any]:
        """可选的 LLM 深度审查，产出 spec §6 的 JSON 字段；不可用或失败返回 {}。"""
        if self.llm is None:
            return {}
        system_prompt = self.get_prompt_template("main_reviewer_system")
        if not system_prompt:
            return {}
        try:
            code = state.get("code_generated", {})
            code_context = "\n\n".join(
                f"### {p}\n```\n{c[:2000]}\n```"
                for p, c in code.items()
                if p.endswith((".gd", ".tscn", ".tres"))
            )
            gdm = state.get("game_design_model") or {}
            scene = state.get("scene_description") or {}
            user_prompt = (
                "请依据主审查规范审查以下 Godot 工程。\n\n"
                f"## 游戏设计模型（GameSpec）\n{json.dumps(gdm, ensure_ascii=False, indent=2)[:1500]}\n\n"
                f"## 场景描述（Scene IR）\n{json.dumps(scene, ensure_ascii=False, indent=2)[:1500]}\n\n"
                f"## 生成产物\n{code_context[:8000]}\n\n只输出符合规范的 JSON。"
            )
            result = await self.llm.chat_json(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                model=self.model,
                temperature=self.llm_config.get("temperature", 0.2),
                max_tokens=self.llm_config.get("max_tokens", 4096),
            )
            if isinstance(result, dict) and not result.get("parse_error"):
                return result
            return {}
        except Exception as e:
            self.log_error("main_reviewer_llm_error", {"error": str(e)})
            return {}

    def review(self, state: GameDevState) -> Dict[str, Any]:
        warnings: List[str] = []
        code = state.get("code_generated", {})
        if not code:
            warnings.append("未发现可审查的 Godot 代码或场景文件")
        for path, content in code.items():
            if not (path.endswith(".gd") or path.endswith(".tscn") or path.endswith(".tres")):
                warnings.append(f"非 Godot 产物未纳入主审查: {path}")
            if path.endswith(".gd") and ("TODO" in content or "pass\n" in content):
                warnings.append(f"{path} 仍包含未完成实现标记")
        return {"passed": not any("未完成" in item for item in warnings), "warnings": warnings}

    def rereview(self, state: GameDevState, first_review: Dict[str, Any]) -> Dict[str, Any]:
        warnings = list(first_review.get("warnings", []))
        unresolved = [item for item in warnings if "未完成" in item]
        return {"passed": not unresolved, "warnings": [f"复审: {item}" for item in unresolved]}

    def review_game_design(self, state: GameDevState) -> Dict[str, Any]:
        gdm = state.get("game_design_model") or {}
        scene = state.get("scene_description") or {}
        warnings: List[str] = []
        required = {
            "genre": "游戏类型",
            "core_loop": "核心循环",
            "player_actions": "玩家动作",
            "win_conditions": "胜利条件",
            "fail_conditions": "失败条件",
        }
        for key, label in required.items():
            value = gdm.get(key)
            if not value:
                warnings.append(f"设计缺少{label}，无法验证核心玩法闭环")
        entities = gdm.get("entities", []) or scene.get("game_objects", [])
        if not entities:
            warnings.append("场景没有人物、敌人、道具或环境实体")
        if entities and not any(self._entity_has_role(entity, ("player", "hero", "character")) for entity in entities):
            warnings.append("场景未明确玩家角色")
        if entities and not any(self._entity_has_role(entity, ("environment", "ground", "platform", "level")) for entity in entities):
            warnings.append("场景未明确环境、地面或关卡承载元素")
        if gdm.get("win_conditions") and not gdm.get("fail_conditions"):
            warnings.append("只有胜利条件，没有失败或风险反馈")
        return {"passed": not warnings, "warnings": warnings, "checked": ["core_loop", "characters", "environment", "win_fail_balance"]}

    @staticmethod
    def _entity_has_role(entity: Any, roles: tuple) -> bool:
        if not isinstance(entity, dict):
            return False
        text = " ".join(str(entity.get(key, "")) for key in ("name", "role", "type", "description")).lower()
        return any(role in text for role in roles)
