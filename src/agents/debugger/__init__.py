"""GameForge - 调试Agent模块

负责分析错误并生成修复方案。
"""

import re
from typing import Any, Dict, List, Optional
from src.agents.base import BaseAgent
from src.core.state.game_state import GameDevState, AgentType
from src.utils.llm_client import get_llm_client


class DebuggerAgent(BaseAgent):
    """调试Agent"""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(AgentType.DEBUGGER, config)
        self.llm = get_llm_client(config)
        self.max_fix_attempts = self.agent_config.get("max_fix_attempts", 5)

    async def execute(self, state: GameDevState, **kwargs) -> Dict[str, Any]:
        self.log_action("debugger_execute")

        error_log = kwargs.get("error_log", state.get("error_log", []))
        fix_result = await self.analyze_and_fix(state, error_log)

        return {
            **fix_result,
            "current_phase": "fix_applied",
            "error_log": [],
        }

    async def analyze_and_fix(self, state: GameDevState, error_log: List[str]) -> Dict[str, Any]:
        """分析错误并生成修复方案

        Args:
            state: 当前游戏开发状态
            error_log: 错误日志列表

        Returns:
            修复结果
        """
        self.log_action("analyze_and_fix")

        if not error_log:
            return {
                "fix_history": state.get("fix_history", []),
                "fix_attempts": state.get("fix_attempts", 0),
            }

        # 收集当前代码上下文
        code_generated = state.get("code_generated", {})
        code_context = ""
        for path, content in code_generated.items():
            if path.endswith(".cs"):
                code_context += f"\n### {path}\n```csharp\n{content}\n```\n"

        # 解析Unity编译错误（结构化）
        parsed_errors = []
        for err in error_log:
            parsed = self._parse_unity_error(err)
            if parsed:
                parsed_errors.append(parsed)

        error_text = "\n".join(error_log)
        parsed_context = ""
        if parsed_errors:
            parsed_context = "\n\n## 解析后的错误详情\n"
            for pe in parsed_errors:
                parsed_context += f"- **{pe.get('code', 'Unknown')}**: {pe.get('file', '?')}:{pe.get('line', '?')} — {pe.get('message', '')}\n"

        system_prompt = self.get_prompt_template("debugger_system")
        user_prompt = f"""请分析以下错误并生成修复方案。

## 错误信息
```
{error_text}
```
{parsed_context}

## 相关代码
{code_context}

请以JSON格式输出修复方案：
{{
    "error_type": "错误类型",
    "error_message": "错误描述",
    "root_cause": "根本原因分析",
    "fixes": [
        {{
            "file": "需要修改的文件路径",
            "description": "修复描述",
            "changes": [
                {{
                    "type": "replace|insert|delete",
                    "old_code": "原代码（replace时需要）",
                    "new_code": "新代码",
                    "line": 0
                }}
            ]
        }}
    ],
    "confidence": 0.9,
    "requires_human_review": false
}}"""

        try:
            result = await self.llm.chat_json(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=self.llm_config.get("temperature", 0.2),
                max_tokens=self.llm_config.get("max_tokens", 4096),
            )

            if result.get("parse_error"):
                self.log_error("debugger_parse_error")
                return self._fallback_fix(state, error_log)

            # 应用修复
            fix_record = {
                "error_type": result.get("error_type", "unknown"),
                "error_message": result.get("error_message", ""),
                "root_cause": result.get("root_cause", ""),
                "fixes_applied": result.get("fixes", []),
                "confidence": result.get("confidence", 0.5),
                "success": True,
            }

            fix_history = state.get("fix_history", []) + [fix_record]

            return {
                "fix_history": fix_history,
                "fix_attempts": state.get("fix_attempts", 0) + 1,
            }

        except Exception as e:
            self.log_error("debugger_llm_error", {"error": str(e)})
            return self._fallback_fix(state, error_log)

    def _fallback_fix(self, state: GameDevState, error_log: List[str]) -> Dict[str, Any]:
        """LLM调用失败时的回退修复"""
        error_text = " ".join(error_log)

        fix_record = {
            "error_type": "unknown",
            "error_message": error_text[:200],
            "fix_description": "自动修复失败，需要人工介入",
            "success": False,
        }

        return {
            "fix_history": state.get("fix_history", []) + [fix_record],
            "fix_attempts": state.get("fix_attempts", 0) + 1,
        }

    async def fix(self, state: GameDevState) -> Dict[str, Any]:
        """兼容旧接口"""
        error_log = state.get("error_log", [])
        return await self.analyze_and_fix(state, error_log)

    def _parse_unity_error(self, error_line: str) -> Optional[Dict[str, Any]]:
        """解析Unity编译错误行

        Unity错误格式:
        Assets/Scripts/Player/PlayerController.cs(15,10): error CS0246: The type or namespace 'X' could not be found
        Assets/Scripts/Core/GameManager.cs(42): error CS1061: Type 'Y' does not contain a definition for 'Z'

        Returns:
            解析结果 {"file", "line", "column", "code", "message"} 或 None
        """
        # 匹配 Unity 编译错误格式
        match = re.match(
            r'^(.+?)\((\d+)(?:,\s*(\d+))?\):\s*error\s+(CS\d+):\s*(.+)$',
            error_line.strip()
        )
        if match:
            return {
                "file": match.group(1),
                "line": int(match.group(2)),
                "column": int(match.group(3)) if match.group(3) else 0,
                "code": match.group(4),
                "message": match.group(5).strip(),
            }

        # 匹配简化的错误格式
        simple_match = re.match(r'^(?:error|Error)[:\s]+(.+)$', error_line.strip())
        if simple_match:
            return {
                "file": "",
                "line": 0,
                "column": 0,
                "code": "",
                "message": simple_match.group(1).strip(),
            }

        return None

    def analyze_error(self, error_message: str, stack_trace: str = "") -> Dict[str, Any]:
        error_patterns = {
            "NullReferenceException": {
                "type": "null_reference",
                "description": "空引用错误",
                "suggestion": "检查对象是否已初始化",
            },
            "IndexOutOfRangeException": {
                "type": "index_out_of_range",
                "description": "索引越界错误",
                "suggestion": "检查数组边界",
            },
            "InvalidCastException": {
                "type": "invalid_cast",
                "description": "类型转换错误",
                "suggestion": "检查类型兼容性",
            },
        }

        for pattern, info in error_patterns.items():
            if pattern in error_message:
                return {"error_type": pattern, "analysis": info, "auto_fixable": True}

        return {
            "error_type": "unknown",
            "analysis": {"description": "未知错误类型"},
            "auto_fixable": False,
        }
