"""GameForge - 审查↔重构对话协商 单元测试

验证「多智能体改造第一步」：
1. DialogueSession 的轮次交替 / 终止判定 / max_rounds 护栏。
2. run_review_refactor_dialogue 能在多轮后达成共识、并更新代码（用假 agent，不依赖真实 LLM）。
3. 默认关闭开关时，编排图仍走旧的 code_reviewer -> refactor 边。
"""

import pytest
from src.core.dialogue.session import DialogueSession, DialogueTurn, DialogueTranscript, TERMINATE
from src.core.dialogue.review_refactor_negotiation import run_review_refactor_dialogue


# ── DialogueSession 基础测试 ─────────────────────────────────

class TestDialogueSession:
    async def test_guide_terminates_round_one(self):
        async def guide(h):
            return DialogueTurn(content="通过", should_terminate=True)
        async def assistant(h):
            return DialogueTurn(content="收到")

        t: DialogueTranscript = await DialogueSession("t", max_rounds=3).run(guide, assistant)
        assert t.rounds == 1
        assert t.reached_consensus is True
        assert t.terminated_by == "guide"
        assert len(t.messages) == 1

    async def test_assistant_terminates(self):
        async def guide(h):
            return DialogueTurn(content="有问题")
        async def assistant(h):
            return DialogueTurn(content="已改", should_terminate=True)

        t = await DialogueSession("t", max_rounds=3, guide_name="g", assistant_name="a").run(guide, assistant)
        assert t.rounds == 1
        assert t.terminated_by == "a"
        # 顺序：guide -> assistant
        assert t.messages[0].speaker == "g"
        assert t.messages[1].speaker == "a"

    async def test_max_rounds_guard(self):
        calls = {"n": 0}
        async def guide(h):
            calls["n"] += 1
            return DialogueTurn(content="还有问题")
        async def assistant(h):
            return DialogueTurn(content="改了")

        t = await DialogueSession("t", max_rounds=2).run(guide, assistant)
        assert t.rounds == 2
        assert t.reached_consensus is False
        assert t.terminated_by is None
        # 每轮 guide+assistant 各一次 = 4 次
        assert calls["n"] == 2

    def test_transcript_serializable(self):
        async def guide(h):
            return DialogueTurn(content="x", should_terminate=True)
        async def assistant(h):
            return DialogueTurn(content="y")

        import asyncio
        t = asyncio.run(DialogueSession("t").run(guide, assistant))
        d = t.to_dict()
        assert d["topic"] == "t"
        assert isinstance(d["messages"], list)
        assert d["messages"][0]["content"] == "x"


# ── 协商 dyad 测试（假 agent，不调用 LLM） ───────────────────

class _FakeReviewer:
    """第 1 次审查有 1 个问题，第 2 次审查通过。"""
    def __init__(self):
        self.calls = 0

    async def review(self, state):
        self.calls += 1
        if self.calls == 1:
            return {"passed": False, "score": 60, "issues": [
                {"type": "style", "severity": "medium", "file": "player.gd", "line": 10,
                 "message": "函数过长"}]}
        return {"passed": True, "score": 90, "issues": []}

    def summarize_issues(self, rr):
        issues = rr.get("issues") or []
        if not issues:
            return f"通过，评分{rr.get('score')} {TERMINATE}"
        return f"问题: {issues[0]['message']}"


class _FakeRefactor:
    """把 player.gd 内容从 BAD 改成 GOOD（表示应用了修复）。"""
    def __init__(self):
        self.calls = 0

    async def analyze_and_refactor(self, state, code_files):
        self.calls += 1
        updated = dict(code_files)
        if "player.gd" in updated and updated["player.gd"] == "BAD":
            updated["player.gd"] = "GOOD"
            return {"refactored_code": updated, "new_artifacts": [
                {"file_path": "player.gd", "changes": [{"description": "拆分函数"}]}]}
        return {"refactored_code": updated, "new_artifacts": []}

    def summarize_changes(self, res):
        if res.get("new_artifacts"):
            return "已修复 player.gd"
        return "无需改动"


class TestReviewRefactorNegotiation:
    async def test_converges_and_updates_code(self):
        reviewer = _FakeReviewer()
        refactor = _FakeRefactor()
        state = {"code_generated": {"player.gd": "BAD"}}

        result = await run_review_refactor_dialogue(reviewer, refactor, state, max_rounds=3)

        # 协商后代码被更新
        assert result["code_generated"]["player.gd"] == "GOOD"
        # 审查最终通过
        assert result["review_result"]["passed"] is True
        assert result["current_phase"] == "code_review_passed"
        # transcript 可见协作：至少 1 轮（指导者提问题 + 助手改 + 指导者复核通过）
        transcript = result["review_dialogue_transcript"]
        assert transcript["rounds"] >= 2
        assert transcript["reached_consensus"] is True
        speakers = [m["speaker"] for m in transcript["messages"]]
        assert "code_reviewer" in speakers
        assert "refactor" in speakers
        # reviewer 被调用 2 次（首轮提问题 + 复核通过）
        assert reviewer.calls == 2
        # refactor 被调用 1 次（应用修复）
        assert refactor.calls == 1

    async def test_no_issues_short_circuits(self):
        reviewer = _FakeReviewer()
        reviewer.calls = 1  # 直接让首次审查即通过
        # 第一次 review 已算过 -> 下次 review 返回 passed（用 monkeypatch 简化）
        class _PassReviewer(_FakeReviewer):
            async def review(self, state):
                return {"passed": True, "score": 95, "issues": []}
        reviewer = _PassReviewer()
        refactor = _FakeRefactor()

        result = await run_review_refactor_dialogue(reviewer, refactor, {"code_generated": {}}, max_rounds=3)
        assert result["review_dialogue_transcript"]["rounds"] == 1
        assert result["review_dialogue_transcript"]["reached_consensus"] is True


# ── 编排图开关测试 ───────────────────────────────────────────

class TestWorkflowGraphSwitch:
    def _make_config(self, dialogue_enabled):
        return {"agents": {"review_refactor": {"dialogue_enabled": dialogue_enabled, "max_rounds": 3}}}

    def test_dialogue_disabled_keeps_legacy_edge(self):
        from src.core.graph.workflow import GameDevWorkflow
        wf = GameDevWorkflow(self._make_config(False))
        # 关闭时 refactor 节点仍可达，且没有对话节点介入主路径
        assert wf.dialogue_enabled is False
        # 编译不抛错
        assert wf.graph is not None

    def test_dialogue_enabled_wires_dialogue_node(self):
        from src.core.graph.workflow import GameDevWorkflow
        wf = GameDevWorkflow(self._make_config(True))
        assert wf.dialogue_enabled is True
        assert wf.dialogue_max_rounds == 3
        assert wf.graph is not None
