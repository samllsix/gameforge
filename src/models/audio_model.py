"""GameForge - 音频模型抽象层

封装 TTS、音效、BGM 生成模型,提供统一接口。
"""

from abc import ABC, abstractmethod
from typing import Optional, Dict, Any, List
from enum import Enum
from pydantic import BaseModel, Field
from datetime import datetime
import asyncio
import aiohttp
from pathlib import Path


class AudioType(str, Enum):
    """音频类型枚举"""

    DIALOGUE = "dialogue"  # 对话/旁白
    SFX = "sfx"  # 音效
    BGM = "bgm"  # 背景音乐
    AMBIENT = "ambient"  # 环境音


class AudioFormat(str, Enum):
    """音频格式枚举"""

    WAV = "wav"
    OGG = "ogg"
    MP3 = "mp3"


class AudioAsset(BaseModel):
    """音频资产数据模型"""

    id: str = Field(..., description="资产唯一标识")
    name: str = Field(..., description="资产名称")
    type: AudioType = Field(..., description="音频类型")
    format: AudioFormat = Field(..., description="音频格式")
    file_path: str = Field(..., description="文件路径(相对于项目根目录)")
    duration: float = Field(..., description="时长(秒)")
    sample_rate: int = Field(default=44100, description="采样率")
    description: str = Field(..., description="用途描述")
    prompt: str = Field(..., description="生成 prompt")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="元数据")
    created_at: datetime = Field(default_factory=datetime.now, description="创建时间")


class AudioModel(ABC):
    """音频生成模型抽象基类"""

    def __init__(
        self, api_key: Optional[str] = None, config: Optional[Dict[str, Any]] = None
    ):
        self.api_key = api_key
        self.config = config or {}

    @abstractmethod
    async def generate(
        self,
        prompt: str,
        audio_type: AudioType,
        duration: Optional[float] = None,
        **kwargs,
    ) -> bytes:
        """生成音频

        Args:
            prompt: 生成提示词
            audio_type: 音频类型
            duration: 目标时长(秒)
            **kwargs: 模型特定参数

        Returns:
            音频二进制数据
        """
        pass

    @abstractmethod
    def get_supported_formats(self) -> List[AudioFormat]:
        """获取支持的音频格式"""
        pass


class TTSModel(AudioModel):
    """TTS(文本转语音)模型

    支持 OpenAI TTS、Azure TTS、Edge TTS 等
    """

    def __init__(
        self,
        provider: str = "openai",  # openai, azure, edge
        api_key: Optional[str] = None,
        config: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(api_key, config)
        self.provider = provider

    async def generate(
        self,
        prompt: str,
        audio_type: AudioType = AudioType.DIALOGUE,
        duration: Optional[float] = None,
        voice: str = "alloy",
        **kwargs,
    ) -> bytes:
        """生成 TTS 音频

        Args:
            prompt: 文本内容
            audio_type: 音频类型(默认 DIALOGUE)
            duration: 目标时长(可选,TTS 根据文本自动确定)
            voice: 语音角色(openai: alloy/echo/fable/onyx/nova/shimmer)
            **kwargs: 其他参数
        """
        if self.provider == "openai":
            return await self._generate_openai_tts(prompt, voice, **kwargs)
        elif self.provider == "edge":
            return await self._generate_edge_tts(prompt, voice, **kwargs)
        else:
            raise NotImplementedError(f"TTS provider '{self.provider}' not implemented")

    async def _generate_openai_tts(self, text: str, voice: str, **kwargs) -> bytes:
        """调用 OpenAI TTS API"""
        # 实际实现需要调用 OpenAI API
        # 这里返回占位
        raise NotImplementedError("OpenAI TTS integration pending")

    async def _generate_edge_tts(self, text: str, voice: str, **kwargs) -> bytes:
        """调用 Edge TTS(免费)"""
        # 可使用 edge-tts 库
        raise NotImplementedError("Edge TTS integration pending")

    def get_supported_formats(self) -> List[AudioFormat]:
        return [AudioFormat.MP3, AudioFormat.WAV, AudioFormat.OGG]


class SFXModel(AudioModel):
    """音效生成模型

    支持 ElevenLabs Sound Effects、AudioGen 等
    """

    def __init__(
        self,
        provider: str = "elevenlabs",  # elevenlabs, audiogen
        api_key: Optional[str] = None,
        config: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(api_key, config)
        self.provider = provider

    async def generate(
        self,
        prompt: str,
        audio_type: AudioType = AudioType.SFX,
        duration: Optional[float] = 3.0,
        **kwargs,
    ) -> bytes:
        """生成音效

        Args:
            prompt: 音效描述(如"footsteps on wood floor")
            audio_type: 音频类型(默认 SFX)
            duration: 目标时长(秒,默认 3 秒)
            **kwargs: 其他参数
        """
        if self.provider == "elevenlabs":
            return await self._generate_elevenlabs_sfx(prompt, duration, **kwargs)
        else:
            raise NotImplementedError(f"SFX provider '{self.provider}' not implemented")

    async def _generate_elevenlabs_sfx(
        self, prompt: str, duration: float, **kwargs
    ) -> bytes:
        """调用 ElevenLabs Sound Effects API"""
        # 实际实现需要调用 ElevenLabs API
        raise NotImplementedError("ElevenLabs SFX integration pending")

    def get_supported_formats(self) -> List[AudioFormat]:
        return [AudioFormat.MP3, AudioFormat.WAV]


class MusicModel(AudioModel):
    """背景音乐生成模型

    支持 Suno、Udio、MusicGen 等
    """

    def __init__(
        self,
        provider: str = "suno",  # suno, udio, musicgen
        api_key: Optional[str] = None,
        config: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(api_key, config)
        self.provider = provider

    async def generate(
        self,
        prompt: str,
        audio_type: AudioType = AudioType.BGM,
        duration: Optional[float] = 60.0,
        genre: Optional[str] = None,
        mood: Optional[str] = None,
        **kwargs,
    ) -> bytes:
        """生成背景音乐

        Args:
            prompt: 音乐描述
            audio_type: 音频类型(默认 BGM)
            duration: 目标时长(秒,默认 60 秒)
            genre: 音乐风格(如 "electronic", "orchestral")
            mood: 情绪(如 "tense", "calm")
            **kwargs: 其他参数
        """
        if self.provider == "suno":
            return await self._generate_suno_music(
                prompt, duration, genre, mood, **kwargs
            )
        else:
            raise NotImplementedError(
                f"Music provider '{self.provider}' not implemented"
            )

    async def _generate_suno_music(
        self,
        prompt: str,
        duration: float,
        genre: Optional[str],
        mood: Optional[str],
        **kwargs,
    ) -> bytes:
        """调用 Suno API"""
        # 实际实现需要调用 Suno API
        raise NotImplementedError("Suno music generation integration pending")

    def get_supported_formats(self) -> List[AudioFormat]:
        return [AudioFormat.MP3, AudioFormat.WAV]


class AudioModelFactory:
    """音频模型工厂"""

    @staticmethod
    def create_model(
        audio_type: AudioType,
        provider: Optional[str] = None,
        api_key: Optional[str] = None,
        config: Optional[Dict[str, Any]] = None,
    ) -> AudioModel:
        """根据音频类型创建对应模型

        Args:
            audio_type: 音频类型
            provider: 指定提供商(可选)
            api_key: API 密钥
            config: 配置

        Returns:
            音频模型实例
        """
        if audio_type == AudioType.DIALOGUE:
            return TTSModel(
                provider=provider or "openai", api_key=api_key, config=config
            )
        elif audio_type in (AudioType.SFX, AudioType.AMBIENT):
            return SFXModel(
                provider=provider or "elevenlabs", api_key=api_key, config=config
            )
        elif audio_type == AudioType.BGM:
            return MusicModel(
                provider=provider or "suno", api_key=api_key, config=config
            )
        else:
            raise ValueError(f"Unsupported audio type: {audio_type}")
