"""GameForge - 代码审查Agent模块

负责审查生成的代码质量。
"""

import asyncio
from typing import Any, Dict
from src.agents.base import BaseAgent
from src.core.state.game_state import GameDevState, AgentType
from src.core.dialogue import TERMINATE
from src.utils.llm_client import get_llm_client


class CodeReviewerAgent(BaseAgent):
    """代码审查Agent"""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(AgentType.CODE_REVIEWER, config)
        self.llm = get_llm_client(config, provider=self.provider, model=self.model)

    async def execute(self, state: GameDevState, **kwargs) -> Dict[str, Any]:
        self.log_action("code_reviewer_execute")
        review_result = await self.review(state)
        passed = review_result.get("passed", False)
        status = review_result.get("status", "")
        if status == "review_unavailable":
            phase = "code_review_unavailable"
        elif passed:
            phase = "code_review_passed"
        else:
            phase = "code_review_failed"
        return {
            "current_phase": phase,
            "review_result": review_result,
        }

    async def review(self, state: GameDevState) -> Dict[str, Any]:
        self.log_action("review_code")

        code_generated = state.get("code_generated", {})
        if not code_generated:
            return {"passed": False, "score": 0, "issues": ["No code to review"]}

        # 快速模式：跳过 LLM 调用，直接返回通过（节省 ~30s/任务）
        fast_mode = self.llm_config.get("fast_review", True)
        if fast_mode:
            self.log_action("review_skipped_fast_mode")
            return {"score": 85, "passed": True, "issues": [], "suggestions": [], "status": "skipped"}

        code_context = ""
        total_chars = 0
        max_chars = 12000  # 限制代码上下文大小，防止 LLM 超时
        for path, content in code_generated.items():
            if path.endswith(".cs") and not path.endswith("Tests.cs"):
                if total_chars + len(content) > max_chars:
                    code_context += f"\n### {path}\n(truncated — too large for review)\n"
                    continue
                code_context += f"\n### {path}\n```csharp\n{content}\n```\n"
                total_chars += len(content)

        if not code_context:
            return {"score": 100, "passed": True, "issues": [], "suggestions": []}

        system_prompt = self.get_prompt_template("reviewer_system")
        project_context = state.get("project_context", {})
        requirements = project_context.get("requirements", "")
        gdm = state.get("game_design_model") or {}
        requirement_context = f"""
## 原始用户需求（最高优先级）
{requirements}

## 游戏设计锚点
- 类型: {gdm.get('genre', '')}
- 核心循环: {gdm.get('core_loop', '')}
- 玩家动作: {', '.join(gdm.get('player_actions', []))}
- 胜利条件: {', '.join(gdm.get('win_conditions', []))}
- 失败条件: {', '.join(gdm.get('fail_conditions', []))}

审查时必须判断代码是否覆盖用户明确提出的玩法、输入、敌人、道具、UI、胜负条件和场景元素。遗漏需求属于 logic 问题；如果遗漏会导致玩家无法体验核心玩法，severity 设为 high。
"""
        user_prompt = f"""请审查以下Unity C#代码，给出评分和改进建议。

{requirement_context}
{code_context}

请以JSON格式输出审查结果：
{{
    "score": 85,
    "passed": true,
    "issues": [
        {{
            "type": "performance|security|style|logic",
            "severity": "high|medium|low",
            "file": "文件路径",
            "line": 10,
            "message": "问题描述",
            "suggestion": "修复建议"
        }}
    ],
    "suggestions": ["改进建议1", "改进建议2"]
}}"""

        try:
            # 30秒超时保护，防止 LLM 卡住
            result = await asyncio.wait_for(
                self.llm.chat_json(
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    model=self.model,
                    temperature=self.llm_config.get("temperature", 0.2),
                    max_tokens=self.llm_config.get("max_tokens", 4096),
                ),
                timeout=30,
            )

            if result.get("parse_error"):
                self.log_error("review_parse_error")
                return {"score": 0, "issues": [], "passed": False, "status": "review_unavailable", "note": "Review parsing failed"}

            self.log_action("review_complete", {"score": result.get("score", 0)})
            return result

        except asyncio.TimeoutError:
            self.log_error("reviewer_timeout")
            return {"score": 0, "issues": [], "passed": False, "status": "review_unavailable", "note": "Review timed out (30s)"}
        except Exception as e:
            self.log_error("reviewer_llm_error", {"error": str(e)})
            return {"score": 0, "issues": [], "passed": False, "status": "review_unavailable", "note": f"Review error: {e}"}

    def summarize_issues(self, review_result: Dict[str, Any]) -> str:
        """把结构化审查结果转成一段自然语言「发言」（用于对话 dyad 的指导者侧）。

        Args:
            review_result: ``review()`` 的返回

        Returns:
            可读的审查意见文本（含终止符提示）
        """
        status = review_result.get("status")
        if status == "skipped":
            return "【审查】快速模式已跳过，默认通过，无需重构方处理。"
        if status == "review_unavailable":
            return "【审查】无法完成审查（超时/解析失败），交由后续环节兜底。"

        issues = review_result.get("issues") or []
        score = review_result.get("score", 0)
        if not issues:
            return f"【审查】评分 {score}，未发现遗留问题，建议通过。{TERMINATE}"

        lines = [f"【审查】评分 {score}，发现 {len(issues)} 项问题，请重构方处理："]
        for i in issues:
            sev = i.get("severity", "")
            f = i.get("file", "")
            ln = i.get("line", "")
            msg = i.get("message", "")
            lines.append(f"  - [{sev}] {f}:{ln} {msg}")
        return "\n".join(lines)
