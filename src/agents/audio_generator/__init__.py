"""GameForge - 音频生成 Agent

根据 GDM 中的音频需求，调用音频模型（TTS / SFX / Music）生成对话、音效、背景音乐。

结构对齐 :mod:`src.agents.audio_generator`（本文件自身）与 UIGenerator：

1. 从 GDM 抽取音频需求（dialogues / sfx / bgm / ambient）
2. 交给 :mod:`src.models.audio_model` 的模型工厂生成音频字节
3. 写入 ``res://assets/audio/<type>/*`` 并生成 Godot ``.import`` 配置
4. 更新 ``state.audio_assets``

修复记录（2026-09-04）：
- ``from ..base_agent import BaseAgent`` 改为 ``from ..base import BaseAgent``（原模块不存在，导致 import 即失败）
- 构造器不再把 ``llm_client``/字符串 agent_type 传给 BaseAgent，改存实例属性，agent_type 复用枚举
- 实现基类抽象方法 ``execute()``，``run()`` 保留为兼容别名
- 修复 ``.import`` f-string 条件表达式未包 ``{}`` 的缺陷（原样把 Python 代码写进文件）；
  OGG/MP3/WAV 的 importer/type/目标扩展名现在按格式正确映射
- API Key 支持从 ``audio.providers.stepfun.api_key_env`` 指定的环境变量读取
- 需求抽取对 dict/字符串形态的 GDM 条目做容错
"""

from __future__ import annotations

import asyncio
import hashlib
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from ...core.gdm import as_item, as_item_list
from ...core.state.game_state import AgentType, GameDevState
from ...models.audio_model import (
    DEFAULT_AMBIENT_SECONDS,
    DEFAULT_BGM_SECONDS,
    DEFAULT_SAMPLE_RATE,
    DEFAULT_SFX_SECONDS,
    AudioAsset,
    AudioFormat,
    AudioModelFactory,
    AudioType,
)
from ...utils.logger import get_logger
from ..base import BaseAgent

logger = get_logger(__name__)

#: 音频文件在 Godot 项目里的目录
AUDIO_DIR = "res://assets/audio"

#: 音频类型 → 默认目标时长（秒），GDM 未给出 duration 时的兜底
DEFAULT_DURATION_BY_TYPE: Dict[AudioType, float] = {
    AudioType.SFX: DEFAULT_SFX_SECONDS,
    AudioType.BGM: DEFAULT_BGM_SECONDS,
    AudioType.AMBIENT: DEFAULT_AMBIENT_SECONDS,
}

#: 16-bit 立体声 PCM 每秒字节数（用于从音频字节数粗略估算时长）
BYTES_PER_SECOND = DEFAULT_SAMPLE_RATE * 2 * 2

#: 并发生成音频的默认上限（agents.audio_generation.max_concurrent_audio 缺省值）
DEFAULT_MAX_CONCURRENT_AUDIO = 3

#: 资产 ID（md5 摘要）保留长度
ASSET_ID_LENGTH = 16

#: 音频描述最大字符数（生成 prompt 截断写入 description）
DESCRIPTION_MAX_CHARS = 100

#: 格式 → (.import importer, 资源类型, 导入目标扩展名)
_IMPORT_META: Dict[AudioFormat, tuple] = {
    AudioFormat.OGG: ("oggvorbisstr", "AudioStreamOGGVorbis", "oggstr"),
    AudioFormat.MP3: ("mp3", "AudioStreamMP3", "mp3str"),
    AudioFormat.WAV: ("wav", "AudioStreamWAV", "wavstr"),
}

#: 音频类型 → 输出目录名（dialogue→dialogues；sfx/bgm/ambient 保持原词）
_TYPE_DIRS: Dict[AudioType, str] = {
    AudioType.DIALOGUE: "dialogues",
    AudioType.SFX: "sfx",
    AudioType.BGM: "bgm",
    AudioType.AMBIENT: "ambient",
}


class AudioGeneratorAgent(BaseAgent):
    """音频生成 Agent

    职责:
    1. 从 GDM 中提取音频需求（对话/音效/BGM/环境音）
    2. 调用 TTS/SFX/Music 模型生成音频数据
    3. 写入 res://assets/audio/ 目录 + Godot .import 配置
    4. 更新 state.audio_assets
    """

    def __init__(self, llm_client: Any = None, config: Optional[Dict[str, Any]] = None):
        super().__init__(
            agent_type=AgentType.CODE_GENERATOR,  # 复用 code_generator 枚举，保持 6-Agent 契约
            config=config or {},
        )
        # llm_client 当前不参与生成（音频需求由 GDM 确定性推导），保留形参以对齐调用方
        self.llm_client = llm_client

        # 专属配置段：agents.audio_generation；缺省时回退到顶层同名键，保持向后兼容
        self.audio_config: Dict[str, Any] = dict(
            (config or {}).get("agents", {}).get("audio_generation", {}) or {}
        )
        self.audio_dir_res = str(
            self.audio_config.get("audio_dir", AUDIO_DIR)
        ).rstrip("/")
        self.output_dir = Path(self.audio_dir_res.replace("res://", ""))

    def _audio_setting(self, key: str, default: Any) -> Any:
        """读取音频生成配置：优先 agents.audio_generation，回退顶层 config。"""
        if key in self.audio_config:
            return self.audio_config[key]
        return self.config.get(key, default)

    # ─── 对外入口 ───────────────────────────────────────

    async def execute(self, state: GameDevState, **kwargs: Any) -> Dict[str, Any]:
        """执行音频生成。

        Args:
            state: 当前游戏开发状态

        Returns:
            状态增量：``audio_assets`` / ``message_bus`` / ``error_log``
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

            # 4. 返回更新（audio_data 已在写入时从 metadata 弹出）
            return {
                "audio_assets": {asset.id: asset.model_dump() for asset in audio_assets},
                "message_bus": [
                    {
                        "type": "audio_generated",
                        "agent": "audio_generator",
                        "timestamp": datetime.now().isoformat(),
                        "data": {"count": len(audio_assets)},
                    }
                ],
            }

        except Exception as e:  # noqa: BLE001
            logger.error(f"音频生成失败: {e}", exc_info=True)
            return {
                "error_log": [f"AudioGenerator error: {str(e)}"],
                "audio_assets": {},
            }

    async def run(self, state: GameDevState, **kwargs: Any) -> Dict[str, Any]:
        """兼容别名：委托给 execute()。"""
        return await self.execute(state, **kwargs)

    # ─── 需求抽取 ───────────────────────────────────────

    def _extract_audio_requirements(self, state: GameDevState) -> List[Dict[str, Any]]:
        """从 GDM 中提取音频需求。

        GDM 里的音频条目可能给 dict，也可能给裸字符串（此时整个字符串当作
        名称/描述），这里统一容错归一化。

        Returns:
            音频需求列表，每项包含:
            - name: 资产名称
            - type: 音频类型(dialogue/sfx/bgm/ambient)
            - prompt: 生成提示词
            - duration: 目标时长(可选)
            - metadata: 额外信息
        """
        gdm = state.get("game_design_model")
        if not gdm:
            return []

        requirements: List[Dict[str, Any]] = []

        # 提取对话/旁白（兼容 dialogues 列表 / dialogue 单条 / dict[speaker->text]）
        raw_dialogues = gdm.get("dialogues") or gdm.get("dialogue") or []
        if isinstance(raw_dialogues, dict):
            raw_dialogues = [
                {"speaker": str(k), "text": str(v)}
                for k, v in raw_dialogues.items()
            ]
        for idx, dialogue in enumerate(raw_dialogues):
            item = as_item(dialogue, description_key="description")
            requirements.append({
                "name": f"dialogue_{idx:03d}",
                "type": AudioType.DIALOGUE,
                "prompt": str(item.get("text") or item.get("description") or item.get("name") or ""),
                "metadata": {
                    "speaker": item.get("speaker"),
                    "emotion": item.get("emotion"),
                },
            })

        # 提取音效
        for sfx in as_item_list(gdm.get("sfx"), description_key="description"):
            requirements.append({
                "name": str(sfx.get("name") or "sfx_unnamed"),
                "type": AudioType.SFX,
                "prompt": str(sfx.get("description") or sfx.get("name") or ""),
                "duration": sfx.get("duration", DEFAULT_DURATION_BY_TYPE[AudioType.SFX]),
                "metadata": {"trigger": sfx.get("trigger")},
            })

        # 提取背景音乐
        for bgm in as_item_list(
            gdm.get("bgm") or gdm.get("music"), description_key="description"
        ):
            requirements.append({
                "name": str(bgm.get("name") or "bgm_unnamed"),
                "type": AudioType.BGM,
                "prompt": str(bgm.get("description") or bgm.get("name") or ""),
                "duration": bgm.get("duration", DEFAULT_DURATION_BY_TYPE[AudioType.BGM]),
                "metadata": {
                    "genre": bgm.get("genre"),
                    "mood": bgm.get("mood"),
                    "loop": bgm.get("loop", True),
                },
            })

        # 提取环境音
        for amb in as_item_list(gdm.get("ambient"), description_key="description"):
            requirements.append({
                "name": str(amb.get("name") or "ambient_unnamed"),
                "type": AudioType.AMBIENT,
                "prompt": str(amb.get("description") or amb.get("name") or ""),
                "duration": amb.get("duration", DEFAULT_DURATION_BY_TYPE[AudioType.AMBIENT]),
                "metadata": {"scene": amb.get("scene")},
            })

        return requirements

    # ─── 生成 ───────────────────────────────────────────

    async def _generate_audio_assets(
        self, requirements: List[Dict[str, Any]]
    ) -> List[AudioAsset]:
        """批量生成音频资产，单条失败不影响其余。"""
        tasks = [self._generate_single_audio(req) for req in requirements]

        max_concurrent = max(1, int(self._audio_setting("max_concurrent_audio", DEFAULT_MAX_CONCURRENT_AUDIO)))
        results: List[Any] = []
        for i in range(0, len(tasks), max_concurrent):
            batch = tasks[i: i + max_concurrent]
            batch_results = await asyncio.gather(*batch, return_exceptions=True)
            results.extend(batch_results)

        audio_assets = [r for r in results if isinstance(r, AudioAsset)]
        failed_count = len(results) - len(audio_assets)
        if failed_count > 0:
            logger.warning(f"{failed_count} 个音频生成失败")
        return audio_assets

    async def _generate_single_audio(self, requirement: Dict[str, Any]) -> AudioAsset:
        """生成单个音频资产。"""
        audio_type = requirement["type"]
        prompt = requirement["prompt"]
        duration = requirement.get("duration")
        metadata = dict(requirement.get("metadata", {}) or {})

        logger.info(f"生成音频: {requirement['name']} ({audio_type})")

        # 创建模型：合并全局 audio 配置（providers.stepfun 等）
        audio_section = self.config.get("audio", {}) or {}
        model_config: Dict[str, Any] = dict(self.config.get("audio_model_config", {}) or {})
        api_key = self.config.get("audio_api_key")

        if audio_type == AudioType.DIALOGUE:
            default_provider = audio_section.get("default_tts_provider")
            if default_provider:
                model_config.setdefault("provider", default_provider)
            stepfun_cfg = audio_section.get("providers", {}).get("stepfun", {}) or {}
            for key, value in stepfun_cfg.items():
                model_config.setdefault(key, value)
            # API Key 兜底：从 providers.stepfun.api_key_env 指定的环境变量读取
            if not api_key:
                env_name = stepfun_cfg.get("api_key_env") or "STEPFUN_TTS_API_KEY"
                api_key = os.getenv(env_name)

        model = AudioModelFactory.create_model(
            audio_type=audio_type,
            api_key=api_key,
            config=model_config,
        )

        # 生成音频数据
        audio_data = await model.generate(
            prompt=prompt, audio_type=audio_type, duration=duration, **metadata
        )

        # 优先使用模型支持的首选格式，避免音频内容与扩展名不匹配
        supported_formats = model.get_supported_formats()
        audio_format = supported_formats[0] if supported_formats else AudioFormat.OGG

        # 生成文件路径（类型 → 目录名，不能简单 value+"s"，sfx/bgm 会拼错）
        type_dir = _TYPE_DIRS[audio_type]
        file_path = (
            f"{self.audio_dir_res}/{type_dir}/{requirement['name']}.{audio_format.value}"
        )

        # 计算音频时长(这里简化,实际需要解析音频数据)
        estimated_duration = duration or len(audio_data) / BYTES_PER_SECOND  # 粗略估算

        # 生成资产 ID
        asset_id = hashlib.md5(
            f"{requirement['name']}_{audio_type}".encode()
        ).hexdigest()[:ASSET_ID_LENGTH]

        return AudioAsset(
            id=asset_id,
            name=requirement["name"],
            type=audio_type,
            format=audio_format,
            file_path=file_path,
            duration=estimated_duration,
            description=metadata.get("description", prompt[:DESCRIPTION_MAX_CHARS]),
            prompt=prompt,
            metadata={
                **metadata,
                "audio_data": audio_data,  # 临时存储,写入文件后移除
            },
        )

    # ─── 文件写入 ───────────────────────────────────────

    async def _write_audio_files(
        self, audio_assets: List[AudioAsset], state: GameDevState
    ) -> None:
        """写入音频文件到项目目录，并生成 Godot .import 配置。"""
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

    async def _generate_godot_import(self, audio_path: Path, asset: AudioAsset) -> None:
        """生成 Godot 音频导入配置。

        注意：importer/资源类型/目标扩展名必须按 ``asset.format`` 解析后才放进
        f-string。历史上这里把 ``"oggvorbisstr" if ... else "mp3"`` 直接写进了
        模板（没包 {}），导致 .import 文件里出现一整段 Python 条件表达式。
        """
        import_path = audio_path.with_suffix(audio_path.suffix + ".import")

        importer, res_type, dest_ext = _IMPORT_META.get(
            asset.format, _IMPORT_META[AudioFormat.OGG]
        )
        uid = "uid://" + "".join(c for c in asset.id if c.isalnum())
        dest = f"res://.godot/imported/{audio_path.name}-{asset.id}.{dest_ext}"
        loop = asset.metadata.get("loop", False)

        import_config = f"""[remap]

importer="{importer}"
type="{res_type}"
uid="{uid}"
path="{dest}"

[deps]

source_file="{asset.file_path}"
dest_files=["{dest}"]

[params]

loop={"true" if loop else "false"}
loop_offset=0.0
bpm=0.0
beat_count=0
bar_beats=4
"""
        import_path.write_text(import_config, encoding="utf-8")
        logger.debug(f"生成 Godot 导入配置: {import_path}")
