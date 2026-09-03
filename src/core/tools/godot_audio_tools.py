"""GameForge - Godot 音频工具

提供音频资源注册、AudioStreamPlayer 节点创建等功能。
"""

from typing import Dict, Any, Optional, List
from pathlib import Path
from enum import Enum

from ...models.audio_model import AudioAsset, AudioType, AudioFormat
from ...utils.logger import get_logger

logger = get_logger(__name__)


class AudioBusType(str, Enum):
    """Godot 音频总线类型"""

    MASTER = "Master"
    SFX = "SFX"
    MUSIC = "Music"
    VOICE = "Voice"
    AMBIENT = "Ambient"


class GodotAudioTools:
    """Godot 音频工具集"""

    @staticmethod
    def get_audio_bus_for_type(audio_type: AudioType) -> AudioBusType:
        """根据音频类型返回推荐的音频总线

        Args:
            audio_type: 音频类型

        Returns:
            对应的音频总线
        """
        mapping = {
            AudioType.DIALOGUE: AudioBusType.VOICE,
            AudioType.SFX: AudioBusType.SFX,
            AudioType.BGM: AudioBusType.MUSIC,
            AudioType.AMBIENT: AudioBusType.AMBIENT,
        }
        return mapping.get(audio_type, AudioBusType.MASTER)

    @staticmethod
    def create_audio_player_node(
        asset: AudioAsset,
        node_name: Optional[str] = None,
        autoplay: bool = False,
        volume_db: float = 0.0,
        parent_path: str = ".",
    ) -> Dict[str, Any]:
        """创建 AudioStreamPlayer 节点描述

        Args:
            asset: 音频资产
            node_name: 节点名称(默认使用资产名)
            autoplay: 是否自动播放
            volume_db: 音量(dB)
            parent_path: 父节点路径

        Returns:
            节点描述字典(可被 SceneBuilder 使用)
        """
        node_name = node_name or asset.name.replace("_", " ").title().replace(" ", "")
        audio_bus = GodotAudioTools.get_audio_bus_for_type(asset.type)

        # 根据音频类型选择播放器类型
        if asset.type == AudioType.BGM:
            player_type = "AudioStreamPlayer"  # 2D/3D 游戏用 AudioStreamPlayer2D/3D
        else:
            player_type = "AudioStreamPlayer"

        node_desc = {
            "name": node_name,
            "type": player_type,
            "parent": parent_path,
            "properties": {
                "stream": f'ExtResource("{asset.file_path}")',
                "volume_db": volume_db,
                "autoplay": autoplay,
                "bus": audio_bus.value,
            },
            "metadata": {
                "asset_id": asset.id,
                "audio_type": asset.type.value,
                "description": asset.description,
            },
        }

        # BGM 循环设置
        if asset.type == AudioType.BGM and asset.metadata.get("loop", True):
            node_desc["properties"]["loop"] = True

        return node_desc

    @staticmethod
    def generate_audio_bus_layout(custom_buses: Optional[List[str]] = None) -> str:
        """生成 Godot 音频总线布局配置

        Args:
            custom_buses: 自定义总线名称列表

        Returns:
            default_bus_layout.tres 文件内容
        """
        buses = custom_buses or ["Master", "SFX", "Music", "Voice", "Ambient"]

        # Godot 4.x 音频总线布局格式
        config = '[gd_resource type="AudioBusLayout" format=3]\n\n'

        for idx, bus_name in enumerate(buses):
            config += f'[sub_resource type="AudioBusLayout" id="{idx}"]\n'
            config += f'bus/{idx}/name = "{bus_name}"\n'
            config += f"bus/{idx}/solo = false\n"
            config += f"bus/{idx}/mute = false\n"
            config += f"bus/{idx}/bypass = false\n"
            config += f"bus/{idx}/volume_db = 0.0\n"
            if idx > 0:  # 非 Master 总线连接到 Master
                config += f'bus/{idx}/send = "Master"\n'
            config += "\n"

        return config

    @staticmethod
    def register_audio_resources(
        audio_assets: List[AudioAsset], project_root: Path
    ) -> Dict[str, str]:
        """注册音频资源到 Godot 项目

        Args:
            audio_assets: 音频资产列表
            project_root: 项目根目录

        Returns:
            资源路径映射 {asset_id: godot_resource_path}
        """
        resource_map = {}

        for asset in audio_assets:
            # Godot 资源路径
            resource_path = asset.file_path
            resource_map[asset.id] = resource_path

            logger.debug(f"注册音频资源: {asset.name} -> {resource_path}")

        return resource_map

    @staticmethod
    def create_audio_manager_script(
        audio_assets: List[AudioAsset], script_name: str = "AudioManager"
    ) -> str:
        """生成音频管理器 GDScript

        创建一个全局单例用于播放音效、切换 BGM 等。

        Args:
            audio_assets: 音频资产列表
            script_name: 脚本类名

        Returns:
            GDScript 代码
        """
        # 按类型分组
        by_type = {t: [] for t in AudioType}
        for asset in audio_assets:
            by_type[asset.type].append(asset)

        script = f"""extends Node

# {script_name} - 全局音频管理器
# 自动生成,请勿手动修改

"""

        # 预加载音频资源
        script += "# 音频资源预加载\n"
        for audio_type, assets in by_type.items():
            if not assets:
                continue
            script += f"# {audio_type.value.upper()}\n"
            for asset in assets:
                const_name = asset.name.upper()
                script += f'const {const_name} = preload("{asset.file_path}")\n'

        script += '''

# 当前播放的 BGM
var current_bgm: AudioStreamPlayer = null

# 音效播放池
var sfx_pool: Array[AudioStreamPlayer] = []
const MAX_SFX_POOL_SIZE = 10

func _ready() -> void:
\t# 初始化音效播放池
\tfor i in range(MAX_SFX_POOL_SIZE):
\t\tvar player = AudioStreamPlayer.new()
\t\tplayer.bus = "SFX"
\t\tadd_child(player)
\t\tsfx_pool.append(player)

func play_sfx(stream: AudioStream, volume_db: float = 0.0) -> void:
\t"""播放音效"""
\tfor player in sfx_pool:
\t\tif not player.playing:
\t\t\tplayer.stream = stream
\t\t\tplayer.volume_db = volume_db
\t\t\tplayer.play()
\t\t\treturn
\t# 池满时覆盖最早的
\tsfx_pool[0].stop()
\tsfx_pool[0].stream = stream
\tsfx_pool[0].volume_db = volume_db
\tsfx_pool[0].play()

func play_bgm(stream: AudioStream, fade_duration: float = 1.0) -> void:
\t"""播放背景音乐(带淡入淡出)"""
\tif current_bgm and current_bgm.playing:
\t\t# 淡出当前 BGM
\t\tvar tween = create_tween()
\t\ttween.tween_property(current_bgm, "volume_db", -80.0, fade_duration)
\t\ttween.tween_callback(current_bgm.stop)
\t
\t# 创建新的 BGM 播放器
\tif not current_bgm:
\t\tcurrent_bgm = AudioStreamPlayer.new()
\t\tcurrent_bgm.bus = "Music"
\t\tadd_child(current_bgm)
\t
\tcurrent_bgm.stream = stream
\tcurrent_bgm.volume_db = -80.0
\tcurrent_bgm.play()
\t
\t# 淡入新 BGM
\tvar tween = create_tween()
\ttween.tween_property(current_bgm, "volume_db", 0.0, fade_duration)

func stop_bgm(fade_duration: float = 1.0) -> void:
\t"""停止背景音乐"""
\tif current_bgm and current_bgm.playing:
\t\tvar tween = create_tween()
\t\ttween.tween_property(current_bgm, "volume_db", -80.0, fade_duration)
\t\ttween.tween_callback(current_bgm.stop)

func set_bus_volume(bus_name: String, volume_db: float) -> void:
\t"""设置音频总线音量"""
\tvar bus_idx = AudioServer.get_bus_index(bus_name)
\tif bus_idx >= 0:
\t\tAudioServer.set_bus_volume_db(bus_idx, volume_db)

func get_bus_volume(bus_name: String) -> float:
\t"""获取音频总线音量"""
\tvar bus_idx = AudioServer.get_bus_index(bus_name)
\tif bus_idx >= 0:
\t\treturn AudioServer.get_bus_volume_db(bus_idx)
\treturn 0.0
'''

        return script
