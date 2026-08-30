"""全局美术风格约束测试：所有生图 prompt 必须带类星露谷 2D 像素风。"""
from src.image.style import GAMEFORGE_ART_STYLE, apply_art_style


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
