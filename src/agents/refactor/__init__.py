"""GameForge - 重构Agent模块

负责分析代码质量并生成重构方案。
"""

import re
from typing import Any, Dict, List
from src.agents.base import BaseAgent
from src.core.state.game_state import GameDevState, AgentType
from src.utils.llm_client import get_llm_client


class RefactorAgent(BaseAgent):
    """重构Agent

    负责：
    - 分析代码质量和复杂度
    - 识别代码异味（Code Smells）
    - 生成重构方案
    - 优化代码结构和性能
    """

    def __init__(self, config: Dict[str, Any]):
        super().__init__(AgentType.REFACTOR, config)
        self.llm = get_llm_client(config, provider=self.provider, model=self.model)

    async def execute(self, state: GameDevState, **kwargs) -> Dict[str, Any]:
        self.log_action("refactor_execute")

        code_generated = state.get("code_generated", {})
        if not code_generated:
            self.log_action("no_code_to_refactor")
            return {"current_phase": "refactored"}

        refactor_results = await self.analyze_and_refactor(state, code_generated)

        return {
            "code_generated": refactor_results.get("refactored_code", code_generated),
            "code_artifacts": state.get("code_artifacts", []) + refactor_results.get("new_artifacts", []),
            "current_phase": "refactored",
        }

    async def analyze_and_refactor(
        self, state: GameDevState, code_files: Dict[str, str]
    ) -> Dict[str, Any]:
        """分析代码并生成重构方案

        Args:
            state: 当前游戏开发状态
            code_files: 代码文件字典 {路径: 内容}

        Returns:
            重构结果
        """
        self.log_action("analyze_and_refactor")

        refactored_code = {}
        new_artifacts = []

        for file_path, content in code_files.items():
            if not file_path.endswith(".cs"):
                refactored_code[file_path] = content
                continue

            result = await self._refactor_file(file_path, content, state)
            refactored_code[file_path] = result.get("content", content)

            if result.get("changes_made"):
                new_artifacts.append({
                    "file_path": file_path,
                    "content": result["content"],
                    "language": "csharp",
                    "engine": state.get("project_context", {}).get("engine", "unity"),
                    "refactored": True,
                    "changes": result.get("changes", []),
                })

        return {
            "refactored_code": refactored_code,
            "new_artifacts": new_artifacts,
        }

    async def _refactor_file(
        self, file_path: str, content: str, state: GameDevState
    ) -> Dict[str, Any]:
        """重构单个文件

        Args:
            file_path: 文件路径
            content: 文件内容
            state: 当前状态

        Returns:
            重构结果
        """
        system_prompt = self.get_prompt_template("refactor_system")
        requirements = state.get("project_context", {}).get("requirements", "")
        gdm = state.get("game_design_model") or {}
        if not system_prompt:
            system_prompt = """你是一个Unity/C#代码重构专家。请分析代码并提供重构建议。

重构原则：
1. 遵循SOLID原则
2. 减少圈复杂度
3. 提高代码可读性
4. 优化性能
5. 保持功能不变

输出格式：
```json
{
    "needs_refactoring": true/false,
    "issues": ["问题1", "问题2"],
    "changes": [
        {
            "description": "变更描述",
            "type": "extract_method|rename|inline|move|simplify"
        }
    ],
    "refactored_code": "重构后的完整代码"
}
```"""

        user_prompt = f"""请分析以下C#代码并进行重构优化。

原始用户需求（重构必须保持这些玩法语义不变）:
{requirements}

游戏设计锚点:
- 类型: {gdm.get('genre', '')}
- 核心循环: {gdm.get('core_loop', '')}
- 玩家动作: {', '.join(gdm.get('player_actions', []))}
- 胜利条件: {', '.join(gdm.get('win_conditions', []))}
- 失败条件: {', '.join(gdm.get('fail_conditions', []))}

文件路径: {file_path}

```csharp
{content}
```

要求：
1. 如果代码已经足够好，返回 needs_refactoring: false
2. 如果需要重构，提供完整的重构后代码
3. 保持所有公共API不变
4. 确保代码可以直接编译使用
5. 不允许删除、弱化或重命名用户明确要求的玩法语义；如重构会影响需求命中率，返回 needs_refactoring: false

请以JSON格式输出。"""

        try:
            result = await self.llm.chat_json(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                model=self.model,
                temperature=self.llm_config.get("temperature", 0.2),
                max_tokens=self.llm_config.get("max_tokens", 8192),
            )

            if result.get("parse_error"):
                self.log_error("refactor_parse_error", {"file": file_path})
                return {"content": content, "changes_made": False}

            if not result.get("needs_refactoring", False):
                return {"content": content, "changes_made": False}

            refactored_code = result.get("refactored_code", content)
            if not refactored_code or len(refactored_code) < 50:
                return {"content": content, "changes_made": False}

            return {
                "content": refactored_code,
                "changes_made": True,
                "changes": result.get("changes", []),
            }

        except Exception as e:
            self.log_error("refactor_llm_error", {"error": str(e), "file": file_path})
            return {"content": content, "changes_made": False}

    def analyze_code_quality(self, content: str) -> Dict[str, Any]:
        """静态分析代码质量

        Args:
            content: 代码内容

        Returns:
            质量分析结果
        """
        issues = []
        score = 100

        lines = content.split("\n")
        if len(lines) > 500:
            issues.append("文件过长，建议拆分")
            score -= 10

        method_pattern = r'(?:public|private|protected|internal)?\s*(?:static\s+)?(?:async\s+)?\w+\s+\w+\s*\([^)]*\)\s*\{'
        methods = re.findall(method_pattern, content)
        if len(methods) > 20:
            issues.append("方法数量过多，考虑拆分类")
            score -= 10

        max_indent = 0
        for line in lines:
            stripped = line.lstrip()
            if stripped:
                indent = len(line) - len(stripped)
                max_indent = max(max_indent, indent)
        if max_indent > 16:
            issues.append("嵌套层级过深，建议提取方法")
            score -= 15

        magic_numbers = re.findall(r'(?<!\w)\d+\.?\d*(?!\w)', content)
        if len(magic_numbers) > 10:
            issues.append("存在大量魔术数字，建议提取为常量")
            score -= 5

        comment_lines = sum(1 for line in lines if line.strip().startswith("//") or line.strip().startswith("///"))
        comment_ratio = comment_lines / len(lines) if lines else 0
        if comment_ratio < 0.05:
            issues.append("注释率过低，建议添加文档注释")
            score -= 5

        return {
            "score": max(0, score),
            "issues": issues,
            "line_count": len(lines),
            "method_count": len(methods),
            "max_indent": max_indent,
            "comment_ratio": comment_ratio,
        }
