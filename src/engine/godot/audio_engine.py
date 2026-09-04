r"""GameForge - AI 音频生成引擎

用 AI 模型为游戏项目生成 BGM + 音效，失败/未安装/模型缺失时
自动回退到 sfx_forge 的程序化 8-bit 合成。

后端实现（可切换）：
- musicgen: MusicGen Small（300M，diffusers，无需授权，CC-BY-NC）
- stable_audio_3: Stable Audio 3 Small（433M，需 HF token gated，商用友好）
- procedural: 纯程序化合成（零依赖，保底）

设计原则（与 asset_forge 一致）：
- 失败开放：任何异常都不阻断游戏生成，回退到程序化音效
- 幂等：已生成的文件直接复用，不重复生成
- 可配置：通过 GAMEFORGE_AUDIO_ENABLED / GAMEFORGE_AUDIO_BACKEND 环境变量
- 最小依赖：torch / diffusers 按需延迟导入

模型与缓存目录优先使用 D:\he（通过 GAMEFORGE_AUDIO_CACHE 环境变量配置）。
"""

from __future__ import annotations

import os
import threading
from typing import Any, Dict, List, Optional, Tuple

import structlog

logger = structlog.get_logger()

# ─── 配置 ───────────────────────────────────────────────
ENABLED = os.environ.get("GAMEFORGE_AUDIO_ENABLED", "1") == "1"
BACKEND = os.environ.get("GAMEFORGE_AUDIO_BACKEND", "musicgen")  # musicgen / stable_audio_3 / procedural
CACHE_DIR = os.environ.get("GAMEFORGE_AUDIO_CACHE", r"D:\he\audio_cache")
# BGM 短循环策略：游戏 BGM 循环播放，12 秒循环段即可（30s 会让 CPU 推理 3 分钟）
BGM_SECONDS = int(os.environ.get("GAMEFORGE_AUDIO_BGM_SECS", "12"))
GUIDANCE_SCALE = float(os.environ.get("GAMEFORGE_AUDIO_CFG", "1.0"))  # 1.0=关CFG提速1.37x
QUANTIZE = os.environ.get("GAMEFORGE_AUDIO_QUANTIZE", "1") == "1"  # int8 动态量化提速1.4x
GENERATE_TIMEOUT = int(os.environ.get("GAMEFORGE_AUDIO_TIMEOUT", "300"))  # 推理超时保护
# 跨项目 BGM 库：同 prompt 的 BGM 全局复用（每个 genre 只推理一次）
BGM_LIBRARY = os.environ.get("GAMEFORGE_AUDIO_LIBRARY", os.path.join(CACHE_DIR, "bgm_library"))
MAX_SFX_SECONDS = int(os.environ.get("GAMEFORGE_AUDIO_SFX_SECS", "3"))
SAMPLE_RATE = 32000  # MusicGen 原生采样率

# 运行时状态
_engine_lock = threading.Lock()
_model_loaded: Any = None  # None = 未加载, False = 加载失败, otherwise = model object
_import_checked = False
_import_ok = False


# ─── 可用性探测（只做一次）────────────────────────────
def _check_imports() -> bool:
    """探测 AI 音频依赖是否可用，结果缓存。"""
    global _import_checked, _import_ok
    if _import_checked:
        return _import_ok
    _import_checked = True

    if BACKEND == "procedural":
        _import_ok = False  # 直接走回退路径
        return False

    try:
        import torch  # noqa: F401

        if BACKEND == "musicgen":
            from transformers import MusicgenForConditionalGeneration  # noqa: F401
        elif BACKEND == "stable_audio_3":
            import stable_audio_tools  # noqa: F401
        _import_ok = True
    except Exception as e:  # noqa: BLE001
        logger.info("audio_engine.import_unavailable", backend=BACKEND, error=str(e)[:120])
        _import_ok = False
    return _import_ok


def is_available() -> bool:
    """AI 音频生成是否可用（开关 + 依赖 + 模型）。"""
    if not ENABLED:
        return False
    if BACKEND == "procedural":
        return False
    if not _check_imports():
        return False
    return _ensure_model_loaded()


# ─── 模型加载（懒加载 + 失败记忆）─────────────────────
def _ensure_model_loaded() -> bool:
    global _model_loaded
    with _engine_lock:
        if _model_loaded is False:
            return False
        if _model_loaded is not None:
            return True

        try:
            import torch

            os.makedirs(CACHE_DIR, exist_ok=True)
            # 配置 HuggingFace 缓存目录到 D 盘
            os.environ.setdefault("HUGGINGFACE_HUB_CACHE", CACHE_DIR)
            os.environ.setdefault("TORCH_HOME", os.path.join(CACHE_DIR, "torch"))

            device = "cuda" if torch.cuda.is_available() else "cpu"
            dtype = torch.float32 if device == "cpu" else torch.float16
            logger.info("audio_engine.loading", backend=BACKEND, device=device, dtype=str(dtype))

            if BACKEND == "musicgen":
                from transformers import MusicgenForConditionalGeneration, AutoProcessor

                # 优先用本地 modelscope 缓存路径（国内下载快）
                # modelscope 存储结构: <cache>/models/<org>--<name>/snapshots/<rev>/
                model_name = "facebook/musicgen-small"
                local_model_dir = os.path.join(
                    CACHE_DIR, "models", "AI-ModelScope--musicgen-small", "snapshots", "master"
                )
                if os.path.isdir(local_model_dir) and os.path.isfile(
                    os.path.join(local_model_dir, "model.safetensors")
                ):
                    model_path = local_model_dir
                    logger.info("audio_engine.using_local_model", path=model_path)
                else:
                    model_path = model_name
                    logger.info("audio_engine.using_hf_model", name=model_name)

                model = MusicgenForConditionalGeneration.from_pretrained(
                    model_path, torch_dtype=dtype, cache_dir=CACHE_DIR,
                )
                processor = AutoProcessor.from_pretrained(model_path, cache_dir=CACHE_DIR)
                # CPU 上用 int8 动态量化：线性层推理提速 ~1.4x，音频质量损失可忽略
                if QUANTIZE and device == "cpu":
                    model = torch.ao.quantization.quantize_dynamic(
                        model, {torch.nn.Linear}, dtype=torch.qint8
                    )
                    logger.info("audio_engine.int8_quantized")
                model = model.to(device).eval()
                _model_loaded = {
                    "type": "musicgen",
                    "model": model,
                    "processor": processor,
                    "device": device,
                    "dtype": dtype,
                }
                logger.info("audio_engine.musicgen_loaded")

            elif BACKEND == "stable_audio_3":
                from stable_audio_tools import get_pretrained_model

                model, cfg = get_pretrained_model("stabilityai/stable-audio-3-small-music")
                model = model.to(device).eval()
                _model_loaded = {
                    "type": "stable_audio_3",
                    "model": model,
                    "config": cfg,
                    "device": device,
                }
                logger.info("audio_engine.stable_audio_loaded")
            else:
                logger.warning("audio_engine.unknown_backend", backend=BACKEND)
                _model_loaded = False
                return False

            return True
        except Exception as e:  # noqa: BLE001
            logger.warning("audio_engine.load_failed", backend=BACKEND, error=str(e)[:200])
            _model_loaded = False
            return False


# ─── 核心生成 ─────────────────────────────────────────
def _generate_bgm_musicgen(prompt: str, seconds: int) -> Optional[Tuple[Any, int]]:
    """用 MusicGen 生成 BGM，返回 (audio_array, sample_rate)。"""
    from concurrent.futures import ThreadPoolExecutor
    from concurrent.futures import TimeoutError as FutureTimeout

    def _infer():
        import torch

        info = _model_loaded
        model = info["model"]
        processor = info["processor"]
        device = info["device"]

        inputs = processor(text=[prompt], padding=True, return_tensors="pt").to(device)
        # MusicGen 50 tokens/s：12s BGM = 600 tokens
        # guidance_scale=1.0 跳过无条件分支，计算量减半
        with torch.no_grad():
            audio_values = model.generate(
                **inputs,
                max_new_tokens=int(seconds * 50),
                do_sample=True,
                guidance_scale=GUIDANCE_SCALE,
            )
        audio = audio_values[0, 0].cpu().float().numpy()
        sr = model.config.audio_encoder.sampling_rate if hasattr(model.config, "audio_encoder") else 32000
        return audio, sr

    try:
        # 超时保护：超时线程继续跑完但结果丢弃，主流程回退程序化合成
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(_infer)
            return future.result(timeout=GENERATE_TIMEOUT)
    except FutureTimeout:
        logger.warning("audio_engine.inference_timeout", secs=GENERATE_TIMEOUT, prompt=prompt[:60])
        return None
    except Exception as e:  # noqa: BLE001
        logger.warning("audio_engine.musicgen_failed", error=str(e)[:200], prompt=prompt[:60])
        return None


def _generate_audio(prompt: str, seconds: float, kind: str = "bgm") -> Optional[Tuple[Any, int]]:
    """统一生成入口，kind: bgm / sfx。失败返回 None。"""
    if not _ensure_model_loaded():
        return None

    model_type = _model_loaded["type"]

    if model_type == "musicgen":
        if kind == "bgm":
            return _generate_bgm_musicgen(prompt, int(seconds))
        else:
            # MusicGen 主要做音乐，音效质量一般 → 返回 None 让调用方回退程序化
            logger.debug("audio_engine.musicgen_no_sfx", reason="musicgen better for music than sfx")
            return None
    else:
        # 其他后端暂未实现
        return None


def _write_wav(filepath: str, audio: Any, sr: int) -> None:
    """写 16-bit PCM wav。"""
    import numpy as np
    import wave

    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    samples = (np.clip(audio, -1.0, 1.0) * 32767).astype("<i2")
    with wave.open(filepath, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(samples.tobytes())


# ─── 跨项目 BGM 库（核心提速：每个 genre 只推理一次）──
def _library_path(prompt: str, seconds: int) -> str:
    """BGM 库文件路径：prompt 哈希 + 时长。"""
    import hashlib

    h = hashlib.sha256(f"{prompt}|{seconds}|{GUIDANCE_SCALE}".encode()).hexdigest()[:16]
    return os.path.join(BGM_LIBRARY, f"{h}_{seconds}s.wav")


def _library_get(prompt: str, seconds: int, dest: str) -> bool:
    """库命中则复制到项目，返回 True。"""
    src = _library_path(prompt, seconds)
    if not os.path.isfile(src) or os.path.getsize(src) < 1000:
        return False
    try:
        import shutil

        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copyfile(src, dest)
        logger.info("audio_engine.bgm_library_hit")
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("audio_engine.library_copy_failed", error=str(e)[:120])
        return False


def _library_put(prompt: str, seconds: int, src: str) -> None:
    """新 BGM 存入库供后续项目复用。"""
    try:
        import shutil

        dest = _library_path(prompt, seconds)
        os.makedirs(BGM_LIBRARY, exist_ok=True)
        shutil.copyfile(src, dest)
    except Exception as e:  # noqa: BLE001
        logger.warning("audio_engine.library_store_failed", error=str(e)[:120])


# ─── BGM 预设 ─────────────────────────────────────────
_BGM_PRESETS: Dict[str, Dict[str, Any]] = {
    "platformer": {
        "prompt": "upbeat 8-bit style platformer background music, catchy melody, playful chiptune, looping",
    },
    "rpg": {
        "prompt": "epic fantasy RPG adventure background music, orchestral strings and woodwinds, heroic atmospheric",
    },
    "scifi": {
        "prompt": "futuristic sci-fi electronic ambient music, synths and pads, mysterious space atmosphere",
    },
    "puzzle": {
        "prompt": "relaxing puzzle game background music, gentle piano and ambient textures, calm meditative",
    },
    "action": {
        "prompt": "intense action combat music, fast-paced drums and synths, energetic driving beat",
    },
    "horror": {
        "prompt": "dark horror ambient music, eerie drones and unsettling textures, suspenseful atmospheric",
    },
}


def _pick_bgm_preset(genre: str) -> Dict[str, Any]:
    """根据游戏类型匹配 BGM 预设，找不到就用 platformer。"""
    genre_l = genre.lower() if genre else ""
    # 按匹配优先级排序（先匹配更具体的）
    mapping = [
        ("horror", "horror"), ("scary", "horror"), ("terror", "horror"),
        ("platform", "platformer"), ("jump", "platformer"),
        ("puzzle", "puzzle"), ("match", "puzzle"), ("casual", "puzzle"),
        ("scifi", "scifi"), ("space", "scifi"), ("cyber", "scifi"), ("future", "scifi"),
        ("rpg", "rpg"), ("role", "rpg"), ("fantasy", "rpg"), ("adventure", "rpg"),
        ("action", "action"), ("shoot", "action"), ("fight", "action"), ("combat", "action"),
    ]
    for kw, preset in mapping:
        if kw in genre_l:
            return _BGM_PRESETS[preset]
    return _BGM_PRESETS["platformer"]


# ─── SFX 预设（程序化合成名称列表）───────────────────
_SFX_NAMES = ["jump", "coin", "death", "click"]


# ─── 公共 API ─────────────────────────────────────────
def generate_audio_for_project(
    project_path: str,
    genre: str = "platformer",
    bgm: bool = True,
    sfx_names: Optional[List[str]] = None,
    force: bool = False,
) -> Dict[str, str]:
    """为项目生成 BGM + 音效，写入 assets/sfx/，返回 {name: 相对路径}。

    AI 不可用时自动回退到程序化合成(sfx_forge)。
    已存在的文件跳过（幂等），force=True 强制重新生成。
    """
    out_dir = os.path.join(project_path, "assets", "sfx")
    os.makedirs(out_dir, exist_ok=True)

    if sfx_names is None:
        sfx_names = list(_SFX_NAMES)

    # 可用则用 AI，否则回退
    if ENABLED and is_available():
        logger.info("audio_engine.using_ai", backend=BACKEND, genre=genre)
        return _generate_with_ai(project_path, genre, bgm, sfx_names, force)
    else:
        reason = "disabled" if not ENABLED else f"backend_{BACKEND}_unavailable"
        logger.info("audio_engine.fallback_procedural", reason=reason)
        return _fallback_procedural(project_path, force)


def _generate_with_ai(
    project_path: str,
    genre: str,
    bgm: bool,
    sfx_names: List[str],
    force: bool,
) -> Dict[str, str]:
    """AI 生成 BGM，SFX 用程序化兜底（质量更可靠）。"""
    out_dir = os.path.join(project_path, "assets", "sfx")
    written: Dict[str, str] = {}
    fallback_bgm = False

    # ── BGM: 库复用 > AI 生成 ──
    if bgm:
        bgm_path = os.path.join(out_dir, "bgm.wav")
        if not force and os.path.isfile(bgm_path):
            written["bgm"] = "assets/sfx/bgm.wav"
        else:
            preset = _pick_bgm_preset(genre)
            prompt = preset["prompt"]
            seconds = BGM_SECONDS
            if _library_get(prompt, seconds, bgm_path):
                # 库命中：秒级复制，跳过推理
                written["bgm"] = "assets/sfx/bgm.wav"
            else:
                result = _generate_audio(prompt, seconds, kind="bgm")
                if result is not None:
                    audio, sr = result
                    try:
                        _write_wav(bgm_path, audio, sr)
                        _library_put(prompt, seconds, bgm_path)
                        written["bgm"] = "assets/sfx/bgm.wav"
                        logger.info("audio_engine.bgm_generated", genre=genre, secs=seconds)
                    except Exception as e:  # noqa: BLE001
                        logger.warning("audio_engine.bgm_write_failed", error=str(e)[:120])
                        fallback_bgm = True
                else:
                    fallback_bgm = True

    # ── SFX: 用程序化合成（小文件 + 质量稳定 + 秒级生成）──
    from . import sfx_forge

    procedural = sfx_forge.write_sfx(project_path, force=force)
    for name in sfx_names:
        if name in procedural:
            written[name] = procedural[name]
    if fallback_bgm and "bgm" not in written and "bgm" in procedural:
        written["bgm"] = procedural["bgm"]

    if fallback_bgm:
        logger.info("audio_engine.bgm_fell_back")

    if written:
        logger.info("audio_engine.done", count=len(written))
    return written


def _fallback_procedural(project_path: str, force: bool) -> Dict[str, str]:
    """完全回退到程序化合成（零 AI 依赖）。"""
    from . import sfx_forge

    return sfx_forge.write_sfx(project_path, force=force)
