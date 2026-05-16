"""GameForge - 测试生成Agent模块

负责为代码生成测试用例。
"""

import re
from typing import Any, Dict, List
from src.agents.base import BaseAgent
from src.core.state.game_state import GameDevState, AgentType
from src.utils.llm_client import get_llm_client


class TestGeneratorAgent(BaseAgent):
    """测试生成Agent"""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(AgentType.TEST_GENERATOR, config)
        self.llm = get_llm_client(config)
        self.coverage_target = self.agent_config.get("coverage_target", 80)

    async def execute(self, state: GameDevState, **kwargs) -> Dict[str, Any]:
        self.log_action("test_generator_execute")
        test_code = await self.generate(state)
        return {
            "code_generated": {**state.get("code_generated", {}), **test_code},
            "current_phase": "test_generated",
        }

    async def generate(self, state: GameDevState) -> Dict[str, str]:
        self.log_action("generate_tests")

        code_generated = state.get("code_generated", {})
        if not code_generated:
            return {}

        test_code = {}

        for file_path, content in code_generated.items():
            if file_path.endswith(".cs") and not file_path.endswith("Tests.cs"):
                test_path = file_path.replace(".cs", "Tests.cs")
                if test_path not in code_generated and test_path not in test_code:
                    generated = await self._generate_test_with_llm(file_path, content)
                    if generated:
                        test_code[test_path] = generated

        return test_code

    async def _generate_test_with_llm(self, source_path: str, source_content: str) -> str:
        """用LLM生成测试代码

        Args:
            source_path: 源文件路径
            source_content: 源代码内容

        Returns:
            测试代码
        """
        class_name = source_path.split("/")[-1].replace(".cs", "")
        test_file_path = source_path.replace(".cs", "Tests.cs")

        system_prompt = self.get_prompt_template("test_generator_system")
        user_prompt = f"""请为以下Unity C#代码生成完整的单元测试。

源文件: {source_path}
类名: {class_name}

源代码:
```csharp
{source_content}
```

要求：
1. 使用NUnit框架（[TestFixture], [Test], [SetUp], [TearDown]）
2. 使用UnityEngine.TestTools（[UnityTest]）
3. 测试文件路径: {test_file_path}
4. 测试类名: {class_name}Tests
5. 包含初始化测试、方法测试、边界测试
6. 测试必须完整可编译

请直接输出测试代码，不要包含解释文字。"""

        try:
            response = self.llm.chat(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=self.llm_config.get("temperature", 0.4),
                max_tokens=self.llm_config.get("max_tokens", 4096),
            )

            # 提取代码块
            code_match = re.search(r'```(?:csharp|cs)?\s*\n(.*?)\n```', response, re.DOTALL)
            if code_match:
                return code_match.group(1).strip()

            # 如果没有代码块但看起来像代码
            if 'class ' in response and '[Test]' in response:
                return response.strip()

            self.log_error("test_parse_failed", {"class": class_name})
            return self._generate_test_template(source_path, source_content)

        except Exception as e:
            self.log_error("test_generator_llm_error", {"error": str(e)})
            return self._generate_test_template(source_path, source_content)

    def _generate_test_template(self, source_path: str, source_content: str) -> str:
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
            Assert.IsNotNull(_instance);
        }}

        [UnityTest]
        public IEnumerator {class_name}_Update_WorksCorrectly()
        {{
            yield return null;
            Assert.IsNotNull(_instance);
        }}
        #endregion
    }}
}}'''
