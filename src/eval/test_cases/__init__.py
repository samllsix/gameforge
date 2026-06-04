"""GameForge - 测试用例管理模块

管理评测用的测试用例和测试数据集。
"""

import json
import os
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field, asdict


@dataclass
class TestCase:
    """测试用例定义"""

    __test__ = False

    id: str
    name: str
    description: str
    requirements: str
    expected_features: List[str] = field(default_factory=list)
    engine: str = "unity"
    difficulty: str = "medium"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class TestCaseManager:
    """测试用例管理器"""

    __test__ = False

    def __init__(self, data_dir: str = "data/eval_datasets"):
        self.data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)

    def get_default_cases(self) -> List[TestCase]:
        """获取默认测试用例

        Returns:
            测试用例列表
        """
        return [
            TestCase(
                id="tc_001",
                name="2D平台跳跃游戏",
                description="创建一个基础的2D平台跳跃游戏",
                requirements="玩家角色可以左右移动和跳跃，有平台和障碍物，碰撞检测，计分系统",
                expected_features=["PlayerController", "CollisionHandler", "ScoreManager", "GameManager"],
                engine="unity",
                difficulty="easy",
            ),
            TestCase(
                id="tc_002",
                name="弹幕射击游戏",
                description="创建一个垂直卷轴弹幕射击游戏",
                requirements="玩家飞机移动射击，敌人生成，子弹碰撞，道具系统，Boss战",
                expected_features=["PlayerShip", "EnemySpawner", "BulletSystem", "PowerUpManager", "BossController"],
                engine="unity",
                difficulty="medium",
            ),
            TestCase(
                id="tc_003",
                name="RPG回合制战斗",
                description="创建一个回合制RPG战斗系统",
                requirements="角色属性系统，技能系统，回合制战斗，AI敌人，战斗UI",
                expected_features=["CharacterStats", "SkillSystem", "BattleManager", "EnemyAI", "BattleUI"],
                engine="unity",
                difficulty="hard",
            ),
        ]

    def save_case(self, test_case: TestCase):
        """保存测试用例

        Args:
            test_case: 测试用例
        """
        filepath = os.path.join(self.data_dir, f"{test_case.id}.json")
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(test_case.to_dict(), f, ensure_ascii=False, indent=2)

    def load_case(self, case_id: str) -> Optional[TestCase]:
        """加载测试用例

        Args:
            case_id: 用例ID

        Returns:
            测试用例，未找到返回None
        """
        filepath = os.path.join(self.data_dir, f"{case_id}.json")
        if not os.path.exists(filepath):
            return None

        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
            return TestCase(**data)

    def list_cases(self) -> List[TestCase]:
        """列出所有测试用例

        Returns:
            测试用例列表
        """
        cases = []
        if os.path.isdir(self.data_dir):
            for filename in os.listdir(self.data_dir):
                if filename.endswith(".json"):
                    case_id = filename.replace(".json", "")
                    case = self.load_case(case_id)
                    if case:
                        cases.append(case)
        return cases

    def init_default_cases(self):
        """初始化默认测试用例到文件"""
        for case in self.get_default_cases():
            self.save_case(case)
