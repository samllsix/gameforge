"""AIImageClient 测试：风格漏斗上下文透传（P1）与 i2i 参考图（P3）。

历史教训：P1 曾因编辑未落盘导致 genre/palette_base/camera 被 **kwargs 吞掉、
风格上下文静默丢失——这些测试就是守这条线的。
"""
import asyncio
import base64
import sys
import types

from src.image.ai_image_client import AIImageClient, ImageResult, StepImageProvider


def _make_client(tmp_path):
    """构造一个只有 step provider、无程序化兜底的客户端（provider 用替身）。"""
    client = AIImageClient.__new__(AIImageClient)
    client.output_dir = str(tmp_path)
    client.providers = {"step": _FakeProvider()}
    client.prefer_provider = "step"
    client.fallback_to_procedural = False
    client._procedural = None
    return client


class _FakeProvider:
    """捕获 prompt/kwargs 的异步 provider 替身。"""

    def __init__(self):
        self.calls = []

    async def generate_image(self, prompt, size=(512, 512), seed=None, **kwargs):
        self.calls.append({"prompt": prompt, "size": size, "seed": seed, **kwargs})
        return ImageResult(
            success=True,
            image_path=None,  # 触发 i2i→t2i 回退判断的位置由测试自行控制
            prompt=prompt,
            provider="fake",
        )


def test_style_context_reaches_funnel(tmp_path, monkeypatch):
    """P1：genre/palette_base/camera 必须进入风格串（曾因编辑丢失被 kwargs 吞掉）。"""
    client = _make_client(tmp_path)
    client.generate_image("a farmer sprite", genre="platformer", palette_base="forest_green", camera="2d_side_view")
    prompt = client.providers["step"].calls[0]["prompt"]
    assert "side view profile" in prompt
    assert "forest green dominated color palette" in prompt
    assert "Stardew" in prompt


def test_style_context_defaults_to_side_view(tmp_path, monkeypatch):
    """缺省不再拼 top-down view（横版主力品类的错视角）。"""
    client = _make_client(tmp_path)
    client.generate_image("a coin sprite")
    prompt = client.providers["step"].calls[0]["prompt"]
    assert "side view profile" in prompt
    assert "top-down" not in prompt


def test_reference_image_paths_become_image_urls(tmp_path, monkeypatch):
    """P3：本地参考图 → data URI，经 image_urls kwarg 透传给 provider。"""
    from PIL import Image

    ref = tmp_path / "player.png"
    Image.new("RGB", (32, 32), (10, 120, 40)).save(ref)
    client = _make_client(tmp_path)
    client.generate_image("villager NPC sprite", reference_image_paths=[str(ref)])
    call = client.providers["step"].calls[0]
    uris = call.get("image_urls")
    assert uris and uris[0].startswith("data:image/png;base64,")
    # data URI 内容应与原文件一致
    assert base64.b64decode(uris[0].split(",", 1)[1]) == ref.read_bytes()


def test_reference_paths_missing_files_skipped(tmp_path, monkeypatch):
    """参考图缺失/不可读 → 不传 image_urls（退化为文生图，不报错）。"""
    client = _make_client(tmp_path)
    client.generate_image(
        "villager NPC sprite",
        reference_image_paths=[str(tmp_path / "nope.png"), str(tmp_path)],
    )
    assert "image_urls" not in client.providers["step"].calls[0]


def test_reference_data_uris_unit(tmp_path):
    """_reference_data_uris：空列表 / 坏路径 / 空文件都不产出 URI。"""
    client = AIImageClient.__new__(AIImageClient)
    assert client._reference_data_uris(None) == []
    assert client._reference_data_uris([]) == []
    assert client._reference_data_uris([str(tmp_path / "missing.png")]) == []
    empty = tmp_path / "empty.png"
    empty.write_bytes(b"")
    assert client._reference_data_uris([str(empty)]) == []


def test_step_provider_payload_includes_image_urls(monkeypatch, tmp_path):
    """Step provider：image_urls kwarg 必须进入请求 payload（i2i 契约）。"""
    captured = {}

    class _FakeResponse:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            import io

            from PIL import Image

            buf = io.BytesIO()
            Image.new("RGB", (8, 8), (1, 2, 3)).save(buf, format="PNG")
            return {"data": [{"b64_json": base64.b64encode(buf.getvalue()).decode()}]}

    class _FakeAsyncClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers=None, json=None):
            captured["url"] = url
            captured["payload"] = json
            return _FakeResponse()

    fake_httpx = types.SimpleNamespace(AsyncClient=_FakeAsyncClient, HTTPStatusError=Exception)
    monkeypatch.setitem(sys.modules, "httpx", fake_httpx)

    provider = StepImageProvider(api_key="k", base_url="https://example.invalid/v1")
    result = asyncio.run(provider.generate_image(
        prompt="recolor", size=(512, 512), output_dir=str(tmp_path),
        image_urls=["data:image/png;base64,AAAA"],
    ))
    assert result.success
    assert captured["payload"]["image_urls"] == ["data:image/png;base64,AAAA"]
    assert captured["payload"]["model"] == "step-image-edit-2"
