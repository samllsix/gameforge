"""GameForge - 角色序列帧（walk/run cycle）生成与切帧。

策略（对齐「sheet + SpriteFrames + 状态机」路线）：
1. AI：按固定 prompt 生成 idle / run 横向 strip（同角色多帧）
2. 切帧：等宽横条 → PNG 帧序列
3. 兜底：仅有一张静态图时，用 PIL 做 squash/拉伸 + 垂直 bob 造出 4 帧假跑步
   （不完美，但保证 AnimatedSprite2D 有可播动画，playtest 能看到“在动”）

产物目录：
    <project>/assets/gen/player/
        idle_0.png ...
        run_0.png ...
    <project>/assets/gen/player_anim.json   # 帧元数据，供 .tscn / 调试读取
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Tuple

import structlog

logger = structlog.get_logger()

# ── 美术模型设定（prompt 模板）────────────────────────────────
# 注意：全局 Stardew 像素风由 src/image/style.apply_art_style 自动追加，
# 这里只写「角色动作」约束，避免风格词重复。

ANIM_PROMPTS: Dict[str, Dict[str, Any]] = {
    "idle": {
        "frames": 2,
        "fps": 4,
        "prompt": (
            "2-frame idle animation sprite sheet, horizontal strip, "
            "same character standing still with subtle breathing, "
            "side view full body, equal frame size, "
            "character centered in each frame, plain solid background, "
            "no text, no UI, no motion blur"
        ),
        "size": [1024, 256],  # 2 帧 × 512
    },
    "run": {
        "frames": 4,
        "fps": 10,
        "prompt": (
            "4-frame run cycle sprite sheet, horizontal strip, "
            "same character running side view, full body, "
            "contact / down / pass / up poses, equal frame size, "
            "consistent colors and proportions across frames, "
            "character centered in each frame, plain solid background, "
            "no text, no UI, no motion blur"
        ),
        "size": [1024, 256],  # 4 帧 × 256 — 若 API 只回方形，切帧逻辑会自适应
    },
    "jump": {
        "frames": 2,
        "fps": 6,
        "prompt": (
            "2-frame jump animation sprite sheet, horizontal strip, "
            "same character side view full body, "
            "frame1 crouch prepare, frame2 airborne leap, "
            "equal frame size, plain solid background, no text"
        ),
        "size": [1024, 256],
    },
}

# 期望单帧边长（切帧与缩放目标）
FRAME_EDGE = 128
DEFAULT_ANIM_META = "assets/gen/player_anim.json"


def _project_gen_dir(project_path: str) -> str:
    return os.path.join(project_path, "assets", "gen")


def _player_anim_dir(project_path: str) -> str:
    return os.path.join(_project_gen_dir(project_path), "player")


def _remove_bg_like_asset_forge(img: Any) -> Any:
    try:
        from src.engine.godot.asset_forge import _remove_background

        return _remove_background(img)
    except Exception:  # noqa: BLE001
        return img


def _slice_horizontal_strip(img: Any, n_frames: int) -> List[Any]:
    """把横向 strip 按等宽切成 n 帧；宽度不能整除时用 round 分割。"""
    from PIL import Image

    if n_frames <= 1:
        return [img]
    w, h = img.size
    frames: List[Any] = []
    for i in range(n_frames):
        x0 = int(round(w * i / n_frames))
        x1 = int(round(w * (i + 1) / n_frames))
        if x1 <= x0:
            x1 = min(w, x0 + 1)
        frames.append(img.crop((x0, 0, x1, h)))
    return frames


def _normalize_frame(img: Any, edge: int = FRAME_EDGE) -> Any:
    from PIL import Image

    img = img.convert("RGBA")
    # 去透明边后再缩放到正方形画布中心，避免帧间重心乱跳
    bbox = img.getbbox()
    if bbox:
        img = img.crop(bbox)
    w, h = img.size
    scale = edge / max(w, h, 1)
    nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
    img = img.resize((nw, nh), Image.NEAREST)
    canvas = Image.new("RGBA", (edge, edge), (0, 0, 0, 0))
    canvas.paste(img, ((edge - nw) // 2, (edge - nh) // 2), img)
    return canvas


def _procedural_cycle(base_png: str, n_frames: int, kind: str) -> List[Any]:
    """从静态图造 squash/bob 帧序列（AI strip 失败时的确定性兜底）。"""
    from PIL import Image

    base = Image.open(base_png).convert("RGBA")
    frames: List[Any] = []
    for i in range(n_frames):
        t = i / max(1, n_frames)
        # run: 垂直 bob + 轻微横向 lean；idle: 只 bob；jump: squash→stretch
        if kind == "run":
            bob = int(round(4 * abs((t * 2 - 1))))
            sx = 1.0 + 0.04 * (0.5 - abs(t - 0.5))
            sy = 1.0 - 0.06 * abs(t - 0.5)
        elif kind == "jump":
            if i == 0:
                sx, sy, bob = 1.08, 0.88, 2
            else:
                sx, sy, bob = 0.94, 1.1, -4
        else:  # idle
            bob = int(round(2 * (i % 2)))
            sx = sy = 1.0
        w, h = base.size
        nw, nh = max(1, int(w * sx)), max(1, int(h * sy))
        img = base.resize((nw, nh), Image.NEAREST)
        canvas = Image.new("RGBA", (max(w, nw), max(h, nh) + 8), (0, 0, 0, 0))
        ox = (canvas.width - nw) // 2
        oy = (canvas.height - nh) // 2 + bob
        canvas.paste(img, (ox, oy), img)
        frames.append(canvas)
    return frames


def _write_frames(frames: List[Any], anim_dir: str, anim: str) -> List[str]:
    os.makedirs(anim_dir, exist_ok=True)
    paths: List[str] = []
    for i, fr in enumerate(frames):
        path = os.path.join(anim_dir, f"{anim}_{i}.png")
        fr.save(path)
        paths.append(path)
    return paths


def _generate_ai_strip(
    project_path: str,
    anim: str,
    timeout: float = 75.0,
) -> Optional[str]:
    """用 AI 生成一条横向 strip，返回落盘 png 路径；失败 None。"""
    spec = ANIM_PROMPTS[anim]
    from src.image.ai_image_client import AIImageClient

    out_dir = _player_anim_dir(project_path)
    os.makedirs(out_dir, exist_ok=True)
    client = AIImageClient(
        output_dir=out_dir,
        prefer_provider=os.getenv("IMAGE_PREFER_PROVIDER", "step"),
    )
    # 额外强调 side-view platformer（覆盖全局 top-down 风格词对角色的干扰）
    prompt = (
        f"{spec['prompt']}, "
        f"2D platformer character, side-scrolling pose, "
        f"pixel art game asset for Godot"
    )
    result = client.generate_image(prompt=prompt, size=list(spec["size"]))
    src = result.get("image_path") or result.get("png_path") or result.get("filepath")
    if not result.get("success") or not src or not os.path.isfile(src):
        logger.warning("character_anim.ai_failed", anim=anim, result=str(result)[:160])
        return None
    return src


def build_player_animations(
    project_path: str,
    *,
    base_player_png: Optional[str] = None,
    enabled: Optional[bool] = None,
    prefer_ai: Optional[bool] = None,
    force: bool = False,
) -> Dict[str, Any]:
    """生成/整理玩家 idle/run/jump 帧，写入 assets/gen/player/。

    返回::
        {
          "ok": True,
          "source": "ai_strip" | "procedural" | "existing",
          "anims": {
            "idle": {"fps": 4, "loop": True, "frames": ["res://...", ...]},
            "run": {...},
            "jump": {...},
          },
          "meta_path": "assets/gen/player_anim.json",
        }
    """
    if enabled is None:
        enabled = os.getenv("GAMEFORGE_PLAYER_ANIM", "1").strip().lower() not in {"0", "false", "no"}
    if not enabled:
        return {"ok": False, "source": "disabled", "anims": {}}

    anim_dir = _player_anim_dir(project_path)
    meta_path = os.path.join(_project_gen_dir(project_path), "player_anim.json")
    if not force and os.path.isfile(meta_path):
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                cached = json.load(f)
            if cached.get("anims"):
                logger.info("character_anim.cache_hit", keys=list(cached["anims"].keys()))
                return cached
        except Exception:  # noqa: BLE001
            pass

    if base_player_png is None:
        for cand in (
            os.path.join(_project_gen_dir(project_path), "player.png"),
            os.path.join(anim_dir, "base.png"),
        ):
            if os.path.isfile(cand):
                base_player_png = cand
                break

    # 无任何底图时画一个简易像素小人，保证程序化 cycle 有输入
    if base_player_png is None:
        try:
            from PIL import Image, ImageDraw

            os.makedirs(anim_dir, exist_ok=True)
            base_player_png = os.path.join(anim_dir, "base.png")
            img = Image.new("RGBA", (FRAME_EDGE, FRAME_EDGE), (0, 0, 0, 0))
            d = ImageDraw.Draw(img)
            d.rectangle([40, 40, 88, 100], fill=(120, 220, 255, 255))
            d.ellipse([44, 16, 84, 56], fill=(255, 220, 160, 255))
            d.rectangle([48, 100, 60, 120], fill=(80, 140, 200, 255))
            d.rectangle([68, 100, 80, 120], fill=(80, 140, 200, 255))
            img.save(base_player_png)
            logger.info("character_anim.placeholder_base", path=base_player_png)
        except Exception as e:  # noqa: BLE001
            logger.warning("character_anim.placeholder_failed", error=str(e))
            base_player_png = None

    if prefer_ai is None:
        prefer_ai = os.getenv("GAMEFORGE_PLAYER_ANIM_AI", "1").strip().lower() not in {"0", "false", "no"}

    source = "procedural"
    anims_meta: Dict[str, Any] = {}

    for anim, spec in ANIM_PROMPTS.items():
        n = int(spec["frames"])
        fps = float(spec["fps"])
        frames_out: List[Any] = []
        src_label = "procedural"

        # 已有切好的帧则复用
        existing = [
            os.path.join(anim_dir, f"{anim}_{i}.png")
            for i in range(n)
            if os.path.isfile(os.path.join(anim_dir, f"{anim}_{i}.png"))
        ]
        if len(existing) == n:
            src_label = "existing"
            frame_paths = existing
        else:
            if prefer_ai:
                strip_path = _generate_ai_strip(project_path, anim)
                if strip_path:
                    try:
                        from PIL import Image

                        strip = Image.open(strip_path).convert("RGBA")
                        strip = _remove_bg_like_asset_forge(strip)
                        raw_frames = _slice_horizontal_strip(strip, n)
                        frames_out = [_normalize_frame(fr, FRAME_EDGE) for fr in raw_frames]
                        # AI 条带帧数/比例不稳时，若切出全透明帧则整段放弃
                        if all(fr.getbbox() is None for fr in frames_out):
                            frames_out = []
                        else:
                            src_label = "ai_strip"
                            # 清掉原始 strip，避免和帧文件混淆
                            try:
                                if os.path.abspath(strip_path).endswith(".png"):
                                    # 若 AI 落在 anim 目录且不是帧文件名，删除
                                    base_name = os.path.basename(strip_path)
                                    if not (base_name.startswith(f"{anim}_") and base_name[5:-4].isdigit()):
                                        os.remove(strip_path)
                            except OSError:
                                pass
                    except Exception as e:  # noqa: BLE001
                        logger.warning("character_anim.slice_failed", anim=anim, error=str(e))
                        frames_out = []

            if not frames_out:
                if not base_player_png:
                    logger.warning("character_anim.no_base", anim=anim)
                    continue
                frames_out = [
                    _normalize_frame(fr, FRAME_EDGE)
                    for fr in _procedural_cycle(base_player_png, n, anim)
                ]
                src_label = "procedural"

            frame_paths = _write_frames(frames_out, anim_dir, anim)

        res_frames = [
            {"path": "res://assets/gen/player/" + os.path.basename(p), "index": i}
            for i, p in enumerate(frame_paths)
        ]
        anims_meta[anim] = {
            "fps": fps,
            "loop": anim != "jump",
            "frames": res_frames,
            "source": src_label,
        }
        if src_label == "ai_strip":
            source = "ai_strip"
        elif source != "ai_strip" and src_label == "procedural":
            source = "procedural"

    payload = {
        "ok": bool(anims_meta),
        "source": source,
        "frame_edge": FRAME_EDGE,
        "anims": anims_meta,
        "meta_path": DEFAULT_ANIM_META,
        "prompts": {k: v["prompt"] for k, v in ANIM_PROMPTS.items()},
    }
    try:
        os.makedirs(os.path.dirname(meta_path), exist_ok=True)
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except OSError as e:
        logger.warning("character_anim.meta_write_failed", error=str(e))

    logger.info(
        "character_anim.done",
        source=source,
        anims=list(anims_meta.keys()),
        frames=sum(len(v["frames"]) for v in anims_meta.values()),
    )
    return payload


def load_player_anim_meta(project_path: str) -> Dict[str, Any]:
    meta_path = os.path.join(_project_gen_dir(project_path), "player_anim.json")
    if not os.path.isfile(meta_path):
        return {}
    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return {}
