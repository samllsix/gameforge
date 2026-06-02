"""GameForge - 代码生成Agent模块

负责根据任务描述生成游戏代码。
"""

import re
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional
from src.agents.base import BaseAgent
from src.core.state.game_state import GameDevState, AgentType, TaskType
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

    # 提取类名
    class_match = re.search(r'public\s+(?:partial\s+)?(?:class|struct|interface)\s+(\w+)', code)
    if class_match:
        meta.class_name = class_match.group(1)

    # 提取namespace
    ns_match = re.search(r'namespace\s+([\w.]+)', code)
    if ns_match:
        meta.namespace = ns_match.group(1)

    # 从注释提取挂载目标
    mount_match = re.search(r'//\s*挂载:\s*(\S+)', code)
    if mount_match:
        meta.target_game_object = mount_match.group(1)

    # 从注释提取组件
    comp_match = re.search(r'//\s*组件:\s*(.+)', code)
    if comp_match:
        meta.required_components = [c.strip() for c in comp_match.group(1).split(",")]

    # 从注释提取依赖
    deps_match = re.search(r'//\s*依赖:\s*\[([^\]]*)\]', code)
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
    - 根据任务描述生成代码
    - 支持Unity C#和Unreal C++
    - 遵循项目编码规范
    """

    def __init__(self, config: Dict[str, Any]):
        super().__init__(AgentType.CODE_GENERATOR, config)
        self.llm = get_llm_client(config, provider=self.provider, model=self.model)
        self.supported_engines = self.agent_config.get("supported_engines", ["unity", "unreal"])

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

    def generate_readme(self, project_name: str, task_plan: list, code_files: dict) -> str:
        """生成Unity项目README"""
        files_section = ""
        for path in sorted(code_files.keys()):
            if path.endswith(".cs"):
                files_section += f"- `{path}`\n"

        tasks_section = ""
        for task in task_plan:
            tasks_section += f"- **{task.get('name', '')}**: {task.get('description', '')}\n"

        # 从代码中提取Tag/Layer引用
        tags = set()
        layers = set()
        inputs = set()
        for content in code_files.values():
            for m in re.finditer(r'CompareTag\("(\w+)"\)', content):
                tags.add(m.group(1))
            for m in re.finditer(r'LayerMask\.GetMask\("(\w+)"\)', content):
                layers.add(m.group(1))
            for m in re.finditer(r'Input\.Get(?:Axis|Button)(?:Raw)?\("(\w+)"\)', content):
                inputs.add(m.group(1))

        settings_section = ""
        if tags:
            settings_section += f"- **Tags**: {', '.join(sorted(tags))}\n"
        if layers:
            settings_section += f"- **Layers**: {', '.join(sorted(layers))}\n"
        if inputs:
            settings_section += f"- **Input Axes**: {', '.join(sorted(inputs))}\n"

        readme = f"""# {project_name} — Unity 项目说明

## 概述
本项目由 GameForge AI 自动生成，包含完整的可运行游戏代码。

## 任务计划
{tasks_section}

## 文件列表
{files_section}

## Unity 配置要求
{settings_section if settings_section else "无特殊配置要求"}

## 运行方法
1. 在 Unity Hub 中打开本项目
2. 确保 Unity 版本 >= 2022.3
3. 打开 `Assets/Scenes/` 中的场景文件
4. 点击 Play 按钮运行

## 注意事项
- 首次打开可能需要等待 Unity 编译完成
- 如果遇到编译错误，请检查 Tag/Layer 是否已正确配置
- 所有脚本使用 GameForge 命名空间
"""
        return readme

    def generate_project_settings(self, code_files: dict) -> str:
        """扫描代码生成Unity项目配置建议"""
        tags = set()
        layers = set()
        inputs = set()
        physics_notes = []

        for path, content in code_files.items():
            if not path.endswith(".cs"):
                continue
            for m in re.finditer(r'CompareTag\("(\w+)"\)', content):
                tags.add(m.group(1))
            for m in re.finditer(r'LayerMask\.GetMask\("(\w+)"\)', content):
                layers.add(m.group(1))
            for m in re.finditer(r'Input\.Get(?:Axis|Button)(?:Raw)?\("(\w+)"\)', content):
                inputs.add(m.group(1))
            if "Physics2D" in content:
                physics_notes.append(f"- `{path}`: 使用 Physics2D")
            if "Rigidbody2D" in content:
                physics_notes.append(f"- `{path}`: 使用 Rigidbody2D")

        doc = f"""# Unity 项目配置建议

以下配置由代码分析自动生成，请在 Unity Editor 中手动设置。

## Tags
"""
        if tags:
            for tag in sorted(tags):
                doc += f"- `{tag}`\n"
        else:
            doc += "- 无自定义Tag要求\n"

        doc += "\n## Layers\n"
        if layers:
            for layer in sorted(layers):
                doc += f"- `{layer}` (建议分配到 Layer 8+)\n"
        else:
            doc += "- 无自定义Layer要求\n"

        doc += "\n## Input Axes\n"
        if inputs:
            for inp in sorted(inputs):
                doc += f"- `{inp}`\n"
        else:
            doc += "- 使用 Unity 默认 Input Axes\n"

        doc += "\n## Physics\n"
        if physics_notes:
            for note in physics_notes:
                doc += note + "\n"
        else:
            doc += "- 无特殊物理设置\n"

        doc += """
## Camera
- 推荐使用 Orthographic 模式（2D游戏）
- 建议 Size: 5-6

## 其他建议
- 确保 Sprite 的 Pixels Per Unit 设置正确（角色: 128, 地块: 64）
- 使用 Point filter 避免 Sprite 模糊
"""
        return doc

    async def generate(self, state: GameDevState, task: Dict[str, Any]) -> List[Dict[str, Any]]:
        self.log_action("generate_code", {"task_id": task.get("id")})

        engine = state.get("project_context", {}).get("engine", "unity")
        project_name = state.get("project_context", {}).get("project_name", "GameForge")

        task_type = task.get("type", TaskType.CODE.value)
        if task_type != TaskType.CODE.value:
            self.log_error("unsupported_task_type", {"type": task_type})
            return []

        # 尝试从模板匹配（确定性快速路径）
        template_artifacts = self._try_template_code(state, task, engine)
        if template_artifacts:
            self.log_action("template_code_used", {"task_id": task.get("id"), "file_count": len(template_artifacts)})
            return template_artifacts

        return await self._generate_game_code(task, engine, project_name, state)

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
                "language": "csharp" if engine == "unity" else "cpp",
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

    async def _generate_game_code(
        self, task: Dict[str, Any], engine: str, project_name: str, state: GameDevState
    ) -> List[Dict[str, Any]]:
        system_prompt = self.get_prompt_template("code_generator_system")

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

        user_prompt = f"""请根据以下任务生成完整的、高质量的代码实现。

项目名称: {project_name}
游戏引擎: {engine}
任务ID: {task.get('id', 'unknown')}
任务名称: {task.get('name', '')}
任务描述: {task.get('description', '')}
{task_meta}
{gdm_context}
{existing_context}

要求：
1. 生成完整可编译的代码，不要省略任何部分
2. 遵循系统提示中的命名规范和代码结构
3. 使用 namespace {project_name.replace(' ', '.')}.[ModuleName] 格式
4. 代码必须是可以直接复制到Unity项目中使用的
5. 代码质量要求（直接生成高质量代码）：
   - 遵循SOLID原则：单一职责、开闭原则
   - 使用#region/#endregion组织代码块
   - 字段使用[SerializeField]私有化，通过属性暴露公共接口
   - 使用事件系统解耦模块间通信
   - 空值检查和边界条件处理
   - 避免FindObjectOfType等性能杀手，改用缓存引用
   - 使用[Header]和[Tooltip]增强Inspector可读性
6. 如果游戏设计模型中有输入映射，使用Input.GetAxis/Input.GetButton读取输入
7. 如果有Tags/Layers引用，使用CompareTag和LayerMask.GetMask
8. 组件之间的引用使用[SerializeField]或事件系统，不要使用Find系列方法

请直接输出代码，格式如下：
```csharp
// 文件: Assets/Scripts/[Module]/[FileName].cs
namespace ...
{
    ...
}
```

如果需要生成多个文件，每个文件用单独的代码块。"""

        try:
            response = await self.llm.chat(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                model=self.model,
                temperature=self.llm_config.get("temperature", 0.3),
                max_tokens=self.llm_config.get("max_tokens", 8192),
            )

            artifacts = self._parse_code_response(response, task, engine)

            if not artifacts:
                self.log_error("no_code_parsed", {"response_preview": response[:200]})
                return self._fallback_generate(task, engine)

            self.log_action("code_generated", {"file_count": len(artifacts)})
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

        # 匹配所有代码块
        code_blocks = re.findall(
            r'```(?:csharp|cs|cpp)?\s*\n(.*?)\n```',
            response,
            re.DOTALL,
        )

        for block in code_blocks:
            block = block.strip()
            if not block:
                continue

            # 尝试从注释中提取文件路径
            file_path_match = re.search(r'//\s*文件:\s*(\S+)', block)
            if file_path_match:
                file_path = file_path_match.group(1)
                block = re.sub(r'//\s*文件:\s*\S+\s*\n', '', block, count=1).strip()
            else:
                file_path = self._infer_file_path(task, engine, len(artifacts))

            # 提取元数据注释
            metadata = self._extract_metadata(block, task)
            # 移除元数据注释
            block = re.sub(r'//\s*(任务|依赖|挂载|组件):\s*[^\n]*\n?', '', block).strip()

            artifacts.append({
                "file_path": file_path,
                "content": block,
                "language": "csharp" if engine == "unity" else "cpp",
                "engine": engine,
                "metadata": metadata,
            })

        # 如果没有匹配到代码块，尝试把整个响应当作代码
        if not artifacts and response.strip() and not response.strip().startswith('{'):
            if 'class ' in response or 'namespace ' in response or 'using ' in response:
                file_path = self._infer_file_path(task, engine, 0)
                artifacts.append({
                    "file_path": file_path,
                    "content": response.strip(),
                    "language": "csharp" if engine == "unity" else "cpp",
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

        # 提取 // 任务: xxx
        task_match = re.search(r'//\s*任务:\s*(\S+)', code_block)
        if task_match:
            metadata["source_task"] = task_match.group(1)

        # 提取 // 依赖: [A, B, C]
        deps_match = re.search(r'//\s*依赖:\s*\[([^\]]*)\]', code_block)
        if deps_match and deps_match.group(1).strip():
            metadata["dependencies"] = [d.strip() for d in deps_match.group(1).split(",")]

        # 提取 // 挂载: Player
        mount_match = re.search(r'//\s*挂载:\s*(\S+)', code_block)
        if mount_match:
            metadata["target_game_object"] = mount_match.group(1)

        # 提取 // 组件: Rigidbody2D, BoxCollider2D
        comp_match = re.search(r'//\s*组件:\s*(.+)', code_block)
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

        if engine == "unity":
            if "Player" in task_name or "玩家" in task_name:
                return "Assets/Scripts/Player/PlayerController.cs"
            elif "GameManager" in task_name or "游戏管理" in task_name:
                return "Assets/Scripts/Core/GameManager.cs"
            elif "碰撞" in task_name or "Collision" in task_name:
                return "Assets/Scripts/Core/CollisionHandler.cs"
            elif "计分" in task_name or "Score" in task_name:
                return "Assets/Scripts/Core/ScoreManager.cs"
            elif "Camera" in task_name or "摄像机" in task_name or "相机" in task_name:
                return "Assets/Scripts/Camera/CameraFollow.cs"
            elif "Enemy" in task_name or "敌人" in task_name:
                return "Assets/Scripts/Enemy/EnemyController.cs"
            elif "Coin" in task_name or "金币" in task_name or "Pickup" in task_name or "拾取" in task_name or "Collectible" in task_name or "收集" in task_name:
                return "Assets/Scripts/Collectibles/CoinController.cs"
            elif "UI" in task_name or "HUD" in task_name or "界面" in task_name:
                return "Assets/Scripts/UI/UIManager.cs"
            elif "测试" in task_name or "Test" in task_name:
                return f"Assets/Scripts/Tests/Test_{index}.cs"
            else:
                # 使用任务名作为文件名，避免 Generated_N
                safe_name = re.sub(r'[^A-Za-z0-9_]', '', task_name.replace(' ', ''))
                if safe_name:
                    return f"Assets/Scripts/{safe_name}/{safe_name}.cs"
                return f"Assets/Scripts/Generated/Generated_{index}.cs"
        else:
            return f"Source/GameForge/Generated/Generated_{index}.cpp"

    def _fallback_generate(self, task: Dict[str, Any], engine: str) -> List[Dict[str, Any]]:
        """LLM调用失败时的回退生成"""
        task_name = task.get("name", "")

        if engine == "unity":
            if "Player" in task_name or "玩家" in task_name:
                return self._generate_player_code(engine)
            elif "GameManager" in task_name or "游戏管理" in task_name:
                return self._generate_game_manager_code(engine)
            elif "Enemy" in task_name or "敌人" in task_name:
                return self._generate_enemy_code(engine)
            elif "Coin" in task_name or "金币" in task_name or "Collectible" in task_name or "收集" in task_name or "Pickup" in task_name or "拾取" in task_name:
                return self._generate_coin_code(engine)
            elif "Camera" in task_name or "摄像机" in task_name or "相机" in task_name:
                return self._generate_camera_follow_code(engine)
            elif "UI" in task_name or "HUD" in task_name or "界面" in task_name:
                return self._generate_ui_manager_code(engine)
            elif "Score" in task_name or "计分" in task_name:
                return self._generate_score_code(engine)
            elif "Collision" in task_name or "碰撞" in task_name:
                return self._generate_collision_code(engine)

        # 通用 fallback — 使用任务名生成有意义的类名
        safe_name = re.sub(r'[^A-Za-z0-9_]', '', task_name.replace(' ', ''))
        class_name = safe_name if safe_name else "GeneratedComponent"
        file_path = self._infer_file_path(task, engine, 0)
        return self._generate_generic_code(class_name, engine, file_path)

    def _generate_player_code(self, engine: str) -> List[Dict[str, Any]]:
        if engine == "unreal":
            return [{
                "file_path": "Source/GameForge/Player/PlayerCharacter.cpp",
                "content": '''#include "PlayerCharacter.h"
#include "GameFramework/CharacterMovementComponent.h"

APlayerCharacter::APlayerCharacter()
{
    PrimaryActorTick.bCanEverTick = true;
    GetCharacterMovement()->MaxWalkSpeed = 600.0f;
    GetCharacterMovement()->JumpZVelocity = 1000.0f;
}

void APlayerCharacter::BeginPlay()
{
    Super::BeginPlay();
}

void APlayerCharacter::Tick(float DeltaTime)
{
    Super::Tick(DeltaTime);
}

void APlayerCharacter::SetupPlayerInputComponent(UInputComponent* PlayerInputComponent)
{
    Super::SetupPlayerInputComponent(PlayerInputComponent);
    PlayerInputComponent->BindAxis("MoveForward", this, &APlayerCharacter::MoveForward);
    PlayerInputComponent->BindAxis("MoveRight", this, &APlayerCharacter::MoveRight);
    PlayerInputComponent->BindAction("Jump", IE_Pressed, this, &ACharacter::Jump);
}

void APlayerCharacter::MoveForward(float Value)
{
    if (Value != 0.0f)
    {
        AddMovementInput(GetActorForwardVector(), Value);
    }
}

void APlayerCharacter::MoveRight(float Value)
{
    if (Value != 0.0f)
    {
        AddMovementInput(GetActorRightVector(), Value);
    }
}''',
                "language": "cpp",
                "engine": "unreal",
            }]
        if engine == "unity":
            return [{
                "file_path": "Assets/Scripts/Player/PlayerController.cs",
                "content": '''using UnityEngine;

namespace GameForge.Player
{
    /// <summary>
    /// 玩家控制器 - 移动、跳跃、sprite动画
    /// </summary>
    public class PlayerController : MonoBehaviour
    {
        #region Fields
        [Header("Movement")]
        [SerializeField] private float _moveSpeed = 5f;
        [SerializeField] private float _jumpForce = 10f;
        [SerializeField] private float _groundCheckDistance = 0.15f;

        [Header("Sprites")]
        [SerializeField] private Sprite _idleSprite;
        [SerializeField] private Sprite _walkASprite;
        [SerializeField] private Sprite _walkBSprite;
        [SerializeField] private Sprite _jumpSprite;
        [SerializeField] private Sprite _hitSprite;

        [Header("Animation")]
        [SerializeField] private float _walkFrameRate = 0.12f;
        [SerializeField] private float _hitDuration = 0.3f;

        private Rigidbody2D _rb;
        private BoxCollider2D _col;
        private SpriteRenderer _sr;
        private float _moveInput;
        private bool _jumpRequested;
        private float _jumpCooldown;
        private int _groundMask;
        private float _animTimer;
        private int _walkFrame;
        private bool _facingRight = true;
        private float _hitTimer;
        #endregion

        #region Unity Lifecycle
        private void Awake()
        {
            _rb = GetComponent<Rigidbody2D>();
            _col = GetComponent<BoxCollider2D>();
            _sr = GetComponent<SpriteRenderer>();
            if (_rb == null || _col == null)
            {
                Debug.LogError("[PlayerController] Required component missing!");
                enabled = false;
                return;
            }
            if (_sr == null) _sr = GetComponentInChildren<SpriteRenderer>();
            _groundMask = LayerMask.GetMask("Ground");
            if (_groundMask == 0) _groundMask = ~LayerMask.GetMask("Player");
        }

        private void Update()
        {
            _moveInput = Input.GetAxisRaw("Horizontal");
            if (Input.GetButtonDown("Jump")) _jumpRequested = true;
            if (_hitTimer > 0) _hitTimer -= Time.deltaTime;
            UpdateAnimation();
        }

        private void FixedUpdate()
        {
            Move();
            _jumpCooldown -= Time.fixedDeltaTime;
            if (_jumpRequested && IsGrounded() && _jumpCooldown <= 0f)
            {
                Jump();
                _jumpRequested = false;
                _jumpCooldown = 0.25f;
            }
            if (!IsGrounded()) _jumpRequested = false;
        }
        #endregion

        #region Ground Detection
        public bool IsGrounded()
        {
            Vector2 origin = (Vector2)transform.position + _col.offset;
            Vector2 size = _col.size * 0.85f;
            var hit = Physics2D.BoxCast(origin, size, 0f, Vector2.down,
                _groundCheckDistance, _groundMask);
            return hit.collider != null;
        }
        #endregion

        #region Movement
        public void Move()
        {
            _rb.velocity = new Vector2(_moveInput * _moveSpeed, _rb.velocity.y);
        }

        public void Jump()
        {
            _rb.velocity = new Vector2(_rb.velocity.x, _jumpForce);
        }
        #endregion

        #region Animation
        private void UpdateAnimation()
        {
            if (_sr == null) return;
            if (_hitTimer > 0) { if (_hitSprite != null) _sr.sprite = _hitSprite; return; }
            bool grounded = IsGrounded();
            if (!grounded) { if (_jumpSprite != null) _sr.sprite = _jumpSprite; }
            else if (Mathf.Abs(_moveInput) > 0.1f)
            {
                _animTimer += Time.deltaTime;
                if (_animTimer >= _walkFrameRate) { _walkFrame = 1 - _walkFrame; _animTimer = 0f; }
                _sr.sprite = _walkFrame == 0 ? _walkASprite : _walkBSprite;
            }
            else { _animTimer = 0f; _walkFrame = 0; if (_idleSprite != null) _sr.sprite = _idleSprite; }
            if ((_moveInput > 0.01f && !_facingRight) || (_moveInput < -0.01f && _facingRight)) Flip();
        }

        private void Flip()
        {
            _facingRight = !_facingRight;
            var scale = transform.localScale; scale.x *= -1f; transform.localScale = scale;
        }
        #endregion

        #region Public API
        public void TakeHit() { _hitTimer = _hitDuration; }
        public bool IsHit => _hitTimer > 0;
        public bool FacingRight => _facingRight;
        #endregion
    }
}''',
                "language": "csharp",
                "engine": "unity",
            }]
        return []

    def _generate_game_manager_code(self, engine: str) -> List[Dict[str, Any]]:
        if engine == "unity":
            return [{
                "file_path": "Assets/Scripts/Core/GameManager.cs",
                "content": '''using UnityEngine;
using UnityEngine.SceneManagement;

namespace GameForge.Core
{
    /// <summary>
    /// 游戏管理器 - 管理游戏状态和流程
    /// </summary>
    public class GameManager : MonoBehaviour
    {
        #region Singleton
        public static GameManager Instance { get; private set; }
        #endregion

        #region Fields
        [SerializeField] private int _maxLives = 3;

        private int _currentLives;
        private int _score;
        private bool _isGameOver;
        #endregion

        #region Properties
        public int CurrentLives => _currentLives;
        public int Score => _score;
        public bool IsGameOver => _isGameOver;
        #endregion

        #region Unity Lifecycle
        private void Awake()
        {
            if (Instance == null)
            {
                Instance = this;
                DontDestroyOnLoad(gameObject);
                InitializeGame();
            }
            else
            {
                Destroy(gameObject);
            }
        }
        #endregion

        #region Public Methods
        public void InitializeGame()
        {
            _currentLives = _maxLives;
            _score = 0;
            _isGameOver = false;
        }

        public void AddScore(int points)
        {
            _score += points;
        }

        public void LoseLife()
        {
            _currentLives--;
            if (_currentLives <= 0)
            {
                GameOver();
            }
        }

        public void GameOver()
        {
            _isGameOver = true;
            Debug.Log("Game Over!");
        }

        public void RestartGame()
        {
            InitializeGame();
            SceneManager.LoadScene(SceneManager.GetActiveScene().buildIndex);
        }
        #endregion
    }
}''',
                "language": "csharp",
                "engine": "unity",
            }]
        return []

    def _generate_collision_code(self, engine: str) -> List[Dict[str, Any]]:
        if engine == "unity":
            return [{
                "file_path": "Assets/Scripts/Core/CollisionHandler.cs",
                "content": '''using UnityEngine;

namespace GameForge.Core
{
    /// <summary>
    /// 碰撞处理器 - 统一管理碰撞检测逻辑
    /// </summary>
    public class CollisionHandler : MonoBehaviour
    {
        #region Events
        public static event System.Action<Collision2D> OnCollision;
        public static event System.Action<Collider2D> OnTrigger;
        #endregion

        #region Fields
        [SerializeField] private string[] _collisionTags = { "Player", "Enemy", "Pickup" };
        [SerializeField] private bool _debugCollisions = false;
        #endregion

        #region Collision Callbacks
        private void OnCollisionEnter2D(Collision2D collision)
        {
            if (!ShouldProcessCollision(collision.gameObject)) return;

            if (_debugCollisions)
                Debug.Log($"Collision with {collision.gameObject.name}");

            OnCollision?.Invoke(collision);
            ProcessCollision(collision);
        }

        private void OnTriggerEnter2D(Collider2D other)
        {
            if (!ShouldProcessCollision(other.gameObject)) return;

            if (_debugCollisions)
                Debug.Log($"Trigger with {other.gameObject.name}");

            OnTrigger?.Invoke(other);
            ProcessTrigger(other);
        }
        #endregion

        #region Processing
        private bool ShouldProcessCollision(GameObject other)
        {
            foreach (var tag in _collisionTags)
            {
                if (other.CompareTag(tag)) return true;
            }
            return false;
        }

        private void ProcessCollision(Collision2D collision)
        {
            if (ScoreManager.Instance == null) return;

            if (collision.gameObject.CompareTag("Enemy"))
                ScoreManager.Instance.OnPlayerHit();
            else if (collision.gameObject.CompareTag("Pickup"))
                ScoreManager.Instance.OnPickupCollected(collision.gameObject);
        }

        private void ProcessTrigger(Collider2D other)
        {
            if (other.CompareTag("Pickup"))
            {
                ScoreManager.Instance?.OnPickupCollected(other.gameObject);
            }
        }
        #endregion
    }
}''',
                "language": "csharp",
                "engine": engine,
            }]
        return []

    def _generate_score_code(self, engine: str) -> List[Dict[str, Any]]:
        if engine == "unity":
            return [{
                "file_path": "Assets/Scripts/Core/ScoreManager.cs",
                "content": '''using UnityEngine;
using UnityEngine.Events;

namespace GameForge.Core
{
    /// <summary>
    /// 计分管理器 - 管理游戏分数和连击系统
    /// </summary>
    public class ScoreManager : MonoBehaviour
    {
        #region Events
        [System.Serializable]
        public class ScoreEvent : UnityEvent<int> { }

        public ScoreEvent OnScoreChanged = new ScoreEvent();
        public UnityEvent OnComboMaxed = new UnityEvent();
        #endregion

        #region Fields
        [Header("Score Settings")]
        [SerializeField] private int _baseScore = 100;
        [SerializeField] private int _pickupScore = 50;
        [SerializeField] private int _enemyScore = 200;

        [Header("Combo Settings")]
        [SerializeField] private float _comboWindow = 2.0f;
        [SerializeField] private int _maxComboMultiplier = 8;
        [SerializeField] private int _comboStep = 1;

        private int _currentScore;
        private int _currentCombo;
        private float _comboTimer;
        private int _highScore;
        #endregion

        #region Properties
        public int CurrentScore => _currentScore;
        public int HighScore => _highScore;
        public int CurrentCombo => _currentCombo;
        public int ComboMultiplier => Mathf.Min(1 + _currentCombo * _comboStep, _maxComboMultiplier);
        #endregion

        #region Unity Lifecycle
        private void Awake()
        {
            _highScore = PlayerPrefs.GetInt("HighScore", 0);
        }

        private void Update()
        {
            if (_currentCombo > 0)
            {
                _comboTimer -= Time.deltaTime;
                if (_comboTimer <= 0f)
                    ResetCombo();
            }
        }
        #endregion

        #region Public Methods
        public void AddScore(int points)
        {
            int multiplied = points * ComboMultiplier;
            _currentScore += multiplied;
            OnScoreChanged?.Invoke(_currentScore);

            if (_currentScore > _highScore)
            {
                _highScore = _currentScore;
                PlayerPrefs.SetInt("HighScore", _highScore);
            }
        }

        public void OnEnemyDefeated()
        {
            AddScore(_enemyScore);
            IncrementCombo();
        }

        public void OnPlayerHit()
        {
            ResetCombo();
        }

        public void OnPickupCollected(GameObject pickup)
        {
            AddScore(_pickupScore);
            Destroy(pickup);
        }

        public void ResetScore()
        {
            _currentScore = 0;
            ResetCombo();
            OnScoreChanged?.Invoke(_currentScore);
        }
        #endregion

        #region Combo System
        private void IncrementCombo()
        {
            _currentCombo++;
            _comboTimer = _comboWindow;

            if (_currentCombo >= _maxComboMultiplier)
                OnComboMaxed?.Invoke();
        }

        private void ResetCombo()
        {
            _currentCombo = 0;
            _comboTimer = 0f;
        }
        #endregion
    }
}''',
                "language": "csharp",
                "engine": engine,
            }]
        return []

    def _generate_enemy_code(self, engine: str) -> List[Dict[str, Any]]:
        if engine == "unity":
            return [{
                "file_path": "Assets/Scripts/Enemy/EnemyController.cs",
                "content": '''using UnityEngine;

namespace GameForge.Enemy
{
    /// <summary>
    /// 敌人控制器 - 巡逻、碰撞检测
    /// </summary>
    public class EnemyController : MonoBehaviour
    {
        #region Fields
        [Header("Patrol")]
        [SerializeField] private float _moveSpeed = 2f;
        [SerializeField] private float _patrolDistance = 3f;

        [Header("Sprites")]
        [SerializeField] private Sprite _walkASprite;
        [SerializeField] private Sprite _walkBSprite;

        private Rigidbody2D _rb;
        private SpriteRenderer _sr;
        private float _startX;
        private int _direction = 1;
        private float _animTimer;
        private int _walkFrame;
        #endregion

        #region Unity Lifecycle
        private void Awake()
        {
            _rb = GetComponent<Rigidbody2D>();
            _sr = GetComponent<SpriteRenderer>();
            _startX = transform.position.x;
            if (_rb == null) { Debug.LogError("[EnemyController] Rigidbody2D missing!"); enabled = false; return; }
            if (_sr == null) _sr = GetComponentInChildren<SpriteRenderer>();
        }

        private void FixedUpdate() { Patrol(); }
        private void Update() { UpdateAnimation(); }
        #endregion

        #region Patrol
        private void Patrol()
        {
            float distFromStart = transform.position.x - _startX;
            if (Mathf.Abs(distFromStart) >= _patrolDistance) _direction = -_direction;
            _rb.velocity = new Vector2(_direction * _moveSpeed, _rb.velocity.y);
            if (_sr != null) { var s = transform.localScale; s.x = Mathf.Abs(s.x) * (_direction > 0 ? 1f : -1f); transform.localScale = s; }
        }
        #endregion

        #region Animation
        private void UpdateAnimation()
        {
            if (_sr == null || _walkASprite == null) return;
            _animTimer += Time.deltaTime;
            if (_animTimer >= 0.2f) { _walkFrame = 1 - _walkFrame; _animTimer = 0f; }
            _sr.sprite = _walkFrame == 0 ? _walkASprite : (_walkBSprite ?? _walkASprite);
        }
        #endregion

        #region Collision
        private void OnCollisionEnter2D(Collision2D collision)
        {
            if (!collision.gameObject.CompareTag("Player")) return;
            var contact = collision.GetContact(0);
            if (contact.normal.y < -0.5f)
            {
                Die();
                var playerRb = collision.gameObject.GetComponent<Rigidbody2D>();
                if (playerRb != null) playerRb.velocity = new Vector2(playerRb.velocity.x, 8f);
            }
            else
            {
                var player = collision.gameObject.GetComponent<GameForge.Player.PlayerController>();
                if (player != null && !player.IsHit)
                {
                    player.TakeHit();
                    if (GameForge.Core.GameManager.Instance != null) { GameForge.Core.GameManager.Instance.LoseLife(); if (GameForge.Core.GameManager.Instance.IsGameOver) GameForge.Core.GameManager.Instance.RestartGame(); }
                }
            }
        }

        private void Die() { Destroy(gameObject); }
        #endregion
    }
}''',
                "language": "csharp",
                "engine": "unity",
            }]
        return []

    def _generate_coin_code(self, engine: str) -> List[Dict[str, Any]]:
        if engine == "unity":
            return [{
                "file_path": "Assets/Scripts/Collectibles/CoinController.cs",
                "content": '''using UnityEngine;

namespace GameForge.Collectibles
{
    /// <summary>
    /// 金币控制器 - 收集、浮动动画
    /// </summary>
    public class CoinController : MonoBehaviour
    {
        #region Fields
        [Header("Collection")]
        [SerializeField] private int _scoreValue = 10;

        [Header("Animation")]
        [SerializeField] private float _bobSpeed = 2f;
        [SerializeField] private float _bobHeight = 0.15f;

        private Vector3 _startPosition;
        #endregion

        #region Unity Lifecycle
        private void Awake() { _startPosition = transform.position; }

        private void Update()
        {
            float newY = _startPosition.y + Mathf.Sin(Time.time * _bobSpeed) * _bobHeight;
            transform.position = new Vector3(_startPosition.x, newY, _startPosition.z);
        }
        #endregion

        #region Collection
        private void OnTriggerEnter2D(Collider2D other)
        {
            if (other.CompareTag("Player"))
            {
                if (GameForge.Core.GameManager.Instance != null) GameForge.Core.GameManager.Instance.AddScore(_scoreValue);
                Destroy(gameObject);
            }
        }
        #endregion
    }
}''',
                "language": "csharp",
                "engine": "unity",
            }]
        return []

    def _generate_generic_code(self, task_name: str, engine: str, file_path: str = None) -> List[Dict[str, Any]]:
        if engine == "unity":
            class_name = task_name.replace(" ", "").replace("-", "").replace("_", "")
            if not class_name or not class_name[0].isalpha():
                class_name = "Generated" + class_name

            if not file_path:
                file_path = f"Assets/Scripts/Generated/{class_name}.cs"

            return [{
                "file_path": file_path,
                "content": f'''using UnityEngine;

namespace GameForge.Generated
{{
    /// <summary>
    /// {task_name} - 自动生成的组件
    /// </summary>
    public class {class_name} : MonoBehaviour
    {{
        #region Fields
        [SerializeField] private bool _enabled = true;
        [SerializeField] private float _updateInterval = 0.1f;
        private float _timer;
        #endregion

        #region Unity Lifecycle
        private void Awake()
        {{
            Initialize();
        }}

        private void Update()
        {{
            if (!_enabled) return;

            _timer += Time.deltaTime;
            if (_timer >= _updateInterval)
            {{
                _timer = 0f;
                OnTick();
            }}
        }}
        #endregion

        #region Protected Methods
        protected virtual void Initialize()
        {{
            // 初始化逻辑
        }}

        protected virtual void OnTick()
        {{
            // 定时更新逻辑
        }}
        #endregion

        #region Public Methods
        public void SetEnabled(bool enabled)
        {{
            _enabled = enabled;
        }}
        #endregion
    }}
}}''',
                "language": "csharp",
                "engine": engine,
            }]
        return []

    def _generate_camera_follow_code(self, engine: str) -> List[Dict[str, Any]]:
        """生成摄像机跟随脚本"""
        if engine == "unity":
            return [{
                "file_path": "Assets/Scripts/Camera/CameraFollow.cs",
                "content": '''using UnityEngine;

namespace GameForge.Camera
{
    /// <summary>
    /// 2D/3D 摄像机跟随目标
    /// </summary>
    public class CameraFollow : MonoBehaviour
    {
        #region Fields
        [Header("跟随设置")]
        [SerializeField] private Transform _target;
        [SerializeField] private float _smoothSpeed = 5f;
        [SerializeField] private Vector3 _offset = new Vector3(0, 1, -10);

        [Header("边界限制")]
        [SerializeField] private bool _useBounds;
        [SerializeField] private float _minX, _maxX, _minY, _maxY;
        #endregion

        #region Unity Lifecycle
        private void LateUpdate()
        {
            if (_target == null) return;
            FollowTarget();
        }
        #endregion

        #region Private Methods
        private void FollowTarget()
        {
            Vector3 desiredPos = _target.position + _offset;
            Vector3 smoothedPos = Vector3.Lerp(transform.position, desiredPos, _smoothSpeed * Time.deltaTime);

            if (_useBounds)
            {
                smoothedPos.x = Mathf.Clamp(smoothedPos.x, _minX, _maxX);
                smoothedPos.y = Mathf.Clamp(smoothedPos.y, _minY, _maxY);
            }

            transform.position = smoothedPos;
        }
        #endregion

        #region Public Methods
        public void SetTarget(Transform target)
        {
            _target = target;
        }
        #endregion
    }
}''',
                "language": "csharp",
                "engine": engine,
            }]
        return []

    def _generate_ui_manager_code(self, engine: str) -> List[Dict[str, Any]]:
        """生成UI管理器脚本"""
        if engine == "unity":
            return [{
                "file_path": "Assets/Scripts/UI/UIManager.cs",
                "content": '''using UnityEngine;
using UnityEngine.UI;

namespace GameForge.UI
{
    /// <summary>
    /// 游戏UI管理器 — 管理HUD和菜单
    /// </summary>
    public class UIManager : MonoBehaviour
    {
        #region Fields
        [Header("HUD元素")]
        [SerializeField] private Text _scoreText;
        [SerializeField] private Text _livesText;
        [SerializeField] private Slider _healthBar;

        [Header("面板")]
        [SerializeField] private GameObject _pausePanel;
        [SerializeField] private GameObject _gameOverPanel;
        #endregion

        #region Unity Lifecycle
        private void Awake()
        {
            if (_pausePanel) _pausePanel.SetActive(false);
            if (_gameOverPanel) _gameOverPanel.SetActive(false);
        }
        #endregion

        #region Public Methods
        public void UpdateScore(int score)
        {
            if (_scoreText) _scoreText.text = $"Score: {score}";
        }

        public void UpdateLives(int lives)
        {
            if (_livesText) _livesText.text = $"Lives: {lives}";
        }

        public void UpdateHealth(float normalized)
        {
            if (_healthBar) _healthBar.value = Mathf.Clamp01(normalized);
        }

        public void ShowPauseMenu(bool show)
        {
            if (_pausePanel) _pausePanel.SetActive(show);
        }

        public void ShowGameOver(bool show)
        {
            if (_gameOverPanel) _gameOverPanel.SetActive(show);
        }
        #endregion
    }
}''',
                "language": "csharp",
                "engine": engine,
            }]
        return []
