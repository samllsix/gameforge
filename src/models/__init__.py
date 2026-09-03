"""GameForge 模型抽象层

统一封装 LLM、图像、音频、视频等生成模型,提供标准化接口。
"""

from .audio_model import AudioModel, TTSModel, SFXModel, MusicModel, AudioAsset

__all__ = [
    "AudioModel",
    "TTSModel",
    "SFXModel",
    "MusicModel",
    "AudioAsset",
]
