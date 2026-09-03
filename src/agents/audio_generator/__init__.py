"""GameForge - 音频生成 Agent

根据 GDM 中的音频需求,调用音频模型生成对话、音效、背景音乐。
"""

from typing import Dict, Any, Optional, List
import asyncio
from pathlib import Path
import hashlib
from datetime import datetime

from ..base_agent import BaseAgent
from ...core.state.game_state import GameDevState
from ...models.audio_model import (
    AudioModelFactory,
    AudioAsset,
    AudioType,
    AudioFormat,
)
from ...utils.logger import get_logger

logger = get_logger(__name__)


class AudioGeneratorAgent(BaseAgent):
    """音频生成 Agent

    职责:
    1. 从 GDM 中提取音频需求
    2. 调用 TTS/SFX/Music 模型生成音频
    3. 写入 res://assets/audio/ 目录
    4. 更新 state.audio_assets
    """

    def __init__(self, llm_client=None, config: Optional[Dict[str, Any]] = None):
        super().__init__(
            agent_type="audio_generator", llm_client=llm_client, config=config or {}
        )
        self.output_dir = Path("res://assets/audio")

    async def run(self, state: GameDevState) -> Dict[str, Any]:
        """执行音频生成

        Args:
            state: 当前游戏开发状态

        Returns:
            更新后的状态字段
        """
        logger.info("AudioGeneratorAgent 开始执行")

        try:
            # 1. 提取音频需求
            audio_requirements = self._extract_audio_requirements(state)
            if not audio_requirements:
                logger.warning("GDM 中未找到音频需求,跳过生成")
                return {"audio_assets": {}}

            logger.info(f"提取到 {len(audio_requirements)} 个音频需求")

            # 2. 生成音频资产
            audio_assets = await self._generate_audio_assets(audio_requirements)

            # 3. 写入文件
            await self._write_audio_files(audio_assets, state)

            # 4. 返回更新
            return {
                "audio_assets": {asset.id: asset.dict() for asset in audio_assets},
                "message_bus": [
                    {
                        "type": "audio_generated",
                        "agent": "audio_generator",
                        "timestamp": datetime.now().isoformat(),
                        "data": {"count": len(audio_assets)},
                    }
                ],
            }

        except Exception as e:
            logger.error(f"音频生成失败: {e}", exc_info=True)
            return {
                "error_log": [f"AudioGenerator error: {str(e)}"],
                "audio_assets": {},
            }

    def _extract_audio_requirements(self, state: GameDevState) -> List[Dict[str, Any]]:
        """从 GDM 中提取音频需求

        Args:
            state: 游戏开发状态

        Returns:
            音频需求列表,每项包含:
            - name: 资产名称
            - type: 音频类型(dialogue/sfx/bgm/ambient)
            - prompt: 生成提示词
            - duration: 目标时长(可选)
            - metadata: 额外信息
        """
        gdm = state.get("game_design_model")
        if not gdm:
            return []

        requirements = []

        # 提取对话/旁白
        if "dialogues" in gdm:
            for idx, dialogue in enumerate(gdm["dialogues"]):
                requirements.append(
                    {
                        "name": f"dialogue_{idx:03d}",
                        "type": AudioType.DIALOGUE,
                        "prompt": dialogue.get("text", ""),
                        "metadata": {
                            "speaker": dialogue.get("speaker"),
                            "emotion": dialogue.get("emotion"),
                        },
                    }
                )

        # 提取音效
        if "sfx" in gdm:
            for sfx in gdm["sfx"]:
                requirements.append(
                    {
                        "name": sfx.get("name", "sfx_unnamed"),
                        "type": AudioType.SFX,
                        "prompt": sfx.get("description", ""),
                        "duration": sfx.get("duration", 3.0),
                        "metadata": {"trigger": sfx.get("trigger")},
                    }
                )

        # 提取背景音乐
        if "bgm" in gdm:
            for bgm in gdm["bgm"]:
                requirements.append(
                    {
                        "name": bgm.get("name", "bgm_unnamed"),
                        "type": AudioType.BGM,
                        "prompt": bgm.get("description", ""),
                        "duration": bgm.get("duration", 60.0),
                        "metadata": {
                            "genre": bgm.get("genre"),
                            "mood": bgm.get("mood"),
                            "loop": bgm.get("loop", True),
                        },
                    }
                )

        # 提取环境音
        if "ambient" in gdm:
            for amb in gdm["ambient"]:
                requirements.append(
                    {
                        "name": amb.get("name", "ambient_unnamed"),
                        "type": AudioType.AMBIENT,
                        "prompt": amb.get("description", ""),
                        "duration": amb.get("duration", 10.0),
                        "metadata": {"scene": amb.get("scene")},
                    }
                )

        return requirements

    async def _generate_audio_assets(
        self, requirements: List[Dict[str, Any]]
    ) -> List[AudioAsset]:
        """批量生成音频资产

        Args:
            requirements: 音频需求列表

        Returns:
            生成的音频资产列表
        """
        tasks = []
        for req in requirements:
            task = self._generate_single_audio(req)
            tasks.append(task)

        # 并发生成(可配置并发数)
        max_concurrent = self.config.get("max_concurrent_audio", 3)
        results = []
        for i in range(0, len(tasks), max_concurrent):
            batch = tasks[i : i + max_concurrent]
            batch_results = await asyncio.gather(*batch, return_exceptions=True)
            results.extend(batch_results)

        # 过滤失败的生成
        audio_assets = [r for r in results if isinstance(r, AudioAsset)]
        failed_count = len(results) - len(audio_assets)
        if failed_count > 0:
            logger.warning(f"{failed_count} 个音频生成失败")

        return audio_assets

    async def _generate_single_audio(self, requirement: Dict[str, Any]) -> AudioAsset:
        """生成单个音频资产

        Args:
            requirement: 音频需求

        Returns:
            音频资产
        """
        audio_type = requirement["type"]
        prompt = requirement["prompt"]
        duration = requirement.get("duration")
        metadata = requirement.get("metadata", {})

        logger.info(f"生成音频: {requirement['name']} ({audio_type})")

        # 创建模型
        model = AudioModelFactory.create_model(
            audio_type=audio_type,
            api_key=self.config.get("audio_api_key"),
            config=self.config.get("audio_model_config", {}),
        )

        # 生成音频数据
        audio_data = await model.generate(
            prompt=prompt, audio_type=audio_type, duration=duration, **metadata
        )

        # 选择格式(Godot 推荐 OGG)
        audio_format = AudioFormat.OGG

        # 生成文件路径
        type_dir = audio_type.value + "s"  # dialogues, sfx, bgm, ambient
        file_path = (
            f"res://assets/audio/{type_dir}/{requirement['name']}.{audio_format.value}"
        )

        # 计算音频时长(这里简化,实际需要解析音频数据)
        estimated_duration = duration or len(audio_data) / (44100 * 2 * 2)  # 粗略估算

        # 生成资产 ID
        asset_id = hashlib.md5(
            f"{requirement['name']}_{audio_type}".encode()
        ).hexdigest()[:16]

        return AudioAsset(
            id=asset_id,
            name=requirement["name"],
            type=audio_type,
            format=audio_format,
            file_path=file_path,
            duration=estimated_duration,
            description=metadata.get("description", prompt[:100]),
            prompt=prompt,
            metadata={
                **metadata,
                "audio_data": audio_data,  # 临时存储,写入文件后移除
            },
        )

    async def _write_audio_files(
        self, audio_assets: List[AudioAsset], state: GameDevState
    ):
        """写入音频文件到项目目录

        Args:
            audio_assets: 音频资产列表
            state: 游戏开发状态
        """
        project_context = state.get("project_context", {})
        project_root = Path(project_context.get("project_root", "."))

        for asset in audio_assets:
            # 转换 res:// 路径为实际路径
            rel_path = asset.file_path.replace("res://", "")
            full_path = project_root / rel_path

            # 创建目录
            full_path.parent.mkdir(parents=True, exist_ok=True)

            # 写入音频数据
            audio_data = asset.metadata.pop("audio_data", None)
            if audio_data:
                full_path.write_bytes(audio_data)
                logger.info(f"写入音频文件: {full_path}")

            # 生成 Godot .import 配置
            await self._generate_godot_import(full_path, asset)

    async def _generate_godot_import(self, audio_path: Path, asset: AudioAsset):
        """生成 Godot 音频导入配置

        Args:
            audio_path: 音频文件路径
            asset: 音频资产
        """
        import_path = audio_path.with_suffix(audio_path.suffix + ".import")

        # Godot 4.x 音频导入配置
        loop = asset.metadata.get("loop", False)
        import_config = f"""[remap]

importer="oggvorbisstr" if asset.format == AudioFormat.OGG else "mp3"
type="AudioStreamOGGVorbis" if asset.format == AudioFormat.OGG else "AudioStreamMP3"
uid="uid://{"".join(c for c in asset.id if c.isalnum())}"
path="res://.godot/imported/{audio_path.name}-{asset.id}.oggstr"

[deps]

source_file="{asset.file_path}"
dest_files=["res://.godot/imported/{audio_path.name}-{asset.id}.oggstr"]

[params]

loop={"true" if loop else "false"}
loop_offset=0.0
bpm=0.0
beat_count=0
bar_beats=4
"""

        import_path.write_text(import_config, encoding="utf-8")
        logger.debug(f"生成 Godot 导入配置: {import_path}")
