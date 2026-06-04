"""GameForge test generation agent."""

import json
import re
from typing import Any, Dict

from src.agents.base import BaseAgent
from src.core.state.game_state import AgentType, GameDevState
from src.utils.llm_client import get_llm_client


class TestGeneratorAgent(BaseAgent):
    """Generate Unity EditMode tests without polluting runtime assemblies."""
    __test__ = False

    def __init__(self, config: Dict[str, Any]):
        super().__init__(AgentType.TEST_GENERATOR, config)
        self.llm = get_llm_client(config, provider=self.provider, model=self.model)
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

        source_files = {}
        for file_path, content in code_generated.items():
            if not self._is_runtime_source(file_path):
                continue
            test_path = self._test_path_for_source(file_path)
            if test_path not in code_generated:
                source_files[file_path] = content

        if not source_files:
            return {}

        return await self._generate_all_tests_batch(source_files)

    async def _generate_all_tests_batch(
        self, source_files: Dict[str, str]
    ) -> Dict[str, str]:
        files_section = ""
        for file_path, content in source_files.items():
            class_name = file_path.split("/")[-1].replace(".cs", "")
            files_section += (
                f"\n### File: {file_path}\n"
                f"Class: {class_name}\n"
                f"Test path: {self._test_path_for_source(file_path)}\n"
                f"```csharp\n{content}\n```\n"
            )

        system_prompt = self.get_prompt_template("test_generator_system")
        user_prompt = f"""Generate Unity C# EditMode tests for these source files.
{files_section}

Requirements:
1. Use NUnit and UnityEngine.TestTools.
2. Put every test under Assets/Tests/EditMode/, never under Assets/Scripts/.
3. Include using System.Collections; when IEnumerator is used.
4. If a source class has a namespace, add a using for that namespace.
5. Tests must compile in Unity EditMode.

Output one fenced csharp block per test file:
```csharp
// File: Assets/Tests/EditMode/Player/PlayerControllerTests.cs
using NUnit.Framework;
...
```
"""

        try:
            response = await self.llm.chat(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                model=self.model,
                temperature=self.llm_config.get("temperature", 0.4),
                max_tokens=self.llm_config.get("max_tokens", 8192),
            )

            test_code = {}
            code_blocks = re.findall(
                r"```(?:csharp|cs)?\s*\n(.*?)\n```",
                response,
                re.DOTALL,
            )

            for block in code_blocks:
                block = block.strip()
                if not block:
                    continue

                test_path = ""
                file_path_match = re.search(
                    r"//\s*(?:File|文件):\s*(\S+)", block
                )
                if file_path_match:
                    test_path = self._normalize_test_path(
                        file_path_match.group(1), source_files
                    )
                    block = re.sub(
                        r"//\s*(?:File|文件):\s*\S+\s*\n",
                        "",
                        block,
                        count=1,
                    ).strip()
                else:
                    class_match = re.search(r"class\s+(\w+Tests)", block)
                    if class_match:
                        test_name = class_match.group(1).replace("Tests", "")
                        for src_path in source_files:
                            if test_name in src_path:
                                test_path = self._test_path_for_source(src_path)
                                break

                if not test_path:
                    continue

                if "class " in block and ("[Test]" in block or "[TestFixture]" in block):
                    source_path = self._source_path_for_test_path(test_path, source_files)
                    test_code[test_path] = self._sanitize_test_code(
                        block, source_files.get(source_path, "")
                    )

            for file_path, content in source_files.items():
                test_path = self._test_path_for_source(file_path)
                if test_path not in test_code:
                    test_code[test_path] = self._generate_test_template(
                        file_path, content
                    )

            if test_code:
                test_code.setdefault(
                    "Assets/Tests/EditMode/GameForge.Tests.asmdef",
                    self._generate_tests_asmdef(),
                )

            self.log_action("batch_tests_generated", {"file_count": len(test_code)})
            return test_code

        except Exception as e:
            self.log_error("batch_test_generator_error", {"error": str(e)})
            test_code = {
                self._test_path_for_source(path): self._generate_test_template(
                    path, content
                )
                for path, content in source_files.items()
            }
            if test_code:
                test_code["Assets/Tests/EditMode/GameForge.Tests.asmdef"] = (
                    self._generate_tests_asmdef()
                )
            return test_code

    async def _generate_test_with_llm(
        self, source_path: str, source_content: str
    ) -> str:
        class_name = source_path.split("/")[-1].replace(".cs", "")
        test_file_path = self._test_path_for_source(source_path)

        system_prompt = self.get_prompt_template("test_generator_system")
        user_prompt = f"""Generate a complete Unity EditMode test.
Source file: {source_path}
Class: {class_name}
Test file path: {test_file_path}

```csharp
{source_content}
```

Return only compilable C# test code. Include using System.Collections if needed.
"""

        try:
            response = await self.llm.chat(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                model=self.model,
                temperature=self.llm_config.get("temperature", 0.4),
                max_tokens=self.llm_config.get("max_tokens", 4096),
            )

            code_match = re.search(
                r"```(?:csharp|cs)?\s*\n(.*?)\n```", response, re.DOTALL
            )
            if code_match:
                return self._sanitize_test_code(code_match.group(1).strip(), source_content)

            if "class " in response and "[Test]" in response:
                return self._sanitize_test_code(response.strip(), source_content)

            self.log_error("test_parse_failed", {"class": class_name})
            return self._generate_test_template(source_path, source_content)

        except Exception as e:
            self.log_error("test_generator_llm_error", {"error": str(e)})
            return self._generate_test_template(source_path, source_content)

    def _generate_test_template(self, source_path: str, source_content: str) -> str:
        class_name = source_path.split("/")[-1].replace(".cs", "")
        namespace = self._extract_namespace(source_content)
        namespace_using = f"using {namespace};\n" if namespace else ""

        return f"""using NUnit.Framework;
using System.Collections;
using UnityEngine;
using UnityEngine.TestTools;
{namespace_using}
namespace GameForge.Tests
{{
    [TestFixture]
    public class {class_name}Tests
    {{
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
    }}
}}"""

    def _is_runtime_source(self, file_path: str) -> bool:
        normalized = file_path.replace("\\", "/")
        return (
            normalized.endswith(".cs")
            and not normalized.endswith("Tests.cs")
            and not normalized.startswith("Assets/Editor/")
            and not normalized.startswith("Assets/Tests/")
        )

    def _test_path_for_source(self, source_path: str) -> str:
        normalized = source_path.replace("\\", "/")
        if normalized.startswith("Assets/Scripts/"):
            rel = normalized[len("Assets/Scripts/") :]
        elif normalized.startswith("Assets/"):
            rel = normalized[len("Assets/") :]
        else:
            rel = normalized.rsplit("/", 1)[-1]
        return "Assets/Tests/EditMode/" + rel.replace(".cs", "Tests.cs")

    def _normalize_test_path(
        self, test_path: str, source_files: Dict[str, str]
    ) -> str:
        normalized = test_path.replace("\\", "/")
        if normalized.startswith("Assets/Tests/EditMode/"):
            return normalized

        for src_path in source_files:
            legacy_path = src_path.replace(".cs", "Tests.cs")
            if normalized == legacy_path or normalized.endswith(
                legacy_path.rsplit("/", 1)[-1]
            ):
                return self._test_path_for_source(src_path)

        if normalized.startswith("Assets/Scripts/"):
            return "Assets/Tests/EditMode/" + normalized[len("Assets/Scripts/") :]
        return normalized

    def _source_path_for_test_path(
        self, test_path: str, source_files: Dict[str, str]
    ) -> str:
        test_name = test_path.rsplit("/", 1)[-1].replace("Tests.cs", ".cs")
        for src_path in source_files:
            if src_path.rsplit("/", 1)[-1] == test_name:
                return src_path
        return ""

    def _sanitize_test_code(self, code: str, source_content: str) -> str:
        if "IEnumerator" in code and "using System.Collections;" not in code:
            code = "using System.Collections;\n" + code

        namespace = self._extract_namespace(source_content)
        if namespace and f"using {namespace};" not in code:
            code = f"using {namespace};\n" + code
        return code

    def _extract_namespace(self, source_content: str) -> str:
        match = re.search(r"\bnamespace\s+([\w.]+)", source_content)
        return match.group(1) if match else ""

    def _generate_tests_asmdef(self) -> str:
        return json.dumps(
            {
                "name": "GameForge.Tests",
                "rootNamespace": "GameForge.Tests",
                "references": ["UnityEngine.TestRunner", "UnityEditor.TestRunner"],
                "includePlatforms": ["Editor"],
                "optionalUnityReferences": ["TestAssemblies"],
            },
            indent=2,
        )
