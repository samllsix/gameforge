"""GameForge - 测试生成Agent模块

负责为代码生成测试用例。
"""

from typing import Any, Dict, List
from src.agents.base import BaseAgent
from src.core.state.game_state import GameDevState, AgentType


class TestGeneratorAgent(BaseAgent):
    """测试生成Agent

    负责：
    - 生成单元测试
    - 生成集成测试
    - 生成端到端测试
    """

    def __init__(self, config: Dict[str, Any]):
        """初始化测试生成Agent

        Args:
            config: 配置信息
        """
        super().__init__(AgentType.TEST_GENERATOR, config)
        self.coverage_target = self.agent_config.get("coverage_target", 80)

    async def execute(self, state: GameDevState, **kwargs) -> Dict[str, Any]:
        """执行测试生成任务

        Args:
            state: 当前游戏开发状态

        Returns:
            包含测试代码的状态更新
        """
        self.log_action("test_generator_execute")

        test_code = await self.generate(state)

        return {
            "code_generated": {
                **state.get("code_generated", {}),
                **test_code,
            },
            "current_phase": "test_generated",
        }

    async def generate(self, state: GameDevState) -> Dict[str, str]:
        """生成测试代码

        Args:
            state: 当前游戏开发状态

        Returns:
            测试代码字典，键为文件路径，值为代码内容
        """
        self.log_action("generate_tests")

        code_generated = state.get("code_generated", {})
        if not code_generated:
            return {}

        # TODO: 实现基于LLM的测试生成
        # 这里先返回示例测试代码
        test_code = {}

        for file_path, content in code_generated.items():
            # 只为非测试文件生成测试
            if file_path.endswith(".cs") and not file_path.endswith("Tests.cs"):
                test_path = file_path.replace(".cs", "Tests.cs")
                # 检查测试文件是否已存在
                if test_path not in code_generated and test_path not in test_code:
                    test_code[test_path] = self._generate_test_template(file_path, content)

        return test_code

    def _generate_test_template(self, source_path: str, source_content: str) -> str:
        """生成测试模板

        Args:
            source_path: 源文件路径
            source_content: 源代码内容

        Returns:
            测试代码
        """
        # 从源文件名提取类名
        class_name = source_path.split("/")[-1].replace(".cs", "")

        return f'''using NUnit.Framework;
using UnityEngine;
using UnityEngine.TestTools;

namespace GameForge.Tests
{{
    /// <summary>
    /// {class_name} 单元测试
    /// </summary>
    [TestFixture]
    public class {class_name}Tests
    {{
        #region Setup and Teardown
        private GameObject _testObject;
        private {class_name} _instance;

        [SetUp]
        public void SetUp()
        {{
            _testObject = new GameObject();
            _instance = _testObject.AddComponent<{class_name}>();
        }}

        [TearDown]
        public void TearDown()
        {{
            Object.DestroyImmediate(_testObject);
        }}
        #endregion

        #region Tests
        [Test]
        public void {class_name}_Initialization_HasCorrectDefaults()
        {{
            // Arrange
            // Act
            // Assert
            Assert.IsNotNull(_instance);
        }}

        [UnityTest]
        public IEnumerator {class_name}_Update_WorksCorrectly()
        {{
            // Arrange
            // Act
            yield return null;

            // Assert
            Assert.IsNotNull(_instance);
        }}
        #endregion
    }}
}}'''
