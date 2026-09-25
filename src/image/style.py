"""GameForge - 全局美术风格约束

所有图像生成（AI 与程序化）统一经过这里拼接风格约束，
保证产出的美术资源风格一致：类星露谷（Stardew Valley）的 2D 像素风。

P1（ART_QUALITY_PLAN.md）：视角 / 调色 / 主题形容词从"一条写死的全局串"
改为按调用方上下文参数化（apply_art_style 的 genre / palette_base / camera）：

- camera：相机视角由 scene_ir.camera.mode 决定。此前写死 top-down view，
  而主力品类 platformer 是横版侧视——top-down 会让角色呈 45° 俯视，
  与碰撞体/物理观感脱节（天花板 3）。
- palette_base：调色倾向由主题包决定，与后处理像素量化（pixel_pipeline）
  使用同一套 palette_base 解析。此前只有后处理端用它，生成端没用上，
  模型出图可能给出与主题冲突的配色，只能靠 harmonize 事后兜底（天花板 2）。
- genre：camera 缺省时按品类推断相机（platformer/runner → 侧视，
  shooter/tower_defense/rpg → top-down）。

保留的共性层：像素风底座（Stardew 标记 + 16-bit + 硬边无抗锯齿 +
纯色背景），保证同一项目的素材是"同一个游戏"；
cozy farming-game aesthetic 这类主题形容词不再全局写死
（它和 space_night / neon_city / graveyard 等主题包直接打架）。
"""

# 风格标记：apply_art_style 用它做幂等判断，避免重复拼接
_STYLE_MARKER = "Stardew"

# 共性层：所有素材共享的"像素风"底座（视角/调色已参数化，见 apply_art_style）
GAMEFORGE_ART_STYLE = (
    "Stardew Valley inspired 2D pixel art style, "
    "16-bit retro game aesthetic, "
    "crisp clean pixel edges with no anti-aliasing blur, "
    "soft natural shading, "
    "game asset on plain solid background"
)

# 相机视角 → prompt 短语（键与 SceneIR.CameraIR.mode 对齐，附常见别名）
_CAMERA_PHRASES: dict[str, str] = {
    "2d_side_view": "side view profile",
    "side": "side view profile",
    "side_view": "side view profile",
    "top_down": "top-down view",
    "isometric": "isometric view",
    "3d_third_person": "3d third-person view",
    "3d_first_person": "3d first-person view",
}

# 相机缺省值：与 CameraIR 默认一致（2d_side_view，主力品类 platformer 横版侧视）
_DEFAULT_CAMERA = "2d_side_view"

# 品类 → 相机模式（camera 参数缺省时的推断表；与 genre_specs 的镜头定位对齐）
_GENRE_CAMERA: dict[str, str] = {
    "platformer": "2d_side_view",
    "runner": "2d_side_view",
    "fighting": "2d_side_view",
    "shooter": "top_down",
    "tower_defense": "top_down",
    "rpg": "top_down",
    "farming_sim": "top_down",
}

# palette_base → prompt 短语（与 genre_fusion.THEME_PACKS / pixel_pipeline
# 的调色板同源；AI 出图端此前后置缺失，P1 补上）
_PALETTE_PHRASES: dict[str, str] = {
    "forest_green": "forest green dominated color palette",
    "space_black": "deep space black palette with sparse bright highlights",
    "neon_purple": "neon purple and cyan color palette",
    "lava_red": "lava red and ember orange color palette",
    "warm_beige": "warm beige and tan color palette",
    "sky_blue": "sky blue and white color palette",
    "default": "limited retro color palette",
}

# palette_base 缺省时的调色短语。旧行为的 "warm limited color palette" 去掉
# warm——它和深空/霓虹/墓地等主题打架，是全串最刺眼的一处调性矛盾。
_DEFAULT_PALETTE_PHRASE = "limited color palette"


def _resolve_camera_phrase(camera: str | None, genre: str | None) -> str:
    """视角短语：camera 显式指定优先，否则按品类推断，再不然用默认侧视。"""
    key = (camera or "").strip().lower()
    if not key and genre:
        key = _GENRE_CAMERA.get(genre.strip().lower(), "")
    if not key:
        key = _DEFAULT_CAMERA
    return _CAMERA_PHRASES.get(key, _CAMERA_PHRASES[_DEFAULT_CAMERA])


def build_art_style(
    genre: str | None = None,
    palette_base: str | None = None,
    camera: str | None = None,
) -> str:
    """装配完整风格串：像素风底座 + 视角短语 + 调色短语。

    三者顺序固定，保证同一项目的素材共享同一套风格描述。
    """
    parts = [
        GAMEFORGE_ART_STYLE,
        _resolve_camera_phrase(camera, genre),
        _PALETTE_PHRASES.get((palette_base or "").strip().lower(), "")
        or _DEFAULT_PALETTE_PHRASE,
    ]
    return ", ".join(parts)


def apply_art_style(
    prompt: str,
    *,
    genre: str | None = None,
    palette_base: str | None = None,
    camera: str | None = None,
) -> str:
    """把全局像素风约束拼接到图像 prompt 后。

    幂等：prompt 已含风格标记（或已声明像素风）时原样返回，
    因此在 AIImageClient 与 ImageMCPServer 两层同时调用也安全。

    Args:
        prompt: 原始图像描述（内容向，可含简要画法词，见 art_director P2）
        genre: 游戏品类（camera 缺省时推断视角）
        palette_base: 主题包调色板基色（forest_green / neon_purple / ...）
        camera: 相机模式（SceneIR.CameraIR.mode：2d_side_view / top_down / ...）
    """
    if not prompt:
        return prompt
    if _STYLE_MARKER in prompt or "像素" in prompt:
        return prompt
    return f"{prompt.rstrip()}, {build_art_style(genre, palette_base, camera)}"
