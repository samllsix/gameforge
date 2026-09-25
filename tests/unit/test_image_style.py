"""全局美术风格约束测试：所有生图 prompt 必须带类星露谷 2D 像素风。"""
from src.image.style import GAMEFORGE_ART_STYLE, apply_art_style, build_art_style


def test_style_appended_to_plain_prompt():
    out = apply_art_style("a cute chicken")
    assert out.startswith("a cute chicken")
    assert "Stardew" in out
    assert GAMEFORGE_ART_STYLE in out


def test_style_is_idempotent():
    once = apply_art_style("a cute chicken")
    twice = apply_art_style(once)
    assert once == twice


def test_style_skips_prompt_already_declaring_pixel_art():
    original = "一只像素小鸡，像素风"
    assert apply_art_style(original) == original


def test_style_handles_empty_prompt():
    assert apply_art_style("") == ""


# ── P1：视角 / 调色参数化 ────────────────────────────────────────────

def test_default_uses_side_view_not_top_down():
    """不再写死 top-down view：缺省视角是侧视（主力品类 platformer 横版）。"""
    out = apply_art_style("a cute chicken")
    assert "side view profile" in out
    assert "top-down" not in out


def test_camera_param_selects_view_phrase():
    out = apply_art_style("farm field", camera="top_down")
    assert "top-down view" in out
    out2 = apply_art_style("farm field", camera="2d_side_view")
    assert "side view profile" in out2


def test_genre_infers_camera_when_camera_missing():
    """camera 缺省时按品类推断：platformer 侧视、tower_defense 俯视。"""
    assert "side view profile" in apply_art_style("x", genre="platformer")
    assert "top-down view" in apply_art_style("x", genre="tower_defense")
    # 显式 camera 优先于 genre 推断
    assert "top-down view" in apply_art_style("x", genre="platformer", camera="top_down")


def test_palette_base_selects_color_phrase():
    out = apply_art_style("x", palette_base="space_black")
    assert "deep space black palette" in out
    # 旧全局串的 "warm limited color palette" 不再出现（和深空/霓虹主题打架）
    assert "warm" not in out
    out2 = apply_art_style("x", palette_base="neon_purple")
    assert "neon purple and cyan" in out2


def test_unknown_camera_and_palette_fall_back():
    out = apply_art_style("x", camera="weird_mode", palette_base="not_a_palette")
    assert "side view profile" in out
    assert "limited color palette" in out


def test_style_idempotent_with_params():
    once = apply_art_style("a cute chicken", genre="platformer", palette_base="farm_green", camera="top_down")
    twice = apply_art_style(once, genre="shooter", palette_base="space_black")
    assert once == twice


def test_build_art_style_orders_base_camera_palette():
    style = build_art_style(genre="shooter", palette_base="lava_red")
    assert style.startswith(GAMEFORGE_ART_STYLE)
    assert style.endswith("lava red and ember orange color palette")
    assert "top-down view" in style


def test_mcp_server_generate_image_applies_style(monkeypatch, tmp_path):
    """ImageMCPServer 生图漏斗必须拼接风格约束（强制走程序化兜底分支）"""
    from src.mcp.servers.image_server import ImageMCPServer
    server = ImageMCPServer(output_dir=str(tmp_path))
    # _load_env 会从 .env 重新加载 key；这里强制断开 AI，只测兜底路径
    server.ai_client = None
    server.ai_providers = []

    captured = {}

    def fake_generate_image(prompt, size=None, seed=None):
        captured["prompt"] = prompt
        return {"success": True, "prompt": prompt, "png_path": "x.png"}

    monkeypatch.setattr(server.generator, "generate_image", fake_generate_image)
    server.generate_image("a cute chicken", size=[64, 64])
    assert "Stardew" in captured["prompt"]


def test_mcp_server_passes_style_context(monkeypatch, tmp_path):
    """P1：MCP 层漏斗把 camera/palette_base 上下文带进风格串。"""
    from src.mcp.servers.image_server import ImageMCPServer
    server = ImageMCPServer(output_dir=str(tmp_path))
    server.ai_client = None
    server.ai_providers = []

    captured = {}

    def fake_generate_image(prompt, size=None, seed=None):
        captured["prompt"] = prompt
        return {"success": True, "prompt": prompt, "png_path": "x.png"}

    monkeypatch.setattr(server.generator, "generate_image", fake_generate_image)
    server.generate_image(
        "space corridor", size=[64, 64],
        genre="shooter", palette_base="space_black", camera="top_down",
    )
    assert "top-down view" in captured["prompt"]
    assert "deep space black palette" in captured["prompt"]
