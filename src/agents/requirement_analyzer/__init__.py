"""GameForge - 需求解析 Agent

将用户自然语言需求解析为结构化 Game Spec（DSL），
供下游 Agent 使用，减少 LLM 理解偏差。
"""

import json
import re
from typing import Any, Dict, List, Optional

from src.agents.base import BaseAgent
from src.core.state.game_state import AgentType, GameDevState
from src.utils.llm_client import get_llm_client


class RequirementAnalyzerAgent(BaseAgent):
    """需求解析 Agent — 输出结构化 Game Spec"""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(AgentType.REQUIREMENT_ANALYZER, config)
        self.llm = get_llm_client(config, provider=self.provider, model=self.model)

    async def execute(self, state: GameDevState, **kwargs) -> Dict[str, Any]:
        self.log_action("requirement_analyzer_execute")
        requirements = state.get("project_context", {}).get("requirements", "")
        engine = state.get("project_context", {}).get("engine", "godot")

        spec = await self.analyze(requirements, engine)
        if not spec:
            self.log_error("spec_generation_failed")
            spec = self._fallback_spec(requirements, engine)

        return {
            "game_spec": spec,
            "current_phase": "requirement_analyzed",
        }

    async def analyze(self, requirements: str, engine: str) -> Optional[Dict]:
        """调用 LLM 生成结构化 Game Spec"""
        system_prompt = """你是游戏需求解析专家。将用户自然语言需求转化为结构化的 Game Spec JSON。

输出严格 JSON，包含以下字段：
{
  "game": {"genre": "", "camera": "", "difficulty": "medium"},
  "player": {"hp": 100, "actions": [], "resources": []},
  "enemy": {"types": [], "wave_system": false},
  "mechanics": [],
  "assets": [],
  "scenes": []
}

规则：
1. 只输出 JSON，不要额外文本
2. 用户未提及的字段用合理默认值
3. genre 从：platformer, shooter, rpg, puzzle, tower_defense, racing, fighting, casual 中选择最接近的
4. camera 从：2D_side, 2D_top_down, 3D_first_person, 3D_third_person 中选择
5. difficulty 从：easy, medium, hard 中选择
"""

        user_prompt = f"""引擎: {engine}
用户需求:
{requirements}

请输出 Game Spec JSON。"""

        try:
            response = await self.llm.chat(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                model=self.model,
                temperature=self.llm_config.get("temperature", 0.2),
                max_tokens=self.llm_config.get("max_tokens", 2048),
            )

            spec = self._extract_json(response)
            if spec and self._is_valid_spec(spec):
                return self._normalize_spec(spec, requirements, engine)

            # retry with stricter prompt
            retry_response = await self.llm.chat(
                messages=[
                    {"role": "system", "content": "输出严格 JSON 格式的 Game Spec，不要任何其他内容。"},
                    {"role": "user", "content": user_prompt},
                ],
                model=self.model,
                temperature=0.0,
                max_tokens=self.llm_config.get("max_tokens", 2048),
            )
            spec = self._extract_json(retry_response)
            if spec and self._is_valid_spec(spec):
                return self._normalize_spec(spec, requirements, engine)

            self.log_error("no_valid_spec_in_response", {"preview": response[:300]})
            return None

        except Exception as e:
            self.log_error("spec_llm_error", {"error": str(e)})
            return None

    def _extract_json(self, text: str) -> Optional[Dict]:
        """从 LLM 响应中提取 JSON"""
        from src.utils.json_extractor import extract_json
        return extract_json(text)

    def _is_valid_spec(self, spec: Dict) -> bool:
        """检查 spec 是否包含必要字段"""
        return isinstance(spec, dict) and ("game" in spec or "genre" in spec)

    def _normalize_spec(self, spec: Dict, requirements: str, engine: str) -> Dict:
        """规范化 Game Spec，补全缺失字段"""
        defaults = {
            "game": {
                "genre": "casual",
                "camera": "2D_side",
                "difficulty": "medium",
            },
            "player": {
                "hp": 100,
                "actions": [],
                "resources": [],
            },
            "enemy": {
                "types": [],
                "wave_system": False,
            },
            "mechanics": [],
            "assets": [],
            "scenes": [],
            "requirements": requirements,
            "engine": engine,
        }

        # 递归合并默认值
        def merge(base: Dict, defaults: Dict) -> Dict:
            for key, default_val in defaults.items():
                if key not in base:
                    base[key] = default_val
                elif isinstance(default_val, dict) and isinstance(base[key], dict):
                    merge(base[key], default_val)
            return base

        return merge(spec, defaults)

    def _fallback_spec(self, requirements: str, engine: str) -> Dict:
        """LLM 失败时的兜底 Game Spec"""
        genre = "casual"
        req_lower = requirements.lower()
        if any(k in req_lower for k in ["塔防", "tower", "defense"]):
            genre = "tower_defense"
        elif any(k in req_lower for k in ["射击", "shooter", "枪"]):
            genre = "shooter"
        elif any(k in req_lower for k in ["角色", "rpg", "回合"]):
            genre = "rpg"
        elif any(k in req_lower for k in ["平台", "跳跃", "platformer", "跑酷", "parkour"]):
            genre = "platformer"
        elif any(k in req_lower for k in ["解谜", "puzzle"]):
            genre = "puzzle"

        return {
            "game": {
                "genre": genre,
                "camera": "2D_side",
                "difficulty": "medium",
            },
            "player": {
                "hp": 100,
                "actions": ["move", "jump"],
                "resources": [],
            },
            "enemy": {
                "types": ["basic"],
                "wave_system": False,
            },
            "mechanics": [],
            "assets": [],
            "scenes": ["main_menu", "gameplay"],
            "requirements": requirements,
            "engine": engine,
        }
