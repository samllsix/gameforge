# GameForge 音频生成规范

## 概述

GameForge 音频生成管线自动根据游戏设计文档(GDM)生成对话、音效、背景音乐和环境音。

## 支持的音频类型

| 类型 | 用途 | 模型 | 格式 | 推荐时长 |
|------|------|------|------|----------|
| **dialogue** | 角色对话/旁白 | TTS (OpenAI/Edge) | OGG/MP3 | 根据文本自动 |
| **sfx** | 音效 | ElevenLabs/AudioGen | OGG/WAV | 1-5 秒 |
| **bgm** | 背景音乐 | Suno/Udio/MusicGen | OGG/MP3 | 30-120 秒 |
| **ambient** | 环境音 | SFX 模型 | OGG | 5-20 秒(循环) |

## GDM 音频需求格式

在 Game Design Model 中添加音频需求:

```json
{
  "dialogues": [
    {
      "speaker": "Player",
      "text": "Watch out! Enemies ahead!",
      "emotion": "urgent"
    }
  ],
  "sfx": [
    {
      "name": "footstep_stone",
      "description": "heavy footsteps on stone floor",
      "duration": 2.0,
      "trigger": "player_walk"
    },
    {
      "name": "sword_swing",
      "description": "sword swoosh through air",
      "duration": 0.8,
      "trigger": "attack_action"
    }
  ],
  "bgm": [
    {
      "name": "battle_theme",
      "description": "intense electronic battle music",
      "genre": "electronic",
      "mood": "tense",
      "duration": 90.0,
      "loop": true
    }
  ],
  "ambient": [
    {
      "name": "forest_ambient",
      "description": "forest sounds with birds and wind",
      "duration": 15.0,
      "scene": "forest_level"
    }
  ]
}
```

## 生成流程

1. **需求提取**: AudioGenerator 从 GDM 中提取音频需求
2. **Prompt 优化**: 转换为模型专用 prompt(英文,简洁具体)
3. **并发生成**: 最多同时生成 3 个音频(可配置 `max_concurrent_audio`)
4. **文件写入**: 写入 `res://assets/audio/{type}/` 目录
5. **导入配置**: 自动生成 Godot `.import` 文件

## 目录结构

```
res://
  assets/
    audio/
      dialogues/        # 对话/旁白
      sfxs/             # 音效
      bgm/              # 背景音乐
      ambients/         # 环境音
  scripts/
    AudioManager.gd     # 全局音频管理器
```

## Godot 音频总线

自动分配音频总线:

- **Voice**: 对话/旁白
- **SFX**: 音效
- **Music**: 背景音乐
- **Ambient**: 环境音
- **Master**: 主总线

在 Godot 中调整总线音量:

```gdscript
AudioManager.set_bus_volume("Music", -10.0)  # 降低 BGM 音量
```

## 使用 AudioManager

生成的 `AudioManager.gd` 提供全局音频控制:

```gdscript
# 播放音效
AudioManager.play_sfx(AudioManager.FOOTSTEP_STONE)

# 切换 BGM(带淡入淡出)
AudioManager.play_bgm(AudioManager.BATTLE_THEME, 2.0)

# 停止 BGM
AudioManager.stop_bgm(1.5)
```

## Prompt 编写指南

### 对话/旁白 (TTS)
- 直接用台词文本
- 可指定 emotion: urgent/calm/sad/excited
- 支持多语言(取决于 TTS 模型)

```json
{
  "speaker": "Boss",
  "text": "You dare challenge me?",
  "emotion": "angry"
}
```

### 音效 (SFX)
- 描述动作和材质
- 指定节奏和力度
- 用英文,避免抽象形容词

**好的 prompt**:
- "heavy footsteps on stone floor, steady pace"
- "metal sword clashing against metal shield"
- "wooden door creaking open slowly"

**差的 prompt**:
- "好听的脚步声"
- "epic sound"
- "cool effect"

### 背景音乐 (BGM)
- 描述风格、情绪、节奏
- 指定 genre 和 mood
- 建议 60-90 秒(循环播放)

```json
{
  "description": "fast-paced electronic battle music with heavy drums",
  "genre": "electronic",
  "mood": "tense",
  "duration": 80.0,
  "loop": true
}
```

### 环境音 (Ambient)
- 描述场景氛围
- 建议 10-20 秒(循环)
- 音量低于 SFX 和 BGM

```json
{
  "description": "cave ambience with water drips and distant echoes",
  "duration": 15.0
}
```

## 配置

在 `config/config.yaml` 中配置音频生成:

```yaml
audio:
  enabled: true
  max_concurrent_audio: 3  # 并发生成数量
  audio_api_key: "your-api-key"
  tts_provider: "openai"  # openai/edge
  sfx_provider: "elevenlabs"  # elevenlabs/audiogen
  music_provider: "suno"  # suno/udio/musicgen
  audio_model_config:
    tts_voice: "alloy"
    sfx_duration: 3.0
    bgm_duration: 60.0
```

## 注意事项

1. **API 费用**: 音频生成模型按秒计费,建议先生成少量测试
2. **时长控制**: TTS 根据文本自动确定时长,SFX/BGM 需指定 duration
3. **格式选择**: Godot 推荐 OGG(体积小,质量好),避免 WAV(体积大)
4. **循环设置**: BGM 和 Ambient 建议 `loop: true`,自动无缝循环
5. **音量平衡**: SFX 音量通常高于 Ambient,低于对话

## 故障排查

### 音频生成失败
- 检查 API key 是否配置
- 确认 prompt 为英文且不超过 200 字符
- 查看日志中的具体错误

### 音频无法播放
- 确认 `.import` 文件已生成
- 在 Godot 中重新导入资源(项目 → 重新导入资源)
- 检查音频格式是否为 OGG/MP3

### 音量问题
- 调整 Godot 音频总线音量
- 在 `AudioManager` 中设置默认音量
- 检查音频文件本身是否过大/过小

## 扩展

### 添加新的音频提供商

1. 在 `src/models/audio_model.py` 中实现新的模型类
2. 继承 `AudioModel` 抽象基类
3. 实现 `generate()` 和 `get_supported_formats()` 方法
4. 在 `AudioModelFactory` 中注册

### 自定义音频总线

修改 `GodotAudioTools.generate_audio_bus_layout()` 中的总线列表:

```python
buses = ["Master", "SFX", "Music", "Voice", "Ambient", "UI"]
```

## 示例:完整音频配置

```json
{
  "dialogues": [
    {"speaker": "Player", "text": "Let's go!", "emotion": "excited"}
  ],
  "sfx": [
    {"name": "jump", "description": "character jumping sound", "duration": 0.5},
    {"name": "coin", "description": "collecting coin sound", "duration": 0.3}
  ],
  "bgm": [
    {
      "name": "level1_theme",
      "description": "upbeat adventure music with piano and strings",
      "genre": "orchestral",
      "mood": "adventurous",
      "duration": 90.0,
      "loop": true
    }
  ],
  "ambient": [
    {"name": "wind", "description": "gentle wind blowing", "duration": 10.0}
  ]
}
```

生成后:
- 4 个音频文件写入对应目录
- AudioManager.gd 包含 4 个预加载常量
- 可在 GDScript 中直接调用 `AudioManager.play_sfx(AudioManager.JUMP)`
