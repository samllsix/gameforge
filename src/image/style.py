"""GameForge - 全局美术风格约束

所有图像生成（AI 与程序化）统一经过这里拼接风格约束，
保证产出的美术资源风格一致：类星露谷（Stardew Valley）的 2D 像素风。
"""

# 风格标记：apply_art_style 用它做幂等判断，避免重复拼接
_STYLE_MARKER = "Stardew"

GAMEFORGE_ART_STYLE = (
    "Stardew Valley inspired 2D pixel art style, "
    "cozy 16-bit retro farming-game aesthetic, top-down view, "
    "crisp clean pixel edges with no anti-aliasing blur, "
    "warm limited color palette, soft natural shading, "
    "game asset on plain solid background"
)


def apply_art_style(prompt: str) -> str:
    """把全局像素风约束拼接到图像 prompt 后。

    幂等：prompt 已含风格标记（或已声明像素风）时原样返回，
    因此在 AIImageClient 与 ImageMCPServer 两层同时调用也安全。
    """
    if not prompt:
        return prompt
    if _STYLE_MARKER in prompt or "像素" in prompt:
        return prompt
    return f"{prompt.rstrip()}, {GAMEFORGE_ART_STYLE}"
