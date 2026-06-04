"""测试 RefactorAgent"""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from src.agents.refactor import RefactorAgent


@pytest.fixture
def agent(sample_config):
    return RefactorAgent(sample_config)


@pytest.fixture
def state_with_code():
    return {
        "code_generated": {
            "Assets/Scripts/Player/PlayerController.cs": """
public class PlayerController : MonoBehaviour
{
    public float speed = 10.0f;
    public float jumpForce = 5.0f;
    private Rigidbody2D rb;
    private bool isGrounded;

    void Start()
    {
        rb = GetComponent<Rigidbody2D>();
    }

    void Update()
    {
        float moveX = Input.GetAxisRaw("Horizontal");
        rb.velocity = new Vector2(moveX * speed, rb.velocity.y);

        if (Input.GetButtonDown("Jump") && isGrounded)
        {
            rb.AddForce(new Vector2(0f, jumpForce), ForceMode2D.Impulse);
        }
    }

    void OnCollisionEnter2D(Collision2D collision)
    {
        if (collision.gameObject.CompareTag("Ground"))
        {
            isGrounded = true;
        }
    }

    void OnCollisionExit2D(Collision2D collision)
    {
        if (collision.gameObject.CompareTag("Ground"))
        {
            isGrounded = false;
        }
    }
}
""",
            "Assets/Scripts/Game/GameManager.cs": """
public class GameManager : MonoBehaviour
{
    public static GameManager Instance;
    public int score;
    public int lives = 3;

    void Awake()
    {
        if (Instance == null)
        {
            Instance = this;
        }
        else
        {
            Destroy(gameObject);
        }
    }

    public void AddScore(int points)
    {
        score += points;
    }

    public void LoseLife()
    {
        lives--;
        if (lives <= 0)
        {
            GameOver();
        }
    }

    void GameOver()
    {
        Debug.Log("Game Over!");
    }
}
""",
        },
        "current_phase": "code_generated",
        "project_context": {"engine": "unity"},
    }


@pytest.fixture
def state_empty():
    return {
        "code_generated": {},
        "current_phase": "code_generated",
    }


class TestRefactorAgentExecute:
    """测试 execute 主入口"""

    @pytest.mark.asyncio
    async def test_execute_no_code_returns_refactored(self, agent, state_empty):
        """空代码输入 → 返回当前状态"""
        result = await agent.execute(state_empty)
        assert result["current_phase"] == "refactored"
        assert "code_generated" not in result

    @pytest.mark.asyncio
    async def test_execute_with_code_returns_refactored(self, agent, state_with_code):
        """有代码输入 → 返回重构结果"""
        mock_result = {
            "refactored_code": state_with_code["code_generated"],
            "new_artifacts": [],
        }
        with patch.object(agent, "analyze_and_refactor", new_callable=AsyncMock, return_value=mock_result):
            result = await agent.execute(state_with_code)
            assert result["current_phase"] == "refactored"
            assert "code_generated" in result

    @pytest.mark.asyncio
    async def test_execute_preserves_artifacts(self, agent, state_with_code):
        """保留已有的code_artifacts"""
        state_with_code["code_artifacts"] = [{"file": "existing.cs"}]
        mock_result = {
            "refactored_code": state_with_code["code_generated"],
            "new_artifacts": [{"file": "new.cs"}],
        }
        with patch.object(agent, "analyze_and_refactor", new_callable=AsyncMock, return_value=mock_result):
            result = await agent.execute(state_with_code)
            assert len(result["code_artifacts"]) == 2


class TestAnalyzeAndRefactor:
    """测试 analyze_and_refactor"""

    @pytest.mark.asyncio
    async def test_skips_non_cs_files(self, agent):
        """非.cs文件直接保留"""
        code_files = {
            "README.md": "# Hello",
            "config.json": '{"key": "value"}',
        }
        state = {"project_context": {"engine": "unity"}}
        result = await agent.analyze_and_refactor(state, code_files)
        assert result["refactored_code"]["README.md"] == "# Hello"
        assert result["refactored_code"]["config.json"] == '{"key": "value"}'
        assert len(result["new_artifacts"]) == 0

    @pytest.mark.asyncio
    async def test_processes_cs_files(self, agent, state_with_code):
        """处理.cs文件"""
        mock_file_result = {
            "content": "refactored code",
            "changes_made": True,
            "changes": [{"description": "extract method"}],
        }
        with patch.object(agent, "_refactor_file", new_callable=AsyncMock, return_value=mock_file_result):
            result = await agent.analyze_and_refactor(state_with_code, state_with_code["code_generated"])
            assert len(result["new_artifacts"]) == 2
            assert all(a.get("refactored") for a in result["new_artifacts"])

    @pytest.mark.asyncio
    async def test_no_changes_no_artifacts(self, agent, state_with_code):
        """无变更时不产生新artifacts"""
        mock_file_result = {
            "content": state_with_code["code_generated"]["Assets/Scripts/Player/PlayerController.cs"],
            "changes_made": False,
        }
        with patch.object(agent, "_refactor_file", new_callable=AsyncMock, return_value=mock_file_result):
            result = await agent.analyze_and_refactor(state_with_code, {"Assets/Scripts/Player/PlayerController.cs": "code"})
            assert len(result["new_artifacts"]) == 0


class TestRefactorFile:
    """测试 _refactor_file"""

    @pytest.mark.asyncio
    async def test_llm_parse_error_returns_original(self, agent):
        """LLM解析错误 → 返回原始代码"""
        state = {"project_context": {"engine": "unity"}}
        with patch.object(agent.llm, "chat_json", new_callable=AsyncMock, return_value={"parse_error": True}):
            result = await agent._refactor_file("test.cs", "original code", state)
            assert result["content"] == "original code"
            assert result["changes_made"] is False

    @pytest.mark.asyncio
    async def test_llm_no_refactoring_needed(self, agent):
        """LLM判断无需重构 → 返回原始代码"""
        state = {"project_context": {"engine": "unity"}}
        mock_result = {"needs_refactoring": False}
        with patch.object(agent.llm, "chat_json", new_callable=AsyncMock, return_value=mock_result):
            result = await agent._refactor_file("test.cs", "good code", state)
            assert result["content"] == "good code"
            assert result["changes_made"] is False

    @pytest.mark.asyncio
    async def test_llm_success_refactoring(self, agent):
        """LLM成功重构 → 返回新代码"""
        state = {"project_context": {"engine": "unity"}}
        refactored = "improved code with better structure and more lines to pass the length check"
        mock_result = {
            "needs_refactoring": True,
            "refactored_code": refactored,
            "changes": [{"description": "extracted method"}],
        }
        with patch.object(agent.llm, "chat_json", new_callable=AsyncMock, return_value=mock_result):
            result = await agent._refactor_file("test.cs", "old code", state)
            assert result["content"] == refactored
            assert result["changes_made"] is True
            assert len(result["changes"]) == 1

    @pytest.mark.asyncio
    async def test_llm_empty_refactored_code(self, agent):
        """LLM返回空代码 → 返回原始代码"""
        state = {"project_context": {"engine": "unity"}}
        mock_result = {
            "needs_refactoring": True,
            "refactored_code": "x",  # 太短
        }
        with patch.object(agent.llm, "chat_json", new_callable=AsyncMock, return_value=mock_result):
            result = await agent._refactor_file("test.cs", "original", state)
            assert result["content"] == "original"
            assert result["changes_made"] is False

    @pytest.mark.asyncio
    async def test_llm_exception_returns_original(self, agent):
        """LLM异常 → 返回原始代码"""
        state = {"project_context": {"engine": "unity"}}
        with patch.object(agent.llm, "chat_json", new_callable=AsyncMock, side_effect=Exception("API timeout")):
            result = await agent._refactor_file("test.cs", "original code", state)
            assert result["content"] == "original code"
            assert result["changes_made"] is False


class TestAnalyzeCodeQuality:
    """测试 analyze_code_quality 静态分析"""

    def test_good_code_scores_high(self, agent):
        """高质量代码得分高"""
        code = """
public class Player : MonoBehaviour
{
    // Player speed
    public float speed = 10.0f;

    /// <summary>
    /// Move the player
    /// </summary>
    public void Move(float direction)
    {
        transform.Translate(Vector2.right * direction * speed * Time.deltaTime);
    }
}
"""
        result = agent.analyze_code_quality(code)
        assert result["score"] >= 80
        assert len(result["issues"]) <= 1

    def test_long_file_penalty(self, agent):
        """过长文件扣分"""
        code = "\n".join([f"// line {i}" for i in range(600)])
        result = agent.analyze_code_quality(code)
        assert any("文件过长" in issue for issue in result["issues"])
        assert result["score"] < 100

    def test_many_methods_penalty(self, agent):
        """方法过多扣分"""
        methods = []
        for i in range(25):
            methods.append(f"public void Method{i}() {{ }}")
        code = "\n".join(methods)
        result = agent.analyze_code_quality(code)
        assert any("方法数量过多" in issue for issue in result["issues"])

    def test_deep_nesting_penalty(self, agent):
        """嵌套过深扣分"""
        code = """
public class Deep
{
    public void Method()
    {
        if (true)
        {
            if (true)
            {
                if (true)
                {
                    if (true)
                    {
                        // deeply nested
                    }
                }
            }
        }
    }
}
"""
        result = agent.analyze_code_quality(code)
        assert any("嵌套层级过深" in issue for issue in result["issues"])

    def test_low_comment_ratio_penalty(self, agent):
        """注释率过低扣分"""
        code = """
public class NoComments
{
    public int x;
    public int y;
    public void DoSomething() { }
    public void DoMore() { }
}
"""
        result = agent.analyze_code_quality(code)
        assert any("注释率过低" in issue for issue in result["issues"])

    def test_returns_metrics(self, agent):
        """返回正确的指标"""
        code = "// comment\npublic class Foo {}\n"
        result = agent.analyze_code_quality(code)
        assert "score" in result
        assert "issues" in result
        assert "line_count" in result
        assert "method_count" in result
        assert "max_indent" in result
        assert "comment_ratio" in result
