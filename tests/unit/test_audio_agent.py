"""GameForge - AudioGeneratorAgent 单元测试

覆盖修复点：
- 构造器过不了 BaseAgent 校验（llm_client / 字符串 agent_type）→ 现在可正常实例化
- execute() 抽象方法已实现，run() 是兼容别名
- GDM 松散形态（dict / 裸字符串条目）容错
- .import 不再把 Python 条件表达式写进文件，格式映射正确
- api_key 能从 audio.providers.stepfun.api_key_env 指定的环境变量兜底
"""

import asyncio
import os
from unittest import mock

import pytest

from src.agents.audio_generator import (
    _IMPORT_META,
    _TYPE_DIRS,
    AudioGeneratorAgent,
)
from src.core.state.game_state import AgentType
from src.models.audio_model import (
    AudioAsset,
    AudioFormat,
    AudioModelFactory,
    AudioType,
)

# ─── 测试替身：完全绕开真实网络/API 调用 ─────────────────


class _FakeAudioModel:
    """鸭子类型 AudioModel：不给 .generate 联网，返回固定字节。"""

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    async def generate(self, prompt, audio_type, duration=None, **kwargs):
        if "boom" in prompt:
            raise RuntimeError("boom")
        return b"FAKEAUDIO"

    def get_supported_formats(self):
        return [AudioFormat.MP3]


def _fake_factory(**kwargs):
    return _FakeAudioModel(**kwargs)


# ─── 构造器 / 契约 ───────────────────────────────────────


def test_audio_agent_constructs_without_error():
    """回归：旧实现把 llm_client 和字符串 agent_type 传给 BaseAgent，实例化即炸。"""
    agent = AudioGeneratorAgent(config={})
    assert agent.agent_type == AgentType.CODE_GENERATOR
    assert agent.audio_dir_res == "res://assets/audio"
    assert agent.llm_client is None
    # 配置段可覆盖输出目录
    agent2 = AudioGeneratorAgent(config={
        "agents": {"audio_generation": {"audio_dir": "res://snd"}}
    })
    assert agent2.audio_dir_res == "res://snd"


def test_audio_agent_has_execute_and_run_alias():
    """BaseAgent 抽象方法是 execute()，旧实现只有 run() 导致不可实例化。"""
    agent = AudioGeneratorAgent(config={})
    assert asyncio.iscoroutinefunction(agent.execute)
    assert asyncio.iscoroutinefunction(agent.run)


def test_run_alias_matches_execute(tmp_path):
    """run() 委托 execute()；created_at/时间戳不可比，只比对确定性字段。"""
    agent = AudioGeneratorAgent(config={})
    state = {
        "project_context": {"project_root": str(tmp_path)},
        "game_design_model": {"dialogues": [{"speaker": "npc", "text": "hi"}]},
    }
    with mock.patch.object(AudioModelFactory, "create_model", new=_fake_factory):
        via_run = asyncio.run(agent.run(state))
        via_execute = asyncio.run(agent.execute(state))

    def _shape(assets):
        return {(v["name"], v["file_path"]) for v in assets.values()}

    assert _shape(via_run["audio_assets"]) == _shape(via_execute["audio_assets"])
    assert [m["type"] for m in via_run["message_bus"]] == ["audio_generated"]


def test_max_concurrent_read_from_agent_section():
    agent = AudioGeneratorAgent(config={
        "agents": {"audio_generation": {"max_concurrent_audio": 2}}
    })
    assert agent._audio_setting("max_concurrent_audio", 3) == 2
    plain = AudioGeneratorAgent(config={})
    assert plain._audio_setting("max_concurrent_audio", 3) == 3


# ─── 端到端（stub 模型） ─────────────────────────────────


def test_execute_generates_and_writes_audio_files(tmp_path):
    agent = AudioGeneratorAgent(config={})
    state = {
        "project_context": {"project_root": str(tmp_path)},
        "game_design_model": {
            "dialogues": [{"speaker": "hero", "text": "Hello world"}],
            "sfx": [{"name": "jump", "description": "jump sound"}],
            "bgm": [{"name": "main_theme", "description": "happy tune", "loop": True}],
        },
    }
    with mock.patch.object(AudioModelFactory, "create_model", new=_fake_factory):
        result = asyncio.run(agent.execute(state))

    assert not result.get("error_log")
    assets = result["audio_assets"]
    # dict 的 key 是资产 id（md5），name 才是需求里的名字
    names = {v["name"] for v in assets.values()}
    assert names == {"dialogue_000", "jump", "main_theme"}

    # 目录映射：dialogue→dialogues，sfx→sfx（旧实现 sfx+"s" 会拼成 sfxs）
    assert _TYPE_DIRS == {
        AudioType.DIALOGUE: "dialogues",
        AudioType.SFX: "sfx",
        AudioType.BGM: "bgm",
        AudioType.AMBIENT: "ambient",
    }

    dialogue_file = tmp_path / "assets/audio/dialogues/dialogue_000.mp3"
    sfx_file = tmp_path / "assets/audio/sfx/jump.mp3"
    bgm_file = tmp_path / "assets/audio/bgm/main_theme.mp3"
    assert dialogue_file.read_bytes() == b"FAKEAUDIO"
    assert sfx_file.read_bytes() == b"FAKEAUDIO"
    assert bgm_file.read_bytes() == b"FAKEAUDIO"

    # 每个音频都有 .import（且 loop 透传）
    bgm_import = (tmp_path / "assets/audio/bgm/main_theme.mp3.import").read_text()
    assert "loop=true" in bgm_import
    assert 'importer="mp3"' in bgm_import


def test_execute_resolves_api_key_from_env(tmp_path):
    """api_key 兜底读取 audio.providers.stepfun.api_key_env 指定的环境变量。"""
    agent = AudioGeneratorAgent(config={
        "audio": {
            "default_tts_provider": "stepfun",
            "providers": {"stepfun": {
                "api_key_env": "TEST_GF_AUDIO_KEY",
                "model": "stepaudio-2.5-tts",
            }},
        },
    })
    state = {
        "project_context": {"project_root": str(tmp_path)},
        "game_design_model": {"dialogues": [{"speaker": "npc", "text": "hi"}]},
    }
    captured = {}

    def spy_factory(**kwargs):
        captured.update(kwargs)
        return _FakeAudioModel(**kwargs)

    with (
        mock.patch.dict(os.environ, {"TEST_GF_AUDIO_KEY": "secret-key"}, clear=False),
        mock.patch.object(AudioModelFactory, "create_model", new=spy_factory),
    ):
        result = asyncio.run(agent.execute(state))

    assert not result.get("error_log")
    assert captured["api_key"] == "secret-key"
    assert captured["config"]["model"] == "stepaudio-2.5-tts"
    assert captured["config"]["provider"] == "stepfun"


def test_failed_generation_skipped_others_succeed(tmp_path):
    """单条生成失败（模型抛异常）不拖垮整批，只跳过失败项。"""
    agent = AudioGeneratorAgent(config={})
    state = {
        "project_context": {"project_root": str(tmp_path)},
        "game_design_model": {
            "sfx": [
                {"name": "good", "description": "fine"},
                {"name": "bad", "description": "boom"},
            ],
        },
    }
    with mock.patch.object(AudioModelFactory, "create_model", new=_fake_factory):
        result = asyncio.run(agent.execute(state))

    assert not result.get("error_log")
    names = {v["name"] for v in result["audio_assets"].values()}
    assert names == {"good"}
    assert (tmp_path / "assets/audio/sfx/good.mp3").is_file()
    assert not (tmp_path / "assets/audio/sfx/bad.mp3").exists()


def test_no_audio_requirements_returns_empty(tmp_path):
    agent = AudioGeneratorAgent(config={})
    result = asyncio.run(agent.execute({
        "project_context": {"project_root": str(tmp_path)},
        "game_design_model": {"menus": [{"name": "m"}]},
    }))
    assert result == {"audio_assets": {}}


# ─── 需求抽取容错 ────────────────────────────────────────


def test_extract_handles_loose_gdm_shapes():
    agent = AudioGeneratorAgent(config={})
    state = {
        "game_design_model": {
            # dict[speaker -> text]
            "dialogues": {"npc": "Welcome"},
            # 裸字符串条目
            "sfx": ["explosion"],
            # music 别名
            "music": [{"name": "loop", "description": "chill", "loop": False}],
        }
    }
    reqs = agent._extract_audio_requirements(state)
    by_type = {}
    for r in reqs:
        by_type.setdefault(r["type"], []).append(r)

    dia = by_type[AudioType.DIALOGUE][0]
    assert dia["name"] == "dialogue_000"
    assert dia["prompt"] == "Welcome"
    assert dia["metadata"]["speaker"] == "npc"

    sfx = by_type[AudioType.SFX][0]
    assert sfx["name"] == "explosion"
    assert sfx["prompt"] == "explosion"

    bgm = by_type[AudioType.BGM][0]
    assert bgm["name"] == "loop"
    assert bgm["metadata"]["loop"] is False


# ─── .import 生成器 ──────────────────────────────────────


def test_import_config_resolves_values_not_literal_conditional(tmp_path):
    """回归：f-string 里没包 {} 的条件表达式曾被原样写进 .import 文件。"""
    agent = AudioGeneratorAgent(config={})
    asset = AudioAsset(
        id="abc123def4567",
        name="boom",
        type=AudioType.SFX,
        format=AudioFormat.OGG,
        file_path="res://assets/audio/sfx/boom.ogg",
        duration=1.0,
        description="",
        prompt="",
    )
    audio_path = tmp_path / "boom.ogg"
    asyncio.run(agent._generate_godot_import(audio_path, asset))

    content = (tmp_path / "boom.ogg.import").read_text()
    assert 'importer="oggvorbisstr"' in content
    assert 'type="AudioStreamOGGVorbis"' in content
    assert 'uid="uid://abc123def4567"' in content
    # 不能让 Python 条件表达式泄漏成文本
    assert "if asset.format" not in content
    assert "AudioFormat.OGG" not in content
    # 目标扩展名按格式映射，OGG -> .oggstr（旧实现全部写死 .oggstr）
    assert "boom.ogg-abc123def4567.oggstr" in content


def test_import_meta_covers_all_formats():
    assert set(_IMPORT_META) == {AudioFormat.WAV, AudioFormat.OGG, AudioFormat.MP3}


@pytest.mark.parametrize(
    "audio_format,expected",
    [
        (AudioFormat.OGG, ("oggvorbisstr", "AudioStreamOGGVorbis", "oggstr")),
        (AudioFormat.MP3, ("mp3", "AudioStreamMP3", "mp3str")),
        (AudioFormat.WAV, ("wav", "AudioStreamWAV", "wavstr")),
    ],
)
def test_import_meta_format_mapping(audio_format, expected):
    assert _IMPORT_META[audio_format] == expected
