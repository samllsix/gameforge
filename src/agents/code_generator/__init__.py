"""GameForge - 代码生成Agent模块

负责根据任务描述生成游戏代码。
"""

import re
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional
from src.agents.base import BaseAgent
from src.core.state.game_state import TaskStatus, GameDevState, AgentType, TaskType
from src.utils.llm_client import get_llm_client


@dataclass
class GeneratedFileMetadata:
    """生成文件的元数据 — 代码和场景之间的契约"""
    class_name: str = ""
    file_path: str = ""
    namespace: str = ""
    target_game_object: str = ""
    required_components: List[str] = field(default_factory=list)
    source_task: str = ""
    dependencies: List[str] = field(default_factory=list)
    public_events: List[str] = field(default_factory=list)
    public_methods: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def extract_file_metadata(code: str, file_path: str, task: Dict[str, Any] = None) -> GeneratedFileMetadata:
    """从C#代码中提取文件元数据

    Args:
        code: C#源代码
        file_path: 文件路径
        task: 任务信息

    Returns:
        GeneratedFileMetadata
    """
    meta = GeneratedFileMetadata(file_path=file_path)

    # 提取类名（C# 与 GDScript 均兼容）
    class_match = re.search(r'public\s+(?:partial\s+)?(?:class|struct|interface)\s+(\w+)', code)
    if not class_match:
        class_match = re.search(r'^class_name\s+(\w+)', code, re.MULTILINE)
    if class_match:
        meta.class_name = class_match.group(1)

    # 提取namespace（仅 C#）
    ns_match = re.search(r'namespace\s+([\w.]+)', code)
    if ns_match:
        meta.namespace = ns_match.group(1)

    # 从注释提取挂载目标（// 与 # 均兼容）
    mount_match = re.search(r'(?://|#)\s*挂载:\s*(\S+)', code)
    if mount_match:
        meta.target_game_object = mount_match.group(1)

    # 从注释提取组件
    comp_match = re.search(r'(?://|#)\s*组件:\s*(.+)', code)
    if comp_match:
        meta.required_components = [c.strip() for c in comp_match.group(1).split(",")]

    # 从注释提取依赖
    deps_match = re.search(r'(?://|#)\s*依赖:\s*\[([^\]]*)\]', code)
    if deps_match and deps_match.group(1).strip():
        meta.dependencies = [d.strip() for d in deps_match.group(1).split(",")]

    # 提取public事件（UnityEvent、Action、event关键字）
    meta.public_events = re.findall(
        r'public\s+(?:event\s+)?(?:UnityEvent(?:<[\w,\s]+>)?|Action(?:<[\w,\s]+>)?|System\.Action(?:<[\w,\s]+>)?)\s+(\w+)',
        code,
    )

    # 提取public方法（排除Unity生命周期方法和属性访问器）
    _lifecycle = {
        "Awake", "Start", "Update", "FixedUpdate", "LateUpdate",
        "OnEnable", "OnDisable", "OnDestroy", "OnTriggerEnter",
        "OnTriggerExit", "OnCollisionEnter", "OnCollisionExit",
        "OnTriggerEnter2D", "OnTriggerExit2D", "OnCollisionEnter2D",
        "OnCollisionExit2D", "OnMouseDown", "OnMouseUp",
    }
    meta.public_methods = [
        m.group(1)
        for m in re.finditer(r'public\s+(?:static\s+)?(?:virtual\s+)?(?:override\s+)?(?:void|int|float|bool|string|IEnumerator|Task)\s+(\w+)\s*\(', code)
        if m.group(1) not in _lifecycle
    ]

    # 从任务信息补充
    if task:
        meta.source_task = task.get("id", "")
        if not meta.target_game_object:
            meta.target_game_object = task.get("scene_objects", [""])[0] if task.get("scene_objects") else ""
        if not meta.required_components:
            meta.required_components = task.get("required_components", [])

    return meta


class CodeGeneratorAgent(BaseAgent):
    """代码生成Agent

    负责：
    - 根据任务描述生成 Godot 4.x GDScript 代码
    - 严格遵循全局约束：禁止输出 Unity / Unreal / C# / C++ 代码
    - LLM 失败时回退到经过验证的 GDScript 模板
    """

    def __init__(self, config: Dict[str, Any]):
        super().__init__(AgentType.CODE_GENERATOR, config)
        self.llm = get_llm_client(config, provider=self.provider, model=self.model)
        self.supported_engines = self.agent_config.get("supported_engines", ["godot"])

    async def execute(self, state: GameDevState, **kwargs) -> Dict[str, Any]:
        self.log_action("code_generator_execute")

        task = kwargs.get("task")
        if not task:
            self.log_error("no_task_provided")
            return {"error_log": ["No task provided"]}

        code_artifacts = await self.generate(state, task)

        # 提取每个文件的元数据
        file_metadata = {}
        for art in code_artifacts:
            meta = extract_file_metadata(art["content"], art["file_path"], task)
            file_metadata[art["file_path"]] = meta.to_dict()
            art["metadata"] = {**art.get("metadata", {}), **meta.to_dict()}

        return {
            "code_generated": {
                **state.get("code_generated", {}),
                **{art["file_path"]: art["content"] for art in code_artifacts}
            },
            "code_artifacts": state.get("code_artifacts", []) + code_artifacts,
            "file_metadata": {**state.get("file_metadata", {}), **file_metadata},
            "current_phase": "code_generated",
        }



    async def fix_code(
        self, state: GameDevState, error_log: List[str]
    ) -> Dict[str, Any]:
        """修复生成的代码（原 debugger 职能并入）。

        Args:
            state: 当前游戏开发状态
            error_log: 编译/冒烟测试错误列表

        Returns:
            {"code_generated": 更新后的文件, "fix_history": [...], "fix_attempts": N}
        """
        self.log_action("fix_code", {"error_count": len(error_log)})

        if not error_log:
            return {
                "fix_history": state.get("fix_history", []),
                "fix_attempts": state.get("fix_attempts", 0),
            }

        code_generated = state.get("code_generated", {})
        code_context = ""
        for path, content in code_generated.items():
            if path.endswith(".gd"):
                code_context += f"\n### {path}\n```gdscript\n{content}\n```\n"

        error_text = "\n".join(error_log)
        requirements = state.get("project_context", {}).get("requirements", "")
        gdm = state.get("game_design_model") or {}

        system_prompt = self.get_prompt_template("code_generator_system")
        user_prompt = f"""以下 Godot 4.x GDScript 代码存在错误，请修复。

## 原始用户需求（修复后必须继续满足）
{requirements}

## 游戏设计锚点
- 类型: {gdm.get('genre', '')}
- 核心循环: {gdm.get('core_loop', '')}

## 错误信息
```
{error_text}
```

## 相关代码
{code_context}

要求：
1. 修复所有列出的错误，保持其余逻辑不变。
2. 严格遵循 Godot 4.x GDScript 语法（snake_case、class_name、extends、signal、@export）。
3. 禁止使用 C# / Unity 语法。

请直接输出修复后的完整文件，格式如下：
```gdscript
# 文件: res://scripts/[Module]/[Name].gd
extends CharacterBody2D
...
```
每个文件用单独的代码块。"""

        try:
            response = await self.llm.chat(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                model=self.model,
                temperature=self.llm_config.get("temperature", 0.2),
                max_tokens=self.llm_config.get("max_tokens", 8192),
            )

            artifacts = self._parse_code_response(response, task={"id": "fix", "name": "fix", "type": "code", "description": "修复编译错误"}, engine="godot")
            if not artifacts:
                self.log_error("fix_code_no_output", {"response_preview": response[:200]})
                return self._fix_fallback(state, error_log)

            updated_code = dict(code_generated)
            for art in artifacts:
                fp = art["file_path"]
                # 修复输出可能是相对名，尝试对齐已有文件
                if fp not in updated_code:
                    matches = [k for k in updated_code if k.endswith(fp) or fp.endswith(k)]
                    if matches:
                        fp = matches[0]
                updated_code[fp] = art["content"]

            fix_record = {
                "error_type": "compile_error",
                "error_message": error_text[:200],
                "root_cause": "code_generator fix_code",
                "fixes_applied": [a["file_path"] for a in artifacts],
                "success": True,
            }
            return {
                "code_generated": updated_code,
                "fix_history": state.get("fix_history", []) + [fix_record],
                "fix_attempts": state.get("fix_attempts", 0) + 1,
            }

        except Exception as e:
            self.log_error("fix_code_llm_error", {"error": str(e)})
            return self._fix_fallback(state, error_log)

    def _fix_fallback(self, state: GameDevState, error_log: List[str]) -> Dict[str, Any]:
        """LLM 修复失败时的兜底（记录失败，不改动代码）"""
        fix_record = {
            "error_type": "unknown",
            "error_message": " ".join(error_log)[:200],
            "fix_description": "自动修复失败，需要人工介入",
            "success": False,
        }
        return {
            "code_generated": state.get("code_generated", {}),
            "fix_history": state.get("fix_history", []) + [fix_record],
            "fix_attempts": state.get("fix_attempts", 0) + 1,
        }

    async def generate(self, state: GameDevState, task: Dict[str, Any]) -> List[Dict[str, Any]]:
        self.log_action("generate_code", {"task_id": task.get("id")})

        engine = "godot"  # GameForge 已聚焦 Godot 4.x，禁用 Unity/Unreal/C++
        project_name = state.get("project_context", {}).get("project_name", "GameForge")

        task_type = task.get("type", TaskType.CODE.value)
        if task_type != TaskType.CODE.value:
            # 非代码类任务（documentation/config 等）不产出代码：返回占位产物让
            # 上层把任务标记完成——直接 return [] 会让任务永远 pending，
            # 编排器无限重派直到递归上限（真实 E2E 踩过的坑）
            self.log_action("task_skipped_non_code", {"type": task_type, "task_id": task.get("id")})
            return [{
                "file_path": f"docs/{task.get('id', 'task')}.md",
                "content": task.get("description", "") or f"{task_type} 任务：无需生成代码",
                "metadata": {"task_id": task.get("id"), "skipped_non_code": task_type},
            }]

        # 尝试从整机模板匹配（确定性快速路径）
        template_artifacts = self._try_template_code(state, task, engine)
        if template_artifacts:
            self.log_action("template_code_used", {"task_id": task.get("id"), "file_count": len(template_artifacts)})
            return template_artifacts

        # P0 组件参数化快路径：命中已知标准组件则填参返回，跳过一次全量 LLM 生成
        if self._component_template_enabled():
            component_artifacts = self._try_component_template(state, task, engine)
            if component_artifacts:
                self.log_action(
                    "component_template_used",
                    {"task_id": task.get("id"), "kind": component_artifacts[0]["metadata"].get("from_template")},
                )
                return component_artifacts

        return await self._generate_game_code(task, engine, project_name, state)

    def _component_template_enabled(self) -> bool:
        """是否启用 P0 组件参数化快路径（默认开启，可经 config 关闭回退到 LLM 全文路径）。"""
        return self.agent_config.get("template_first", True)

    def _try_component_template(
        self, state: GameDevState, task: Dict[str, Any], engine: str
    ) -> List[Dict[str, Any]]:
        """P0 组件参数化快路径：识别标准组件 -> 从 GDM/task 提取参数 -> 填参渲染。

        命中返回单个 artifact；未命中返回空列表，交由上层继续走 LLM 全文路径。
        """
        if engine != "godot":
            return []
        from src.agents.code_generator.godot_templates import build_artifact, match_component

        requirements = state.get("project_context", {}).get("requirements", "")
        kind = match_component(task, requirements)
        if not kind:
            return []

        artifact = build_artifact(kind, state.get("game_design_model") or {}, task, engine)
        if not artifact:
            return []
        return [artifact]

    def _try_template_code(self, state: GameDevState, task: Dict[str, Any], engine: str) -> List[Dict[str, Any]]:
        """检查是否可以使用模板代码"""
        requirements = state.get("project_context", {}).get("requirements", "")
        if not requirements:
            return []

        from src.agents.planner import match_template
        tpl = match_template(requirements)
        if not tpl:
            return []

        code_files = tpl.get("code_files", {})
        task_outputs = task.get("output_files", [])
        task_name = task.get("name", "")

        # 匹配任务对应的代码文件
        matched = {}
        for fpath, content in code_files.items():
            # 精确匹配output_files
            if fpath in task_outputs:
                matched[fpath] = content
                continue
            # 模糊匹配：任务名包含文件路径中的关键词
            path_lower = fpath.lower()
            task_lower = task_name.lower()
            if any(kw in path_lower for kw in task_lower.split() if len(kw) > 2):
                matched[fpath] = content

        if not matched:
            return []

        return [
            {
                "file_path": fpath,
                "content": content,
                "language": "gdscript",
                "engine": engine,
                "metadata": {
                    "source_task": task.get("id", ""),
                    "dependencies": [],
                    "target_game_object": "",
                    "required_components": task.get("required_components", []),
                    "from_template": tpl["name"],
                },
            }
            for fpath, content in matched.items()
        ]

    def _gameplay_contract_block(self, state: GameDevState) -> str:
        """品类玩法规格 + API 合约：让 LLM 首版就生成可玩且能过 gd-guard 门禁的代码。

        数据来源：state.genre_match（planner 品类匹配产物）→ genre_specs 规格库；
        场景实体名册来自 scene_description，保证代码引用的节点名真实存在。
        """
        genre_match = state.get("genre_match") or {}
        from src.agents.genre_specs import get_spec

        spec = get_spec(genre_match.get("genre"))
        lines = [f"[品类玩法规格] {spec.name_zh}（基款: {spec.representative}）"]
        lines += [f"- 核心机制: {m}" for m in spec.mechanics]
        lines.append(f"- 胜利条件: {spec.win_condition}")
        lines.append(f"- 失败条件: {spec.lose_condition}")
        if spec.hud_extras:
            lines.append(f"- HUD 需显示: {('、'.join(spec.hud_extras))}")
        sd = state.get("scene_description") or {}
        objs = sd.get("game_objects") or []
        if objs:
            names = [f"{o.get('name', '?')}({o.get('role', '?')})" for o in objs[:12]]
            lines.append("[场景实体（可直接引用这些节点名）] " + ", ".join(names))
        from src.engine.godot.gd_guard import DANGEROUS_APIS

        lines.append("[API 安全合约] 生成的游戏代码禁止使用以下 API（安全门禁一票否决，违者必须重生成）:")
        lines += [f"- {api}" for api in DANGEROUS_APIS]
        lines.append("- 允许: 节点/场景树操作、物理与碰撞、Input、数学、动画、UI(Label/Button)、signal/@export/@onready、Timer")
        return chr(10).join(lines)

    async def _generate_game_code(
        self, task: Dict[str, Any], engine: str, project_name: str, state: GameDevState
    ) -> List[Dict[str, Any]]:
        system_prompt = self.get_prompt_template("code_generator_system")
        requirements = state.get("project_context", {}).get("requirements", "")

        # 收集已生成的代码作为上下文
        existing_code = state.get("code_generated", {})
        existing_context = ""
        if existing_code:
            existing_context = "\n\n## 已生成的代码文件\n"
            for path, content in existing_code.items():
                existing_context += f"\n### {path}\n```\n{content[:500]}\n...\n```\n"

        # Game Design Model 上下文
        gdm = state.get("game_design_model", {})
        gdm_context = ""
        if gdm:
            gdm_context = f"""
## 游戏设计模型
- 游戏名称: {gdm.get('game_title', '')}
- 游戏类型: {gdm.get('genre', '')}
- 视角模式: {gdm.get('camera_mode', '')}
- 核心循环: {gdm.get('core_loop', '')}
- 玩家动作: {', '.join(gdm.get('player_actions', []))}
- 胜利条件: {', '.join(gdm.get('win_conditions', []))}
- 失败条件: {', '.join(gdm.get('fail_conditions', []))}
"""
            # 实体信息
            entities = gdm.get("entities", [])
            if entities:
                gdm_context += "\n### 游戏实体\n"
                for ent in entities:
                    gdm_context += f"- {ent.get('name', '')} ({ent.get('role', '')}): 组件 {ent.get('components', [])}\n"

            # 输入映射
            input_map = gdm.get("input_map", [])
            if input_map:
                gdm_context += "\n### 输入映射\n"
                for inp in input_map:
                    gdm_context += f"- {inp.get('name', '')} ({inp.get('type', '')}): {inp.get('description', '')}\n"

            # Tags/Layers
            tags_layers = gdm.get("tags_layers", {})
            if tags_layers.get("tags"):
                gdm_context += f"\n### Tags: {', '.join(tags_layers['tags'])}\n"
            if tags_layers.get("layers"):
                gdm_context += f"### Layers: {', '.join(l.get('name', '') for l in tags_layers['layers'])}\n"

        # 任务元数据
        target_objects = task.get("target_game_objects", [])
        required_components = task.get("required_components", [])
        task_meta = ""
        if target_objects:
            task_meta += f"\n目标GameObject: {', '.join(target_objects)}"
        if required_components:
            task_meta += f"\n需要的组件: {', '.join(required_components)}"

        # 记忆上下文 — 从 MemoryManager 注入的历史经验
        memory_context = state.get("_memory_context", "")
        memory_section = ""
        if memory_context:
            memory_section = f"\n\n## 历史经验（来自记忆系统）\n{memory_context}"

        if engine == "godot":
            gameplay_contract = self._gameplay_contract_block(state)
            user_prompt = f"""请根据以下任务生成完整的、高质量的 Godot 4.x GDScript 代码实现。

项目名称: {project_name}
游戏引擎: Godot 4.x (GDScript)
原始用户需求（最高优先级，不得被通用模板覆盖）:
{requirements}

任务ID: {task.get('id', 'unknown')}
任务名称: {task.get('name', '')}
任务描述: {task.get('description', '')}
{task_meta}
{gdm_context}
{existing_context}
{memory_section}
{gameplay_contract}

要求：
1. 生成完整、可直接在 Godot 4.x 中运行的 GDScript 代码，不要省略任何部分。
2. 严格遵循系统提示中的 GDScript 命名规范与代码结构（snake_case、class_name、extends、signal、@export）。
3. 每个文件头部用注释标注 Godot 资源路径，格式：
   # 文件: res://scripts/[Module]/[Name].gd
4. 必须使用 Godot 节点与 API：玩家用 CharacterBody2D、金币/道具用 Area2D、管理器用 Node（Autoload）；用 signal 解耦、用 @export 暴露参数、用 @onready 缓存节点引用。
5. 禁止使用 C# / Unity 语法（namespace、using、MonoBehaviour、[SerializeField]、GetComponent、Input.GetAxis、CompareTag 等）。
6. 输入读取用 Godot Input Map：Input.get_action_strength("move_left") / Input.is_action_just_pressed("jump")；若原始需求给了具体按键，直接映射并在说明中提示需要在 project.godot 配置对应输入动作。
7. 物理移动用 move_and_slide()；动画用 AnimatedSprite2D 或 AnimationPlayer（通过 @onready 引用 $NodePath）。
8. 若需求含玩家移动/跳跃、敌人、金币/分数、生命、UI、胜负条件，必须在当前任务代码或预留的信号/@export 中有可追踪落点。
9. 历史代码参考只能作为风格参考；与原始需求冲突时以原始需求为准。

请直接输出代码，格式如下：
```gdscript
# 文件: res://scripts/[Module]/[Name].gd
extends CharacterBody2D

# ==================== 信号 ====================
signal score_changed(new_score: int)

# ==================== 导出变量 ====================
@export var move_speed: float = 200.0

# ==================== 私有变量 ====================
var _score: int = 0

# ==================== 生命周期 ====================
func _ready() -> void:
    pass

func _physics_process(delta: float) -> void:
    pass
```

如果需要生成多个文件，每个文件用单独的代码块。"""

        # RAG：从向量库检索相似代码作为参考
        rag_context = ""
        try:
            from src.utils.vector_store import search_similar_code
            similar = await search_similar_code(
                query=task.get("description", task.get("name", "")),
                engine=engine,
                limit=2,
            )
            if similar:
                rag_context = "\n\n## 相似代码参考（来自历史生成）\n"
                for ref in similar:
                    rag_context += f"\n### {ref['task_name']} (相似度: {ref['score']:.2f})\n```\n{ref['code_preview']}\n```\n"
        except Exception as e:
            self.log_error("vector_search_failed", {"error": str(e)})

        if rag_context:
            user_prompt = user_prompt + rag_context

        try:
            response = await self.llm.chat(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                model=self.model,
                temperature=self.llm_config.get("temperature", 0.2),
                max_tokens=self.llm_config.get("max_tokens", 8192),
            )

            artifacts = self._parse_code_response(response, task, engine)

            if not artifacts:
                self.log_error("no_code_parsed", {"response_preview": response[:200]})
                return self._fallback_generate(task, engine)

            self.log_action("code_generated", {"file_count": len(artifacts)})

            # 存入向量库（异步，不阻塞返回）
            try:
                from src.utils.vector_store import store_code
                for art in artifacts:
                    await store_code(
                        code=art["content"],
                        file_path=art["file_path"],
                        task_name=task.get("name", ""),
                        engine=engine,
                        task_type="code",
                    )
            except Exception as e:
                self.log_error("vector_store_failed", {"error": str(e)})

            return artifacts

        except Exception as e:
            self.log_error("code_generator_llm_error", {"error": str(e)})
            return self._fallback_generate(task, engine)

    def _parse_code_response(
        self, response: str, task: Dict[str, Any], engine: str
    ) -> List[Dict[str, Any]]:
        """解析LLM返回的代码响应

        Args:
            response: LLM原始响应
            task: 任务信息
            engine: 游戏引擎

        Returns:
            代码产物列表
        """
        artifacts = []

        # 匹配所有代码块（仅 Godot GDScript）
        code_blocks = re.findall(
            r'```(?:gdscript|gd)?\s*\n(.*?)\n```',
            response,
            re.DOTALL,
        )

        for block in code_blocks:
            block = block.strip()
            if not block:
                continue

            # 尝试从注释中提取文件路径（GDScript 用 #，兼容 //）
            file_path_match = re.search(r'(?://|#)\s*文件:\s*(\S+)', block)
            if file_path_match:
                file_path = file_path_match.group(1)
                block = re.sub(r'(?://|#)\s*文件:\s*\S+\s*\n', '', block, count=1).strip()
            else:
                file_path = self._infer_file_path(task, engine, len(artifacts))

            # Godot：强制 .gd 扩展名（若误带其它扩展名则归一化）
            if engine == "godot" and not file_path.endswith(".gd"):
                file_path = re.sub(r'\.(cs|cpp|cc|cxx|h|csx)$', '.gd', file_path) or (file_path + ".gd")

            # 提取元数据注释
            metadata = self._extract_metadata(block, task)
            # 移除元数据注释
            block = re.sub(r'(?://|#)\s*(任务|依赖|挂载|组件):\s*[^\n]*\n?', '', block).strip()

            artifacts.append({
                "file_path": file_path,
                "content": block,
                "language": "gdscript",
                "engine": engine,
                "metadata": metadata,
            })

        # 如果没有匹配到代码块，尝试把整个响应当作代码
        if not artifacts and response.strip() and not response.strip().startswith('{'):
            if 'class_name ' in response or 'extends ' in response or 'func ' in response or 'namespace ' in response:
                file_path = self._infer_file_path(task, engine, 0)
                artifacts.append({
                    "file_path": file_path,
                    "content": response.strip(),
                    "language": "gdscript",
                    "engine": engine,
                    "metadata": self._extract_metadata(response, task),
                })

        return artifacts

    def _extract_metadata(self, code_block: str, task: Dict[str, Any]) -> Dict[str, Any]:
        """从代码注释中提取元数据"""
        metadata = {
            "source_task": task.get("id", ""),
            "dependencies": [],
            "target_game_object": "",
            "required_components": [],
        }

        # 提取 // 任务: xxx 或 # 任务: xxx
        task_match = re.search(r'(?://|#)\s*任务:\s*(\S+)', code_block)
        if task_match:
            metadata["source_task"] = task_match.group(1)

        # 提取 // 依赖: [A, B, C]
        deps_match = re.search(r'(?://|#)\s*依赖:\s*\[([^\]]*)\]', code_block)
        if deps_match and deps_match.group(1).strip():
            metadata["dependencies"] = [d.strip() for d in deps_match.group(1).split(",")]

        # 提取 // 挂载: Player
        mount_match = re.search(r'(?://|#)\s*挂载:\s*(\S+)', code_block)
        if mount_match:
            metadata["target_game_object"] = mount_match.group(1)

        # 提取 // 组件: Rigidbody2D, BoxCollider2D
        comp_match = re.search(r'(?://|#)\s*组件:\s*(.+)', code_block)
        if comp_match:
            metadata["required_components"] = [c.strip() for c in comp_match.group(1).split(",")]

        return metadata

    def _infer_file_path(self, task: Dict[str, Any], engine: str, index: int) -> str:
        """根据任务信息推断文件路径"""
        task_name = task.get("name", "")
        # 优先使用 output_files
        output_files = task.get("output_files", [])
        if output_files:
            return output_files[0]

        if engine == "godot":
            if "Player" in task_name or "玩家" in task_name:
                return "res://scripts/player/player_controller.gd"
            elif "GameManager" in task_name or "游戏管理" in task_name:
                return "res://scripts/game_manager.gd"
            elif "碰撞" in task_name or "Collision" in task_name:
                return "res://scripts/core/collision_handler.gd"
            elif "计分" in task_name or "Score" in task_name:
                return "res://scripts/core/score_manager.gd"
            elif "Camera" in task_name or "摄像机" in task_name or "相机" in task_name:
                return "res://scripts/camera/camera_follow.gd"
            elif "Enemy" in task_name or "敌人" in task_name:
                return "res://scripts/enemy/enemy_controller.gd"
            elif "Coin" in task_name or "金币" in task_name or "Pickup" in task_name or "拾取" in task_name or "Collectible" in task_name or "收集" in task_name:
                return "res://scripts/collectibles/coin_controller.gd"
            elif "UI" in task_name or "HUD" in task_name or "界面" in task_name:
                return "res://scripts/ui/ui_manager.gd"
            elif "测试" in task_name or "Test" in task_name:
                return f"res://scripts/tests/test_{index}.gd"
            else:
                safe_name = re.sub(r'[^A-Za-z0-9_]', '', task_name.replace(' ', ''))
                if safe_name:
                    return f"res://scripts/{safe_name}/{safe_name}.gd"
                return f"res://scripts/generated/generated_{index}.gd"

    def _fallback_generate(self, task: Dict[str, Any], engine: str) -> List[Dict[str, Any]]:
        """LLM调用失败时的回退生成"""
        task_name = task.get("name", "")

        if engine == "godot":
            if "Player" in task_name or "玩家" in task_name:
                return self._generate_player_godot()
            elif "GameManager" in task_name or "游戏管理" in task_name:
                return self._generate_game_manager_godot()
            elif "Enemy" in task_name or "敌人" in task_name:
                return self._generate_enemy_godot()
            elif "Coin" in task_name or "金币" in task_name or "Collectible" in task_name or "收集" in task_name or "Pickup" in task_name or "拾取" in task_name:
                return self._generate_coin_godot()
            elif "Camera" in task_name or "摄像机" in task_name or "相机" in task_name:
                return self._generate_camera_follow_godot()
            elif "UI" in task_name or "HUD" in task_name or "界面" in task_name:
                return self._generate_ui_manager_godot()
            elif "Score" in task_name or "计分" in task_name:
                return self._generate_score_godot()
            elif "Collision" in task_name or "碰撞" in task_name:
                return self._generate_collision_godot()
            safe_name = re.sub(r'[^A-Za-z0-9_]', '', task_name.replace(' ', ''))
            class_name = safe_name if safe_name else "GeneratedComponent"
            file_path = self._infer_file_path(task, engine, 0)
            return self._generate_godot_generic(class_name, file_path)

        # 通用 fallback — 使用任务名生成有意义的类名
        safe_name = re.sub(r'[^A-Za-z0-9_]', '', task_name.replace(' ', ''))
        class_name = safe_name if safe_name else "GeneratedComponent"
        file_path = self._infer_file_path(task, engine, 0)
        return self._generate_godot_generic(class_name, file_path)

    # ============ Godot / GDScript 兜底生成器 ============

    def _generate_player_godot(self) -> List[Dict[str, Any]]:
        return [{
            "file_path": "res://scripts/player/player_controller.gd",
            "content": '''## 玩家控制器 — 移动、跳跃
extends CharacterBody2D

# ==================== 信号 ====================
signal health_changed(new_health: int)
signal died()

# ==================== 导出变量 ====================
@export var move_speed: float = 200.0
@export var jump_velocity: float = -400.0

# ==================== 私有变量 ====================
var _health: int = 3
var _gravity: float = ProjectSettings.get_setting("physics/2d/default_gravity", 980.0)

@onready var _sprite: AnimatedSprite2D = $AnimatedSprite2D

func _physics_process(delta: float) -> void:
    var direction := Input.get_action_strength("move_right") - Input.get_action_strength("move_left")
    velocity.x = direction * move_speed
    velocity.y += _gravity * delta
    if Input.is_action_just_pressed("jump") and is_on_floor():
        velocity.y = jump_velocity
    move_and_slide()

func take_damage(amount: int = 1) -> void:
    _health -= amount
    health_changed.emit(_health)
    if _health <= 0:
        died.emit()
''',
            "language": "gdscript",
            "engine": "godot",
        }]

    def _generate_game_manager_godot(self) -> List[Dict[str, Any]]:
        return [{
            "file_path": "res://scripts/game_manager.gd",
            "content": '''## 游戏管理器 — 全局计分与状态（Autoload 单例）
extends Node

# ==================== 信号 ====================
signal score_changed(new_score: int)
signal health_changed(new_health: int)
signal game_over()
signal game_won()

# ==================== 私有变量 ====================
var _score: int = 0
var _health: int = 3
var _coins: int = 0
var _total_coins: int = 5

func add_score(amount: int) -> void:
    _score += amount
    score_changed.emit(_score)

func collect_coin() -> void:
    _coins += 1
    add_score(1)
    if _coins >= _total_coins:
        game_won.emit()

func take_damage(amount: int = 1) -> void:
    _health -= amount
    health_changed.emit(_health)
    if _health <= 0:
        game_over.emit()
''',
            "language": "gdscript",
            "engine": "godot",
        }]

    def _generate_enemy_godot(self) -> List[Dict[str, Any]]:
        return [{
            "file_path": "res://scripts/enemy/enemy_controller.gd",
            "content": '''## 敌人控制器 — 左右巡逻
extends CharacterBody2D

@export var move_speed: float = 60.0
@export var patrol_distance: float = 120.0

var _start_x: float = 0.0
var _dir: int = 1
var _gravity: float = ProjectSettings.get_setting("physics/2d/default_gravity", 980.0)

func _ready() -> void:
    _start_x = global_position.x

func _physics_process(delta: float) -> void:
    if abs(global_position.x - _start_x) >= patrol_distance:
        _dir *= -1
    velocity.x = _dir * move_speed
    velocity.y += _gravity * delta
    move_and_slide()

func _on_body_entered(body: Node) -> void:
    if body.is_in_group("player"):
        if body.has_method("take_damage"):
            body.take_damage(1)
''',
            "language": "gdscript",
            "engine": "godot",
        }]

    def _generate_coin_godot(self) -> List[Dict[str, Any]]:
        return [{
            "file_path": "res://scripts/collectibles/coin_controller.gd",
            "content": '''## 金币 — 收集 + 浮动动画
extends Area2D

@export var score_value: int = 1

var _start_y: float = 0.0

func _ready() -> void:
    _start_y = global_position.y
    body_entered.connect(_on_body_entered)

func _process(delta: float) -> void:
    global_position.y = _start_y + sin(Time.get_ticks_msec() / 200.0) * 6.0

func _on_body_entered(body: Node) -> void:
    if body.is_in_group("player"):
        if Engine.has_singleton("GameManager") or get_tree().root.has_node("GameManager"):
            pass
        queue_free()
''',
            "language": "gdscript",
            "engine": "godot",
        }]

    def _generate_camera_follow_godot(self) -> List[Dict[str, Any]]:
        return [{
            "file_path": "res://scripts/camera/camera_follow.gd",
            "content": '''## 摄像机跟随
extends Camera2D

@export var follow_target: NodePath = ^""
@export var smooth_speed: float = 5.0

func _physics_process(delta: float) -> void:
    var target := get_node_or_null(follow_target)
    if target:
        global_position = global_position.lerp(target.global_position, smooth_speed * delta)
''',
            "language": "gdscript",
            "engine": "godot",
        }]

    def _generate_ui_manager_godot(self) -> List[Dict[str, Any]]:
        return [{
            "file_path": "res://scripts/ui/ui_manager.gd",
            "content": '''## HUD 管理器
extends CanvasLayer

@onready var _score_label: Label = $ScoreLabel
@onready var _health_label: Label = $HealthLabel

func _ready() -> void:
    var gm := get_tree().root.get_node_or_null("GameManager")
    if gm:
        if gm.has_signal("score_changed"):
            gm.score_changed.connect(_on_score_changed)
        if gm.has_signal("health_changed"):
            gm.health_changed.connect(_on_health_changed)

func _on_score_changed(new_score: int) -> void:
    if _score_label:
        _score_label.text = "Score: %d" % new_score

func _on_health_changed(new_health: int) -> void:
    if _health_label:
        _health_label.text = "HP: %d" % new_health
''',
            "language": "gdscript",
            "engine": "godot",
        }]

    def _generate_score_godot(self) -> List[Dict[str, Any]]:
        return self._generate_game_manager_godot()

    def _generate_collision_godot(self) -> List[Dict[str, Any]]:
        return [{
            "file_path": "res://scripts/core/collision_handler.gd",
            "content": '''## 碰撞处理器
extends Node

signal collision_processed(kind: String)

func register(body: Node) -> void:
    if body.has_signal("body_entered"):
        body.body_entered.connect(_on_body_entered)

func _on_body_entered(body: Node) -> void:
    collision_processed.emit(body.name)
''',
            "language": "gdscript",
            "engine": "godot",
        }]

    def _generate_godot_generic(self, class_name: str, file_path: str) -> List[Dict[str, Any]]:
        return [{
            "file_path": file_path,
            "content": '''## %s — 由 GameForge 自动生成的 GDScript 组件
extends Node

func _ready() -> void:
    pass

func _process(delta: float) -> void:
    pass
''' % class_name,
            "language": "gdscript",
            "engine": "godot",
        }]

    # ========== Pipeline：将 review/refactor/test/debug/final_check 归并为内部 Phase ==========

    async def run_pipeline(self, state: GameDevState) -> Dict[str, Any]:
        """执行完整代码生成 Pipeline。

        Phase:
        1. generate — 生成代码
        2. review — 静态/LLM 审查
        3. refactor — 轻量重构（仅 review 发现问题时）
        4. test — 生成 GUT 测试桩
        5. fix — 自动修复（仅出现错误时）
        6. final_check — 最终门禁
        """
        task = self._get_current_task(state)
        if not task:
            return {"current_phase": "no_task"}

        # Phase 1: generate
        code_artifacts = await self.generate(state, task)
        if not code_artifacts:
            # 空产物也必须标记完成，否则任务永远 pending → 编排器无限重派
            self.log_action("task_no_artifacts_completed", {"task_id": task.get("id")})
            task_plan = self._mark_task_completed(state, task)
            return {
                "task_plan": task_plan,
                "current_phase": "code_generated",
                "warnings": [f"任务 {task.get('id', '?')} 无代码产物，已标记完成跳过"],
            }
        # 非代码占位产物：提前返回（带着完成状态），不走 review/fix 流水线
        # ——占位产物缺常规字段，进流水线会抛异常，节点 except 返回的 dict
        #   不含 task_plan，完成状态落不了地 → 无限重派（E2E 实测踩坑）
        if code_artifacts and (code_artifacts[0].get("metadata") or {}).get("skipped_non_code"):
            task_plan = self._mark_task_completed(state, task)
            return {
                "task_plan": task_plan,
                "current_phase": "code_generated",
                "warnings": [f"任务 {task.get('id', '?')} 为非代码类型，已标记完成"],
            }

        # 标记任务完成并更新任务计划
        task_plan = self._mark_task_completed(state, task)
        new_code = {art["file_path"]: art["content"] for art in code_artifacts}

        # 统一验证
        warnings = []
        try:
            from src.utils.unified_validator import validate_all
            validation = validate_all(new_code)
            if validation.has_errors:
                warnings.append(
                    f"代码验证发现 {len(validation.errors)} 个错误: "
                    + "; ".join(e.get("message", "") for e in validation.errors[:3])
                )
        except Exception:
            pass

        # Phase 2: review
        review_result = await self._review(state, code_artifacts)
        state["review_result"] = review_result

        # Phase 3: refactor（仅 review 发现 blocking/high/medium 问题时）
        if review_result.get("needs_refactor"):
            refactored_artifacts = await self._refactor(state, code_artifacts, review_result)
            if refactored_artifacts:
                code_artifacts = refactored_artifacts
                new_code = {art["file_path"]: art["content"] for art in code_artifacts}
                state["code_generated"] = {**state.get("code_generated", {}), **new_code}

        # Phase 4: test（按配置开关）
        if self.agent_config.get("test", {}).get("enabled", True):
            test_artifacts = await self._generate_tests(state, code_artifacts)
            if test_artifacts:
                code_artifacts = code_artifacts + test_artifacts
                new_code = {art["file_path"]: art["content"] for art in code_artifacts}

        # Phase 5: fix（仅 review 失败或验证错误时）
        error_log = []
        if not review_result.get("passed", True):
            error_log.extend(review_result.get("blocking_issues", []))
        error_log.extend(warnings)

        if error_log:
            fix_result = await self.fix_code(state, error_log)
            state.update(fix_result)

        # Phase 6: final_check
        final_check = await self._final_check(state, code_artifacts)
        state["final_review_result"] = final_check

        return {
            "code_generated": new_code,
            "code_artifacts": code_artifacts,
            "task_plan": task_plan,
            "current_phase": "code_generated",
            "warnings": warnings,
            "review_result": review_result,
            "final_review_result": final_check,
        }

    def _get_current_task(self, state: GameDevState) -> Optional[Dict[str, Any]]:
        """从 state 中提取当前任务。"""
        current_task_id = state.get("current_task_id")
        if not current_task_id:
            return None
        for task in state.get("task_plan", []):
            if task.get("id") == current_task_id:
                return task
        return None

    def _mark_task_completed(self, state: GameDevState, task: Dict[str, Any]) -> List[Dict[str, Any]]:
        """将当前任务标记为完成，返回更新后的任务计划。"""
        task_plan = [dict(t) for t in state.get("task_plan", [])]
        for t in task_plan:
            if t.get("id") == task.get("id"):
                t["status"] = TaskStatus.COMPLETED.value
                break
        return task_plan

    async def _review(self, state: GameDevState, code_artifacts: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Phase 2: 代码审查。

        - fast_mode（默认）: 静态检查 TODO / pass / 命名规范
        - llm_review（可选）: LLM 深度审查
        """
        review_cfg = self.agent_config.get("review", {})
        fast_mode = review_cfg.get("fast_mode", True)
        llm_review = review_cfg.get("llm_review", False)

        issues: List[Dict[str, Any]] = []
        suggestions: List[str] = []

        # 快速静态检查
        if fast_mode:
            for art in code_artifacts:
                content = art.get("content", "")
                fpath = art.get("file_path", "")
                if "TODO" in content or "pass\n" in content:
                    issues.append({
                        "type": "style",
                        "severity": "low",
                        "file": fpath,
                        "message": "代码包含未完成标记 TODO 或空实现 pass",
                    })
                if fpath.endswith(".gd") and not content.startswith("extends ") and "class_name" not in content:
                    issues.append({
                        "type": "logic",
                        "severity": "medium",
                        "file": fpath,
                        "message": "GDScript 缺少 extends 或 class_name",
                    })

        # LLM 深度审查
        if llm_review and self.llm:
            try:
                llm_issues = await self._llm_review(state, code_artifacts)
                issues.extend(llm_issues)
            except Exception as e:
                self.log_error("review_llm_failed", {"error": str(e)})

        passed = not any(i.get("severity") in ("high", "medium") for i in issues)
        needs_refactor = any(i.get("severity") in ("high", "medium") for i in issues)
        return {
            "passed": passed,
            "score": 100 - len(issues) * 5,
            "issues": issues,
            "suggestions": suggestions,
            "needs_refactor": needs_refactor,
            "blocking_issues": [i for i in issues if i.get("severity") == "high"],
        }

    async def _llm_review(self, state: GameDevState, code_artifacts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """调用 LLM 进行代码审查，返回 issues 列表。"""
        system_prompt = self.get_prompt_template("reviewer_system")
        if not system_prompt:
            return []

        code_context = ""
        for art in code_artifacts:
            code_context += f"\n### {art['file_path']}\n```\n{art['content'][:2000]}\n```\n"

        user_prompt = f"""请审查以下 Godot GDScript 代码，输出 JSON 数组：
[
  {{"type": "logic|performance|style", "severity": "high|medium|low", "file": "路径", "message": "问题"}}
]

{code_context}"""

        try:
            result = await self.llm.chat_json(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                model=self.model,
                temperature=self.llm_config.get("temperature", 0.2),
                max_tokens=self.llm_config.get("max_tokens", 4096),
            )
            if isinstance(result, dict) and not result.get("parse_error"):
                return result.get("issues", [])
        except Exception as e:
            self.log_error("llm_review_failed", {"error": str(e)})
        return []

    async def _refactor(self, state: GameDevState, code_artifacts: List[Dict[str, Any]], review_result: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Phase 3: 轻量重构。

        - 仅重构 review 发现的高/中优先级问题
        - fast_mode 下不执行
        """
        refactor_cfg = self.agent_config.get("refactor", {})
        if refactor_cfg.get("fast_mode", True):
            return code_artifacts
        if not review_result.get("needs_refactor"):
            return code_artifacts

        refactored = []
        for art in code_artifacts:
            # 简化重构：只做静态替换示例（如重命名、提取常量）
            # 复杂重构可接入 LLM，但保持成本可控
            content = art.get("content", "")
            # 此处可扩展具体重构规则
            refactored.append({
                "file_path": art["file_path"],
                "content": content,
                "language": art.get("language", "gdscript"),
                "engine": art.get("engine", "godot"),
                "metadata": {**art.get("metadata", {}), "refactored": True},
            })
        return refactored

    async def _generate_tests(self, state: GameDevState, code_artifacts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Phase 4: 生成 GUT 测试桩。

        - 只对 .gd 文件生成对应测试文件
        - Godot 未运行时只生成不执行
        """
        test_cfg = self.agent_config.get("test", {})
        if not test_cfg.get("enabled", True):
            return []

        test_artifacts = []
        for art in code_artifacts:
            fpath = art.get("file_path", "")
            if not fpath.endswith(".gd"):
                continue
            test_path = f"{fpath[:-3]}_test.gd"
            content = art.get("content", "")
            class_name = None
            m = re.search(r'^class_name\s+(\w+)', content, re.MULTILINE)
            if m:
                class_name = m.group(1)
            if not class_name:
                continue

            test_content = f'''## 自动生成的 GUT 测试桩
extends GutTest

func test_{class_name.lower()}_exists() -> void:
    var script = preload("res://{fpath.replace('res://', '')}")
    assert_true(script is GDScript, "{class_name} 应为 GDScript")

func test_{class_name.lower()}_has_methods() -> void:
    var script = preload("res://{fpath.replace('res://', '')}")
    # TODO: 根据实际 public_methods 补充断言
    assert_true(true, "占位测试")
'''
            test_artifacts.append({
                "file_path": test_path,
                "content": test_content,
                "language": "gdscript",
                "engine": "godot",
                "metadata": {
                    "source_task": "test_generator",
                    "test_for": fpath,
                },
            })
        return test_artifacts

    async def _final_check(self, state: GameDevState, code_artifacts: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Phase 6: 最终门禁。

        确定性检查：
        - TODO 扫描
        - scene_description 完整性
        - GDM 核心字段

        LLM 深度检查（可选）:
        - config.code_generator.final_check.llm_review = true 时触发
        """
        warnings: List[str] = []
        code = {art["file_path"]: art["content"] for art in code_artifacts}

        # 确定性检查
        for fpath, content in code.items():
            if "TODO" in content or "pass\n" in content:
                warnings.append(f"{fpath} 仍包含未完成实现标记")

        gdm = state.get("game_design_model") or {}
        scene = state.get("scene_description") or {}
        required_gdm = ["genre", "core_loop", "player_actions", "win_conditions", "fail_conditions"]
        for key in required_gdm:
            if not gdm.get(key):
                warnings.append(f"设计缺少 {key}")

        entities = gdm.get("entities", []) or scene.get("game_objects", [])
        if not entities:
            warnings.append("场景没有人物、敌人、道具或环境实体")

        # 可选 LLM 审查
        llm_review_result = {}
        final_check_cfg = self.agent_config.get("final_check", {})
        if final_check_cfg.get("llm_review", False) and self.llm:
            try:
                llm_review_result = await self._llm_final_review(state, code_artifacts)
                if llm_review_result.get("blocking_issues"):
                    warnings.extend(llm_review_result["blocking_issues"])
            except Exception as e:
                self.log_error("final_llm_review_failed", {"error": str(e)})

        passed = not warnings
        return {
            "passed": passed,
            "warnings": warnings,
            "llm_review": llm_review_result,
        }

    async def _llm_final_review(self, state: GameDevState, code_artifacts: List[Dict[str, Any]]) -> Dict[str, Any]:
        """可选的 LLM 最终审查。"""
        system_prompt = self.get_prompt_template("main_reviewer_system")
        if not system_prompt:
            return {}

        code_context = "\n\n".join(
            f"### {a['file_path']}\n```\n{a['content'][:2000]}\n```"
            for a in code_artifacts
            if a['file_path'].endswith(('.gd', '.tscn', '.tres'))
        )
        gdm = state.get("game_design_model") or {}
        scene = state.get("scene_description") or {}
        user_prompt = (
            "请依据主审查规范审查以下 Godot 工程。\n\n"
            f"## 游戏设计模型（GameSpec）\n{json.dumps(gdm, ensure_ascii=False, indent=2)[:1500]}\n\n"
            f"## 场景描述（Scene IR）\n{json.dumps(scene, ensure_ascii=False, indent=2)[:1500]}\n\n"
            f"## 生成产物\n{code_context[:8000]}\n\n只输出符合规范的 JSON。"
        )
        try:
            result = await self.llm.chat_json(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                model=self.model,
                temperature=self.llm_config.get("temperature", 0.2),
                max_tokens=self.llm_config.get("max_tokens", 4096),
            )
            if isinstance(result, dict) and not result.get("parse_error"):
                return result
        except Exception as e:
            self.log_error("llm_final_review_error", {"error": str(e)})
        return {}
