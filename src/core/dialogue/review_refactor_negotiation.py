"""GameForge - 审查↔重构 多轮协商（dyad）

把原本「code_reviewer 写问题、refactor 下一节点默默修」的单向交接，
升级为 ChatDev 式的双 agent 多轮对话：审查者(指导者)提意见，重构者(助手)回应/修改，
审查者再复核，直到达成共识(无遗留问题)或达到 max_rounds 强制收敛。

这是「让 agent 活过来」的第一步——协作由对话承载，且全程留痕(transcript)。
"""

from __future__ import annotations

from typing import Any, Dict

import structlog

from .session import DialogueSession, DialogueTurn

logger = structlog.get_logger()


async def run_review_refactor_dialogue(
    reviewer,
    refactor,
    state: Dict[str, Any],
    max_rounds: int = 3,
) -> Dict[str, Any]:
    """运行审查-重构协商 dyad。

    Args:
        reviewer: CodeReviewerAgent 实例（需有 ``review`` / ``summarize_issues``）
        refactor: RefactorAgent 实例（需有 ``analyze_and_refactor`` / ``summarize_changes``）
        state: 当前 GameDevState
        max_rounds: 最大协商轮次（护栏）

    Returns:
        可直接合并进 LangGraph state 的 dict：
        - current_phase
        - review_result（最终审查结果）
        - code_generated（协商后的代码，reducer 会自动合并）
        - review_dialogue_transcript（可见的协作证据）
    """
    # 工作副本：每轮重构后就地更新，供审查者下一轮复核
    working_code = dict(state.get("code_generated", {}))
    ws = {**state, "code_generated": working_code}

    last_review: Dict[str, Any] = {}

    async def guide_turn(history):
        # 指导者：复核当前代码，给出意见或宣布通过
        rr = await reviewer.review(ws)
        last_review.clear()
        last_review.update(rr)
        msg = reviewer.summarize_issues(rr)
        issues = rr.get("issues") or []
        passed = rr.get("passed", False)
        # 无遗留问题 → 达成共识，请求终止
        terminate = bool(passed) and not issues
        return DialogueTurn(content=msg, should_terminate=terminate, metadata={"review_result": rr})

    async def assistant_turn(history):
        # 助手：根据上一轮审查意见应用重构
        res = await refactor.analyze_and_refactor(ws, ws["code_generated"])
        updated = res.get("refactored_code", ws["code_generated"])
        ws["code_generated"] = updated
        msg = refactor.summarize_changes(res)
        return DialogueTurn(content=msg, should_terminate=False, metadata={"refactor_result": res})

    session = DialogueSession(
        topic="review_refactor",
        max_rounds=max_rounds,
        guide_name="code_reviewer",
        assistant_name="refactor",
    )
    transcript = await session.run(guide_turn, assistant_turn)

    final_review = last_review
    issues = final_review.get("issues") or []
    passed = final_review.get("passed", False)
    if bool(passed) and not issues:
        phase = "code_review_passed"
    else:
        phase = "code_reviewed"

    logger.info(
        "review_refactor_dialogue_done",
        rounds=transcript.rounds,
        consensus=transcript.reached_consensus,
        phase=phase,
    )

    return {
        "current_phase": phase,
        "review_result": final_review,
        "code_generated": ws["code_generated"],
        "review_dialogue_transcript": transcript.to_dict(),
    }
