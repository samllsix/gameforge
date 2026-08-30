"""GameForge - 程序化 8-bit 音效合成器

用 numpy 直接合成经典芯片音效并写出 16-bit PCM wav：
跳跃 / 吃币 / 死亡 / 按钮点击。零 AI 成本、零网络、幂等（已存在则跳过）。

产物由 scene_to_godot.write_project 写入项目 assets/sfx/，
运行时脚本按需 load（文件缺失时静默无声，不会报错）。
"""

from __future__ import annotations

import os
import wave
from typing import Any, Dict

import structlog

logger = structlog.get_logger()

SAMPLE_RATE = 22050

# 每个音效的合成参数
_SFX_SPEC: Dict[str, Dict[str, Any]] = {
    "jump": {"kind": "sweep", "f0": 320, "f1": 620, "duration": 0.14, "volume": 0.5},
    "coin": {"kind": "two_tone", "f0": 988, "f1": 1319, "duration": 0.16, "volume": 0.45},
    "death": {"kind": "sweep", "f0": 320, "f1": 55, "duration": 0.45, "volume": 0.55},
    "click": {"kind": "tick", "f0": 1100, "duration": 0.04, "volume": 0.4},
}


def _square(phase: Any) -> Any:
    import numpy as np

    return np.sign(np.sin(phase))


def _render(spec: Dict[str, Any]) -> Any:
    import numpy as np

    n = int(SAMPLE_RATE * spec["duration"])
    t = np.arange(n) / SAMPLE_RATE
    vol = spec["volume"]

    if spec["kind"] == "sweep":
        # 频率线性扫 + 衰减包络（跳跃上扬 / 死亡下坠）
        freqs = np.linspace(spec["f0"], spec["f1"], n)
        phase = 2 * np.pi * np.cumsum(freqs) / SAMPLE_RATE
        wave_form = _square(phase)
        env = np.linspace(1.0, 0.25, n)
    elif spec["kind"] == "two_tone":
        # 双音阶（经典吃币：B5 → E6）
        half = n // 2
        wave_form = np.concatenate([
            _square(2 * np.pi * spec["f0"] * t[:half]),
            _square(2 * np.pi * spec["f1"] * t[half:]),
        ])
        env = np.concatenate([
            np.linspace(1.0, 0.6, half), np.linspace(1.0, 0.2, n - half),
        ])
    else:  # tick
        wave_form = _square(2 * np.pi * spec["f0"] * t)
        env = np.linspace(1.0, 0.0, n) ** 2

    samples = (wave_form * env * vol * 32767).astype("<i2")
    return samples


def _render_bgm() -> Any:
    """8 小节芯片风循环 BGM：方波琶音旋律 + 三角波低音，~9.6s @120BPM。"""
    import numpy as np

    bpm = 120
    beat = 60.0 / bpm
    total = int(SAMPLE_RATE * beat * 32)  # 32 个八分音符 = 8 小节
    buf = np.zeros(total)
    # A 小调进行: Am F C G（根音低音 + 五度琶音）
    prog = [
        [220.0, 261.6, 329.6], [174.6, 220.0, 261.6],
        [130.8, 164.8, 196.0], [196.0, 246.9, 293.7],
    ]
    step = total // 16
    for i in range(16):
        chord = prog[(i // 4) % 4]
        seg = slice(i * step, (i + 1) * step)
        t = np.arange(step) / SAMPLE_RATE
        # 低音：根音三角波
        bass = chord[0] / 2
        tri = 2 * np.abs(2 * ((t * bass) % 1) - 1) - 1
        buf[seg] += tri * 0.18
        # 琶音：和弦三音轮流（方波，短促）
        note = chord[i % 3] * 2
        sq = np.sign(np.sin(2 * np.pi * note * t)) * 0.10
        env = np.linspace(1.0, 0.3, step)
        buf[seg] += sq * env
    # 首尾交叉淡化保证无缝循环
    fade = int(SAMPLE_RATE * 0.02)
    buf[:fade] = buf[:fade] * np.linspace(0.0, 1.0, fade) + buf[-fade:] * np.linspace(1.0, 0.0, fade)
    return (np.clip(buf, -1, 1) * 32767).astype("<i2")


def write_sfx(project_path: str, force: bool = False) -> Dict[str, str]:
    """把全部音效与 BGM 写入 <project>/assets/sfx/*.wav，返回 {name: 相对路径}。"""
    out_dir = os.path.join(project_path, "assets", "sfx")
    os.makedirs(out_dir, exist_ok=True)
    written: Dict[str, str] = {}

    bgm_path = os.path.join(out_dir, "bgm.wav")
    if force or not os.path.isfile(bgm_path):
        try:
            samples = _render_bgm()
            with wave.open(bgm_path, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(SAMPLE_RATE)
                wf.writeframes(samples.tobytes())
            written["bgm"] = "assets/sfx/bgm.wav"
        except Exception as e:  # noqa: BLE001
            logger.warning("sfx_forge.bgm_failed", error=str(e))
    else:
        written["bgm"] = "assets/sfx/bgm.wav"  # 与音效一致：命中缓存也计入返回

    for name, spec in _SFX_SPEC.items():
        out_path = os.path.join(out_dir, f"{name}.wav")
        if os.path.isfile(out_path) and not force:
            written[name] = os.path.relpath(out_path, project_path).replace(os.sep, "/")
            continue
        try:
            samples = _render(spec)
            with wave.open(out_path, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(SAMPLE_RATE)
                wf.writeframes(samples.tobytes())
            written[name] = os.path.relpath(out_path, project_path).replace(os.sep, "/")
        except Exception as e:  # noqa: BLE001
            logger.warning("sfx_forge.write_failed", sfx=name, error=str(e))
    if written:
        logger.info("sfx_forge.done", count=len(written))
    return written
