"""GameForge - AI 素材锻造器

用 MCP 图像服务（类星露谷 2D 像素风，风格约束见 src/image/style.py）
为生成的游戏项目产出真实素材：视差背景 + 玩家/敌人/金币精灵。

设计要点：
- 无 AI key / 开关关闭 / 任何失败 → 返回空 dict，调用方回退到色块视觉（零行为变化）
- 并发生成（线程池），单图超时 + 总预算控制，最坏情况不拖死建场景
- 产物落 project/assets/gen/，已存在直接复用（幂等，前端轮询重试不重复扣调用）
- 精灵做去底处理（边缘 flood-fill 判定纯色底 → 透明），失败保留原图
"""

from __future__ import annotations

import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from typing import Any, Dict, List, Optional, Tuple

import structlog

logger = structlog.get_logger()

_gen_dirname = os.path.join("assets", "gen")

_path_locks: Dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()

# 对齐验收：pixel 是"原生像素尺寸"（像素画真正作画的分辨率），
# size 是落盘尺寸。pixel 必须整除 size 的每个分量，放大才是整数倍，
# 否则会出现"有的像素 2px、有的 3px"的假像素感（见 tests/unit/test_pixel_pipeline.py）。
_SPEC: Dict[str, Dict[str, Any]] = {
    "background": {
        "size": (1360, 768),
        "pixel": (340, 192),
        "dither": True,
        # 大画幅渐变素材：直接量化到主题调色板，层次分离最好（实测对比图确认）
        "palette_mode": "theme",
        "colors": 48,
        "prompt": (
            "wide parallax game background for a cozy platformer, "
            "rolling hills, barn and windmill on the horizon, fluffy clouds, "
            "no characters, no text, no UI"
        ),
        "remove_background": False,
    },
    "player": {
        "size": (512, 512),
        "pixel": (32, 32),
        "prompt": (
            "single player character game sprite, cute young farmer adventurer, "
            "full body, front view, standing pose, centered in frame"
        ),
        "remove_background": True,
    },
    "enemy": {
        "size": (512, 512),
        "pixel": (32, 32),
        "prompt": (
            "single enemy creature game sprite, grumpy purple slime monster, "
            "full body, front view, centered in frame"
        ),
        "remove_background": True,
    },
    "pickup": {
        "size": (512, 512),
        "pixel": (16, 16),
        "prompt": (
            "single collectible coin game sprite, golden star coin, "
            "slightly glowing, centered in frame"
        ),
        "remove_background": True,
    },
    "icon": {
        "size": (512, 512),
        "pixel": (128, 128),
        "prompt": (
            "square game app icon, cute mascot badge emblem, bold readable silhouette, "
            "centered composition with margin, no text"
        ),
        "remove_background": False,
    },
    "ground": {
        "size": (512, 512),
        "pixel": (32, 32),
        "prompt": (
            "seamless tileable pixel art grass ground texture, top-down view, "
            "uniform lighting, no borders, no objects"
        ),
        "remove_background": False,
    },
    "platform": {
        "size": (512, 512),
        "pixel": (32, 32),
        "prompt": (
            "seamless tileable pixel art wooden plank platform texture, "
            "uniform lighting, no borders"
        ),
        "remove_background": False,
    },
    "decoration": {
        "size": (512, 512),
        "pixel": (32, 32),
        "prompt": (
            "single lush green leafy tree game sprite, pixel art, "
            "full tree, centered in frame"
        ),
        "remove_background": True,
    },
    "npc": {
        "size": (512, 512),
        "pixel": (32, 32),
        "prompt": (
            "single friendly villager NPC game sprite, shopkeeper with apron, "
            "full body, front view, centered in frame"
        ),
        "remove_background": True,
    },
}


def _pixel_art_enabled() -> bool:
    """像素化后处理开关（默认开）。关闭则退回旧的"直接 NEAREST 缩放"行为。"""
    return os.getenv("GAMEFORGE_PIXEL_ART", "1").strip().lower() not in {"0", "false", "no"}


def _theme_palette(scene_ir: Any) -> Optional[list]:
    """按场景主题解析统一调色板（palette_base → 调色板）；解析失败返回 None。

    这是 palette_base 此前只被程序化几何（scene_to_godot._get_palette）使用的补漏点。
    """
    try:
        from src.agents.art_director import resolve_palette_base
        from src.image.procedural.pixel_pipeline import palette_for

        return palette_for(resolve_palette_base(scene_ir))
    except Exception as e:  # noqa: BLE001
        logger.warning("asset_forge.palette_resolve_failed", error=str(e))
        return None


def _pixel_opts_for(
    mode: str, theme: Optional[list], slot_mode: Optional[str] = None, slot_colors: Optional[int] = None
) -> Dict[str, Any]:
    """按全局模式 + 槽位覆盖，装配 pixelate 的关键字参数。

    mode / slot_mode 取值：
        harmonize — 逐图提色保留明暗细节，色相有界对齐主题（细节优先，适合精灵/地块）
        theme     — 硬量化到主题调色板（统一性最强，适合大画幅渐变背景）
        auto/off  — 纯逐图自提取
    """
    colors = int(slot_colors or os.getenv("GAMEFORGE_PIXEL_COLORS", "24"))
    effective = (slot_mode or mode).strip().lower()
    if effective in {"auto", "off", "0", "false", "no"} or not theme:
        return {"palette": None, "colors": colors}
    if effective == "theme":
        return {
            "palette": theme,
            "colors": colors,
            "accents": int(os.getenv("GAMEFORGE_PIXEL_ACCENTS", "6")),
        }
    return {"palette": None, "colors": colors, "harmonize": theme}


def _pixel_options(scene_ir: Any, slot_mode: Optional[str] = None) -> Dict[str, Any]:
    """解析默认（无槽位覆盖）的像素化量化参数。

    GAMEFORGE_PIXEL_PALETTE：
        harmonize（默认）— 逐图提取调色板，保留明暗细节，再把色相有界地对齐到主题。
                           兼顾"细节"与"全项目颜色像一家人"。
        theme            — 全项目硬量化到主题调色板，统一性最强，
                           但灰度素材（地面等）可能被压成很少几种颜色而偏平。
        auto / off       — 纯逐图自提取，不做任何主题对齐。
    """
    mode = os.getenv("GAMEFORGE_PIXEL_PALETTE", "harmonize")
    if mode.strip().lower() in {"auto", "off", "0", "false", "no"}:
        return _pixel_opts_for("auto", None, slot_mode)
    return _pixel_opts_for(mode, _theme_palette(scene_ir), slot_mode)


def _providers_available() -> bool:
    """快速判断是否配置了任一 AI 图像 key（避免为离线环境付初始化成本）"""
    try:
        from dotenv import load_dotenv

        load_dotenv(os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))),
            ".env",
        ))
    except Exception:  # noqa: BLE001
        pass
    return bool(os.getenv("STEP_API_KEY") or os.getenv("SENSENOVA_API_KEY"))


def _smart_prompt(scene_ir: Any, role: str, fallback: str, entity_name: str) -> str:
    """按游戏内容生成素材描述（实体名/品类/主题 → 一句英文生图提示）。

    LLM 不可用/超时 → 原样返回固定兜底模板。风格词由生图漏斗统一追加，这里不写。
    """
    if os.getenv("GAMEFORGE_SMART_PROMPTS", "1").strip().lower() in {"0", "false", "no"}:
        return fallback
    try:
        from src.utils.llm_client import get_llm_client

        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
        import yaml

        cfg_path = os.path.join(repo_root, "config", "config.yaml")
        llm_config = {}
        if os.path.isfile(cfg_path):
            with open(cfg_path, "r", encoding="utf-8") as f:
                llm_config = (yaml.safe_load(f) or {})
        client = get_llm_client(llm_config)
        genre = getattr(scene_ir, "genre", "platformer")
        theme = getattr(scene_ir, "theme", "") or "cozy"
        title = ""
        ctx = getattr(scene_ir, "project_context", None) or {}
        title = ctx.get("project_name", "") if isinstance(ctx, dict) else ""
        user = (
            f'Game: "{title or "untitled"}" ({genre}, {theme} mood). '
            f'Give ONE short English image prompt (max 16 words, no style keywords, no quotes) '
            f'describing the {role} named "{entity_name}" as a game visual. Output only the prompt.'
        )
        reply = client.chat_sync(
            messages=[{"role": "user", "content": user}],
            max_tokens=512,
            temperature=0.7,
        )
        text = (reply or "").strip().strip('"').strip()
        if text and 8 < len(text) < 220 and "\n" not in text:
            logger.info("asset_forge.smart_prompt", role=role, prompt=text[:80])
            return text
    except Exception as e:  # noqa: BLE001
        logger.warning("asset_forge.smart_prompt_failed", role=role, error=str(e))
    return fallback


# P4：敌人变体的 i2i 提示词（同一剪影换色，走重新生成而非 PIL 色相偏移）。
# 键即落盘文件名（enemy.png → enemy2/enemy3.png，与 _hue_variants 命名一致）。
_VARIANT_PROMPTS: Dict[str, str] = {
    "enemy2": "the same enemy creature in a crimson red color scheme, identical silhouette, pose and size",
    "enemy3": "the same enemy creature in a teal blue color scheme, identical silhouette, pose and size",
}

# P4：变体剪影保真下限。实测同一参考图两次生成的 IoU 约 0.48~0.54
# （模型大致保住剪影但不精确），0.3 以下基本是"另画了一个"——放弃 i2i 走色相保底。
_VARIANT_MIN_IOU = 0.3


def _i2i_prompt(content: str, *, same_subject: bool = False) -> str:
    """P3：把内容描述包装成带参考图的编辑指令。

    anchor 模式（npc 等新资产）：参考图只贡献视觉语言（调色板/描边/光位/像素密度），
    产出必须是不同主体——否则 npc 会变成"玩家换色"，正是要消灭的拼凑感。
    edit 模式（敌人变体）：要求完整保留剪影/姿态，只应用换色描述。
    """
    if same_subject:
        return (
            "Edit the reference image: keep its silhouette, pose, outline weight, "
            "lighting direction and pixel density exactly, and apply this change: "
            f"{content}."
        )
    return (
        "Using the reference image as the art-style anchor (same color palette, "
        "outline weight, lighting direction and pixel density), create a NEW game "
        f"asset of a different subject: {content}. Do not copy the reference character."
    )


def _hue_variants(src_png: str, count: int, palette: Optional[list] = None) -> None:
    """为敌人等同类实体生成色相偏移变体（PIL 零成本，丰富视觉而不多扣生图调用）。

    P4 起降级为 i2i 变体的保底路径：i2i 不可用/全败时仍保证"视觉不重样"的下限。
    色相偏移会把颜色推离原有调色板，因此变体落盘前重新量化回同一个 palette，
    否则"同一主题 3 个变体各自一套色"会重新引入色彩割裂。
    """
    try:
        from PIL import Image
        import colorsys

        img = Image.open(src_png).convert("RGBA")
        base = img.split()[3]
        hsv = img.convert("RGB").convert("HSV")
        for i in range(1, count + 1):
            shifted = hsv.copy()
            # hue 通道整体偏移（Hue 0-255 环）
            hue = shifted.split()[0].point(lambda v: (v + int(255 * i / (count + 1))) % 256)
            shifted = Image.merge("HSV", (hue, shifted.split()[1], shifted.split()[2])).convert("RGB")
            out = shifted.convert("RGBA")
            out.putalpha(base)
            if palette:
                from src.image.procedural.color_quantizer import quantize_to_palette

                out = quantize_to_palette(out, palette)
            out_path = src_png.replace(".png", f"{i + 1}.png")
            out.save(out_path)
    except Exception as e:  # noqa: BLE001
        logger.warning("asset_forge.hue_variant_failed", error=str(e))


def _variant_alpha_guard(img: Any, ref_path: str) -> Tuple[Any, float]:
    """P4 变体质量守门：把 i2i 变体的 alpha 与参考图对齐，返回 (图, 剪影IoU)。

    两件事：
    1. 变体残留不透明底色（remove_background 因四角不一致保守跳过时）→
       用参考图 alpha 掩膜清底，保证精灵不会带灰底 box 进游戏。
    2. IoU 供调用方判断模型是否保住剪影：低于 _VARIANT_MIN_IOU 视为漂移，
       应放弃该 i2i 结果，回退色相偏移（保底路径）。
       注意掩膜清底后的 alpha 恒等于参考剪影，IoU 随之退化为 ~1——
       那种情况下"剪影一致"由掩膜强制保证，无需再验证。
    守门自身失败不阻塞（返回 IoU=1.0 当作通过）——它是增强项不是门槛。
    """
    import numpy as np
    from PIL import Image

    try:
        ref = Image.open(ref_path).convert("RGBA").resize(img.size, Image.NEAREST)
        ref_mask = np.array(ref)[:, :, 3] > 128
        arr = np.array(img.convert("RGBA"))
        cur = arr[:, :, 3] > 128
        if cur.mean() > 0.9:
            # 抠底失败（灰底 box）→ 掩膜清底：只保留参考剪影内的像素
            arr[:, :, 3] = np.where(ref_mask, arr[:, :, 3], 0)
            img = Image.fromarray(arr, "RGBA")
            cur = ref_mask  # 清底后剪影即参考剪影
        union = int((ref_mask | cur).sum())
        iou = int((ref_mask & cur).sum()) / union if union else 0.0
        return img, iou
    except Exception as e:  # noqa: BLE001
        logger.warning("asset_forge.variant_guard_failed", error=str(e))
        return img, 1.0


def _hue_variants_if_missing(project_path: str, scene_ir: Any) -> None:
    """enemy2/enemy3 缺失时用色相偏移补齐（P4 保底 / 开关关闭时的旧行为）。

    只在两个都缺时触发：_hue_variants 按 count 连续命名（count=1 只写 enemy2），
    缺一半时跑它会写错文件名；单个缺失留白可接受，不冒写错名的风险。
    """
    enemy_png = os.path.join(project_path, _gen_dirname, "enemy.png")
    if not os.path.isfile(enemy_png):
        return
    missing = [
        v for v in _VARIANT_PROMPTS
        if not os.path.isfile(os.path.join(project_path, _gen_dirname, f"{v}.png"))
    ]
    if len(missing) != len(_VARIANT_PROMPTS):
        return
    logger.info("asset_forge.variant_hue_fallback", missing=missing)
    opts = _pixel_options(scene_ir, _SPEC["enemy"].get("palette_mode"))
    _hue_variants(
        enemy_png, len(missing),
        palette=opts.get("palette") if _pixel_art_enabled() else None,
    )


def _remove_background(img: Any) -> Any:
    """边缘 flood-fill 去纯色底 → 透明。失败时原样返回（宁要带底不要崩）。"""
    try:
        from collections import deque

        img = img.convert("RGBA")
        w, h = img.size
        px = img.load()
        corners = [px[0, 0], px[w - 1, 0], px[0, h - 1], px[w - 1, h - 1]]
        # 四角颜色接近才认为是纯色底
        base = corners[0]
        if any(
            abs(c[0] - base[0]) + abs(c[1] - base[1]) + abs(c[2] - base[2]) > 60
            for c in corners[1:]
        ):
            return img

        def _close(c: Any) -> bool:
            return abs(c[0] - base[0]) + abs(c[1] - base[1]) + abs(c[2] - base[2]) <= 90

        visited = bytearray(w * h)
        queue = deque()
        for x in range(w):
            for y in (0, h - 1):
                queue.append((x, y))
        for y in range(h):
            for x in (0, w - 1):
                queue.append((x, y))
        while queue:
            x, y = queue.popleft()
            if not (0 <= x < w and 0 <= y < h) or visited[y * w + x]:
                continue
            visited[y * w + x] = 1
            c = px[x, y]
            if not _close(c):
                continue
            px[x, y] = (c[0], c[1], c[2], 0)
            queue.extend(((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)))
        return img
    except Exception as e:  # noqa: BLE001
        logger.warning("asset_forge.remove_background_failed", error=str(e))
        return img


def _generate_one(
    key: str,
    project_path: str,
    timeout: float,
    prompt: Optional[str] = None,
    pixel_ctx: Optional[Dict[str, Any]] = None,
    style_ctx: Optional[Dict[str, Any]] = None,
    reference_paths: Optional[List[str]] = None,
    out_key: str = "",
    same_subject: bool = False,
) -> Optional[str]:
    """生成单张素材，成功返回 res:// 路径。prompt 缺省用固定模板（可被智能提示词覆盖）。

    style_ctx: P1 风格漏斗上下文 {genre, palette_base, camera}，
    透传给生图客户端决定视角/调色短语（见 src/image/style.py）。
    reference_paths: P3 i2i 参考图（player 风格锚 / enemy 变体源）。
    带参考图时 prompt 外包编辑指令（_i2i_prompt）；i2i 失败自动退回
    无参考图重生成一次——锚是增强项，缺它不该丢整张素材。
    out_key: 落盘文件名（缺省同 key；变体用 enemy2/enemy3 复用 enemy 的 _SPEC）。
    same_subject: True 时用 edit 模式包 prompt（保留参考图主体，用于敌变体）。
    """
    spec = _SPEC[key]
    name = out_key or key
    out_path = os.path.join(project_path, _gen_dirname, f"{name}.png")
    if os.path.isfile(out_path):
        return "res://assets/gen/" + name + ".png"

    from src.image.ai_image_client import AIImageClient

    client = AIImageClient(
        output_dir=os.path.dirname(out_path),
        prefer_provider=os.getenv("IMAGE_PREFER_PROVIDER", "step"),
    )
    style = style_ctx or {}
    content = prompt or spec["prompt"]
    refs = [p for p in (reference_paths or []) if p and os.path.isfile(p)]

    def _call(with_refs: bool) -> Dict[str, Any]:
        return client.generate_image(
            prompt=_i2i_prompt(content, same_subject=same_subject) if with_refs else content,
            size=list(spec["size"]),
            genre=style.get("genre"),
            palette_base=style.get("palette_base"),
            camera=style.get("camera"),
            reference_image_paths=refs if with_refs else None,
        )

    result = _call(bool(refs))
    if refs and not (
        result.get("success")
        and (result.get("image_path") or result.get("png_path") or result.get("filepath"))
    ):
        # i2i 失败（provider 不支持/超时/限流）→ 退回文生图，锚缺失不应丢整张素材
        logger.warning("asset_forge.i2i_fallback_t2i", asset=name)
        result = _call(False)
    # AI 客户端返回的实际字段是 image_path（Step/SenseNova 会把图先落盘）
    src_path = result.get("image_path") or result.get("png_path") or result.get("filepath")
    if not result.get("success") or not src_path or not os.path.isfile(src_path):
        logger.warning("asset_forge.generate_failed", asset=name, result=str(result)[:200])
        return None

    opts: Dict[str, Any] = {}
    try:
        from PIL import Image

        img = Image.open(src_path)
        if spec["remove_background"]:
            img = _remove_background(img)

        # 归一化到落盘尺寸。AI 实际返回尺寸可能不同（如 Step 512 请求返回 1024），
        # 固定尺寸让 .tscn 里的 Sprite2D 缩放系数可以用常量。
        storage = tuple(spec["size"])
        if _pixel_art_enabled():
            # 像素画管线：降采样到原生像素 → 量化调色板 → 整数倍放大。
            # 这是消灭"百万色照片硬缩到 48px"噪点感的关键一步。
            from src.image.procedural.pixel_pipeline import pixelate, resolve_native

            ctx = pixel_ctx or {}
            opts = _pixel_opts_for(
                ctx.get("mode", "harmonize"),
                ctx.get("theme"),
                spec.get("palette_mode"),
                spec.get("colors"),
            )
            native, storage = resolve_native(tuple(spec.get("pixel", storage)), storage)
            img = pixelate(
                img,
                native=native,
                storage=storage,
                dither=bool(spec.get("dither")),
                **opts,
            )
        elif img.size != storage:
            img = img.resize(storage, Image.NEAREST)

        # P4 变体守门：同主体编辑（敌变体）必须与参考图剪影一致。
        # 残留灰底 → 掩膜清底；IoU 过低 → 判定模型漂移，放弃该结果走色相保底。
        if same_subject and refs:
            img, iou = _variant_alpha_guard(img, refs[0])
            if iou < _VARIANT_MIN_IOU:
                logger.warning("asset_forge.i2i_silhouette_drift", asset=name, iou=round(iou, 3))
                return None

        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        img.save(out_path)
    except Exception as e:  # noqa: BLE001
        logger.warning("asset_forge.postprocess_failed", asset=key, error=str(e))
        # 后处理失败：直接拷原图
        import shutil

        shutil.copyfile(src_path, out_path)
    finally:
        # AI 客户端把原图落在 assets/gen/（output_dir 指向项目内），
        # 归一化产物已写出，清掉原始文件避免被 Godot 一起导入
        try:
            if src_path and os.path.abspath(src_path) != os.path.abspath(out_path):
                os.remove(src_path)
        except OSError:
            pass
    # 变体（enemy2/enemy3）由 forge_assets 第二波 i2i 生成（P4），此处不再色相偏移
    return "res://assets/gen/" + name + ".png"


def forge_assets(
    scene_ir: Any,
    project_path: str,
    *,
    per_image_timeout: float = 60.0,
    budget_seconds: float = 140.0,
    art_prompts: Optional[Dict[str, str]] = None,
) -> Dict[str, str]:
    """为项目生成 AI 素材，返回 {asset_key: res://路径}；失败/关闭/无 key 时返回空 dict。

    P3 起分两波：第一波 8 槽位并行；player/enemy 落盘后第二波用 i2i 生成
    npc（player 风格锚）与敌人变体（enemy 参考图，P4 弃用色相偏移）。
    两波共享预算，第二波有 30s 保底时间片；预算默认 140s 即为此放宽。

    同一项目的并发调用（前端轮询重试会重复触发建场景）串行化：
    后到者拿到锁后直接命中文件缓存，不会重复扣 AI 调用。
    """
    if os.getenv("GAMEFORGE_ASSETS_ENABLED", "1").strip().lower() in {"0", "false", "no"}:
        return {}
    if not _providers_available():
        logger.info("asset_forge.skipped_no_provider")
        return {}

    import threading
    import time

    with _locks_guard:
        lock = _path_locks.setdefault(os.path.abspath(project_path), threading.Lock())
    with lock:
        # 提示词优先级（注释承诺已久，此处补上第一级）：
        #   美术指导书（主题驱动，art_director.plan_art，一次 LLM 调用出全部槽位，
        #               比逐槽 _smart_prompt 便宜 ~9 倍）
        #   > 实体名智能提示词（_smart_prompt）> 固定模板
        # plan_art 放在 provider 检查之后：无 AI key 时不白付 LLM 调用；
        # plan_art 内部已有兜底（缺槽回落母题模板），异常再退化为 None。
        if art_prompts is None:
            try:
                from src.agents.art_director import plan_art

                art_prompts = plan_art(scene_ir)
            except Exception as e:  # noqa: BLE001
                logger.warning("asset_forge.art_plan_failed", error=str(e))
                art_prompts = None

        prompts: Dict[str, str] = {}
        entities = list(getattr(scene_ir, "entities", []) or [])
        for key in _SPEC:
            base = (art_prompts or {}).get(key) or _SPEC[key]["prompt"]
            role_map = {"player": "player", "enemy": "enemy", "npc": "npc", "decoration": "decoration"}
            if art_prompts is None and key in role_map and entities:
                names = [e.name for e in entities if e.role == role_map[key]]
                if names:
                    prompts[key] = _smart_prompt(scene_ir, role_map[key], base, names[0])
                else:
                    prompts[key] = base
            else:
                prompts[key] = base

        # P1 风格漏斗上下文：相机/调色按场景解析一次，9 个槽位共用。
        # palette_base 与像素化量化（_theme_palette）同源同解析，
        # 保证生成端出图倾向与后处理量化处于同一色彩体系。
        camera = getattr(getattr(scene_ir, "camera", None), "mode", None)
        try:
            from src.agents.art_director import resolve_palette_base

            palette_base = resolve_palette_base(scene_ir)
        except Exception as e:  # noqa: BLE001
            logger.warning("asset_forge.palette_base_resolve_failed", error=str(e))
            palette_base = None
        style_ctx = {
            "genre": getattr(scene_ir, "genre", None),
            "palette_base": palette_base,
            "camera": camera,
        }

        deadline = time.time() + budget_seconds
        results: Dict[str, str] = {}
        # 主题参考色只解析一次，同一项目的 9 个槽位共用；
        # 各槽位可用 _SPEC[key]["palette_mode"] 覆写策略（如背景用 theme）。
        mode = os.getenv("GAMEFORGE_PIXEL_PALETTE", "harmonize")
        pixel_ctx = {
            "mode": mode,
            "theme": None
            if mode.strip().lower() in {"auto", "off", "0", "false", "no"}
            else _theme_palette(scene_ir),
        }
        logger.info("asset_forge.pixel_mode", mode=mode)

        # P3 两波生成：第一波除 npc 外全部槽位并行；player 落盘后作为风格锚，
        # 第二波用 i2i 生成 npc；enemy 落盘后用 i2i 生成敌人变体（P4）。
        # 开关：GAMEFORGE_I2I_ANCHOR / GAMEFORGE_I2I_VARIANTS（默认开；
        # 关闭 → 旧行为：npc 并入第一波文生图，变体走 _hue_variants 色相偏移）。
        i2i_anchor = os.getenv("GAMEFORGE_I2I_ANCHOR", "1").strip().lower() not in {"0", "false", "no"}
        i2i_variants = os.getenv("GAMEFORGE_I2I_VARIANTS", "1").strip().lower() not in {"0", "false", "no"}
        wave1_keys = [k for k in _SPEC if not (i2i_anchor and k == "npc")]
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = {
                key: pool.submit(
                    _generate_one, key, project_path, per_image_timeout,
                    prompts[key], pixel_ctx, style_ctx,
                )
                for key in wave1_keys
            }
            for key, fut in futures.items():
                remaining = deadline - time.time()
                if remaining <= 0:
                    logger.warning("asset_forge.budget_exhausted", pending=key)
                    break
                try:
                    path = fut.result(timeout=min(per_image_timeout, remaining))
                except FutureTimeout:
                    logger.warning("asset_forge.timeout", asset=key)
                    fut.cancel()
                    continue
                except Exception as e:  # noqa: BLE001
                    logger.warning("asset_forge.error", asset=key, error=str(e))
                    continue
                if path:
                    results[key] = path

        # ── 第二波：风格锚 i2i（P3/P4） ──
        player_png = os.path.join(project_path, _gen_dirname, "player.png")
        enemy_png = os.path.join(project_path, _gen_dirname, "enemy.png")
        wave2_jobs: List[tuple] = []  # (name, same_subject, args)
        if i2i_anchor and "npc" in prompts:
            npc_png = os.path.join(project_path, _gen_dirname, "npc.png")
            if os.path.isfile(npc_png):
                # 缓存命中（前端轮询重试 / 二次调用）→ 直接计入，不进第二波
                results["npc"] = "res://assets/gen/npc.png"
            else:
                # 锚缺失（player 失败/禁用）→ refs 为空，_generate_one 内部退化为文生图
                refs = [player_png] if os.path.isfile(player_png) else None
                wave2_jobs.append((
                    "npc", False,
                    ("npc", project_path, per_image_timeout, prompts["npc"], pixel_ctx, style_ctx, refs),
                ))
        if i2i_variants and os.path.isfile(enemy_png):
            for vkey, vprompt in _VARIANT_PROMPTS.items():
                if not os.path.isfile(os.path.join(project_path, _gen_dirname, f"{vkey}.png")):
                    wave2_jobs.append((
                        vkey, True,
                        ("enemy", project_path, per_image_timeout, vprompt, pixel_ctx,
                         style_ctx, [enemy_png], vkey),
                    ))
        if wave2_jobs:
            # 第二波保底时间片：第一波超预算时也留给锚定生成（npc/变体是观感关键）
            wave2_deadline = max(deadline, time.time() + 30.0)
            with ThreadPoolExecutor(max_workers=3) as pool:
                futs = [
                    (name, pool.submit(_generate_one, *args, same_subject=same))
                    for name, same, args in wave2_jobs
                ]
                for name, fut in futs:
                    remaining = wave2_deadline - time.time()
                    if remaining <= 0:
                        logger.warning("asset_forge.budget_exhausted", pending=name)
                        continue
                    try:
                        path = fut.result(timeout=min(per_image_timeout, remaining))
                    except FutureTimeout:
                        logger.warning("asset_forge.timeout", asset=name)
                        fut.cancel()
                        continue
                    except Exception as e:  # noqa: BLE001
                        logger.warning("asset_forge.error", asset=name, error=str(e))
                        continue
                    if path:
                        results[name] = path

        # P4 保底 / 开关关闭：变体缺失（i2i 全败或未启用）→ 色相偏移补齐。
        # i2i 成功时两个变体都在，这里是 no-op。
        _hue_variants_if_missing(project_path, scene_ir)

    # 敌人变体（enemy2/enemy3）落盘即计入返回，供场景轮换引用
    for vkey in ("enemy2", "enemy3"):
        if os.path.isfile(os.path.join(project_path, _gen_dirname, vkey + ".png")):
            results[vkey] = "res://assets/gen/" + vkey + ".png"

    # 角色序列帧：在 player.png 之上生成 idle/run/jump（AI strip 或程序化兜底）
    try:
        from src.engine.godot.character_anim import build_player_animations

        anim = build_player_animations(
            project_path,
            base_player_png=os.path.join(project_path, _gen_dirname, "player.png")
            if os.path.isfile(os.path.join(project_path, _gen_dirname, "player.png"))
            else None,
        )
        if anim.get("ok"):
            results["player_anim"] = "assets/gen/player_anim.json"
            results["player_frames"] = anim.get("source", "")
    except Exception as e:  # noqa: BLE001
        logger.warning("asset_forge.player_anim_failed", error=str(e))

    if results:
        logger.info("asset_forge.done", assets=sorted(results.keys()))
    return results
