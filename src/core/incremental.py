"""GameForge - 增量生成模块

基于影响分析，只重新生成变化的文件，减少无效 Agent 调用。
"""

import re
from typing import Any, Dict, List, Optional, Set, Tuple


class DependencyGraph:
    """文件依赖图

    通过静态分析代码引用关系，构建文件依赖图。
    支持：
    - .gd 文件间的 `load()` / preload 依赖
    - .tscn 场景文件中的节点/脚本引用
    - 简单基于命名约定的推断
    """

    # 常见 Godot 脚本引用模式
    _GD_REF_RE = re.compile(r"(?:^|\W)(?P<path>(?:res://)?(?:scripts|src)/[A-Za-z0-9_/]+\.gd)(?:\W|$)")

    # .tscn 中常见的脚本/资源引用
    _TSCN_REF_RE = re.compile(r"(?:^|\W)(?P<path>res://[A-Za-z0-9_/]+\.(?:gd|tscn|png|wav|mp3))(?:\W|$)")

    @classmethod
    def analyze(cls, files: List[Dict[str, str]]) -> Dict[str, Set[str]]:
        """分析文件列表，构建依赖图。

        Args:
            files: 文件列表，每个元素包含 `path` 和 `content`

        Returns:
            {file_path: set(dependent_file_paths)}
        """
        graph: Dict[str, Set[str]] = {}
        content_map: Dict[str, str] = {}

        for file_info in files:
            path = file_info.get("path", "")
            content = file_info.get("content", "")
            if path:
                graph.setdefault(path, set())
                content_map[path] = content

        for path, content in content_map.items():
            deps = cls._extract_deps(path, content, content_map.keys())
            graph[path] = deps

        return graph

    @classmethod
    def _extract_deps(cls, path: str, content: str, known_paths: Set[str]) -> Set[str]:
        deps: Set[str] = set()

        if path.endswith(".gd"):
            deps.update(cls._extract_gd_deps(content, known_paths))
        elif path.endswith(".tscn"):
            deps.update(cls._extract_tscn_deps(content, known_paths))

        return deps

    @classmethod
    def _normalize_ref(cls, ref: str) -> str:
        if ref.startswith("res://"):
            return ref[len("res://"):]
        return ref

    @classmethod
    def _extract_gd_deps(cls, content: str, known_paths: Set[str]) -> Set[str]:
        deps: Set[str] = set()
        for match in cls._GD_REF_RE.finditer(content):
            ref = cls._normalize_ref(match.group("path"))
            if ref in known_paths:
                deps.add(ref)
        return deps

    @classmethod
    def _extract_tscn_deps(cls, content: str, known_paths: Set[str]) -> Set[str]:
        deps: Set[str] = set()
        for match in cls._TSCN_REF_RE.finditer(content):
            ref = cls._normalize_ref(match.group("path"))
            if ref in known_paths:
                deps.add(ref)
        return deps


class IncrementalGenerator:
    """增量生成器

    分析变更影响范围，只重新生成必要的文件。
    """

    def __init__(self, dep_graph: Optional[Dict[str, Set[str]]] = None):
        self.dep_graph = dep_graph or {}

    def compute_impact(
        self,
        changed_files: List[str],
        all_files: Optional[List[str]] = None,
    ) -> Set[str]:
        """计算变更影响范围。

        Args:
            changed_files: 本次变更的文件路径列表
            all_files: 全部已知文件路径列表（用于补全省略依赖）

        Returns:
            受影响文件集合（包含直接变更和依赖文件）
        """
        impacted: Set[str] = set(changed_files)
        queue = list(changed_files)

        while queue:
            current = queue.pop(0)
            for dependent in self.dep_graph.get(current, set()):
                if dependent not in impacted:
                    impacted.add(dependent)
                    queue.append(dependent)

        return impacted

    def filter_task_plan(
        self,
        task_plan: List[Dict[str, Any]],
        impacted_files: Set[str],
    ) -> List[Dict[str, Any]]:
        """根据影响范围过滤任务计划。

        Args:
            task_plan: 原始任务计划
            impacted_files: 受影响文件集合

        Returns:
            过滤后的任务计划（只包含受影响任务）
        """
        filtered = []
        for task in task_plan:
            task_files = set(task.get("files", []))
            # 如果任务涉及的文件与影响范围有交集，保留该任务
            if task_files & impacted_files:
                filtered.append(task)
            elif not task_files:
                # 无文件关联的任务（如配置、设计）默认保留
                filtered.append(task)
        return filtered

    def filter_code_generated(
        self,
        code_generated: Dict[str, str],
        impacted_files: Set[str],
    ) -> Dict[str, str]:
        """根据影响范围过滤已生成代码。

        Args:
            code_generated: 已生成代码 {path: content}
            impacted_files: 受影响文件集合

        Returns:
            过滤后的代码（只保留受影响文件）
        """
        return {path: content for path, content in code_generated.items() if path in impacted_files}

    def should_regenerate(
        self,
        old_state: Dict[str, Any],
        new_state: Dict[str, Any],
    ) -> Tuple[bool, Set[str]]:
        """判断是否需要增量生成，并返回变更文件。

        Args:
            old_state: 旧状态
            new_state: 新状态

        Returns:
            (是否需要增量生成, 变更文件集合)
        """
        old_gdm = old_state.get("game_design_model") or {}
        new_gdm = new_state.get("game_design_model") or {}

        if old_gdm == new_gdm:
            return False, set()

        changed_files = self._detect_gdm_changes(old_gdm, new_gdm)
        return True, changed_files

    def _detect_gdm_changes(
        self,
        old_gdm: Dict[str, Any],
        new_gdm: Dict[str, Any],
    ) -> Set[str]:
        """检测 GDM 变更并映射到文件路径（启发式）。"""
        changed: Set[str] = set()

        # 实体变更 → 对应脚本文件
        old_entities = {e.get("name", "") for e in old_gdm.get("entities", []) if isinstance(e, dict)}
        new_entities = {e.get("name", "") for e in new_gdm.get("entities", []) if isinstance(e, dict)}
        for entity_name in new_entities - old_entities:
            changed.add(f"scripts/{entity_name.lower()}.gd")
        for entity_name in old_entities & new_entities:
            old_entity = next((e for e in old_gdm.get("entities", []) if isinstance(e, dict) and e.get("name") == entity_name), {})
            new_entity = next((e for e in new_gdm.get("entities", []) if isinstance(e, dict) and e.get("name") == entity_name), {})
            if old_entity != new_entity:
                changed.add(f"scripts/{entity_name.lower()}.gd")

        # 场景变更 → 对应场景文件
        old_scenes = set(old_gdm.get("scenes", []))
        new_scenes = set(new_gdm.get("scenes", []))
        for scene_name in new_scenes - old_scenes:
            changed.add(f"scenes/{scene_name}.tscn")
        for scene_name in old_scenes & new_scenes:
            changed.add(f"scenes/{scene_name}.tscn")

        return changed
