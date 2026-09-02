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
from typing import Any, Dict, Optional

import structlog

logger = structlog.get_logger()

_gen_dirname = os.path.join("assets", "gen")

_path_locks: Dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()
_SPEC: Dict[str, Dict[str, Any]] = {
    "background": {
        "size": (1360, 768),
        "prompt": (
            "wide parallax game background for a cozy platformer, "
            "rolling hills, barn and windmill on the horizon, fluffy clouds, "
            "no characters, no text, no UI"
        ),
        "remove_background": False,
    },
    "player": {
        "size": (512, 512),
        "prompt": (
            "single player character game sprite, cute young farmer adventurer, "
            "full body, front view, standing pose, centered in frame"
        ),
        "remove_background": True,
    },
    "enemy": {
        "size": (512, 512),
        "prompt": (
            "single enemy creature game sprite, grumpy purple slime monster, "
            "full body, front view, centered in frame"
        ),
        "remove_background": True,
    },
    "pickup": {
        "size": (512, 512),
        "prompt": (
            "single collectible coin game sprite, golden star coin, "
            "slightly glowing, centered in frame"
        ),
        "remove_background": True,
    },
    "icon": {
        "size": (512, 512),
        "prompt": (
            "square game app icon, cute mascot badge emblem, bold readable silhouette, "
            "centered composition with margin, no text"
        ),
        "remove_background": False,
    },
    "ground": {
        "size": (512, 512),
        "prompt": (
            "seamless tileable pixel art grass ground texture, top-down view, "
            "uniform lighting, no borders, no objects"
        ),
        "remove_background": False,
    },
    "platform": {
        "size": (512, 512),
        "prompt": (
            "seamless tileable pixel art wooden plank platform texture, "
            "uniform lighting, no borders"
        ),
        "remove_background": False,
    },
    "decoration": {
        "size": (512, 512),
        "prompt": (
            "single lush green leafy tree game sprite, pixel art, "
            "full tree, centered in frame"
        ),
        "remove_background": True,
    },
    "npc": {
        "size": (512, 512),
        "prompt": (
            "single friendly villager NPC game sprite, shopkeeper with apron, "
            "full body, front view, centered in frame"
        ),
        "remove_background": True,
    },
}


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


def _hue_variants(src_png: str, count: int) -> None:
    """为敌人等同类实体生成色相偏移变体（PIL 零成本，丰富视觉而不多扣生图调用）。"""
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
            out_path = src_png.replace(".png", f"{i + 1}.png")
            out.save(out_path)
    except Exception as e:  # noqa: BLE001
        logger.warning("asset_forge.hue_variant_failed", error=str(e))


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
) -> Optional[str]:
    """生成单张素材，成功返回 res:// 路径。prompt 缺省用固定模板（可被智能提示词覆盖）。"""
    spec = _SPEC[key]
    out_path = os.path.join(project_path, _gen_dirname, f"{key}.png")
    if os.path.isfile(out_path):
        return "res://assets/gen/" + key + ".png"

    from src.image.ai_image_client import AIImageClient

    client = AIImageClient(
        output_dir=os.path.dirname(out_path),
        prefer_provider=os.getenv("IMAGE_PREFER_PROVIDER", "step"),
    )
    result = client.generate_image(prompt=prompt or spec["prompt"], size=list(spec["size"]))
    # AI 客户端返回的实际字段是 image_path（Step/SenseNova 会把图先落盘）
    src_path = result.get("image_path") or result.get("png_path") or result.get("filepath")
    if not result.get("success") or not src_path or not os.path.isfile(src_path):
        logger.warning("asset_forge.generate_failed", asset=key, result=str(result)[:200])
        return None

    try:
        from PIL import Image

        img = Image.open(src_path)
        if spec["remove_background"]:
            img = _remove_background(img)
        # 归一化到请求尺寸：API 实际返回尺寸可能不同（如 Step 512 请求返回 1024），
        # 固定尺寸让 .tscn 里的 Sprite2D 缩放系数可以用常量
        target = spec["size"]
        if img.size != tuple(target):
            img = img.resize(tuple(target), Image.NEAREST)
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
    # 同类多实体变体：敌人色相偏移出 2 个变体（Enemy2/Enemy3 视觉不重样，零额外调用）
    if key == "enemy" and os.path.isfile(out_path):
        _hue_variants(out_path, 2)
    return "res://assets/gen/" + key + ".png"


def forge_assets(
    scene_ir: Any,
    project_path: str,
    *,
    per_image_timeout: float = 60.0,
    budget_seconds: float = 100.0,
    art_prompts: Optional[Dict[str, str]] = None,
) -> Dict[str, str]:
    """为项目生成 AI 素材，返回 {asset_key: res://路径}；失败/关闭/无 key 时返回空 dict。

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
        # 提示词优先级：美术指导书（主题驱动，art_director.plan_art）
        #            > 实体名智能提示词 > 固定模板
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

        deadline = time.time() + budget_seconds
        results: Dict[str, str] = {}
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = {
                key: pool.submit(_generate_one, key, project_path, per_image_timeout, prompts[key])
                for key in _SPEC
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

    # 色相变体（enemy2/enemy3）落盘即计入返回，供场景轮换引用
    for vkey in ("enemy2", "enemy3"):
        if os.path.isfile(os.path.join(project_path, _gen_dirname, vkey + ".png")):
            results[vkey] = "res://assets/gen/" + vkey + ".png"

    if results:
        logger.info("asset_forge.done", assets=sorted(results.keys()))
    return results
