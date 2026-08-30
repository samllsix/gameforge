"""GameForge - Image MCP Server

支持两种图像生成模式：
1. AI 生成：Step Image Edit 2 / SenseNova U1.5 Lite
2. 程序化生成：ProceduralSpriteGenerator（兜底）

Tool 列表：
- generate_image(prompt, size, provider) -> {png_path, ...}
- generate_sprite_sheet(character) -> {png_path, sheet_size, ...}
- generate_tileset(theme, tile_size, grid) -> {png_path, adjacency, ...}
- generate_animation(frames, base_sprite) -> {png_path, frames, ...}

降级链：
- generate_image  ：AI API → procedural_concept
- sprite/tileset  ：程序化算法
- animation       ：aseprite → passthrough (P2P 单帧)

MCP SDK 依赖：
- 如未装 mcp 包，本文件仍可作为命令行工具直接运行（见 __main__）
- 装上 mcp 包后可作为 stdio MCP server 启动
"""
from __future__ import annotations
import argparse
import asyncio
import json
import sys
from typing import Any, Dict, List, Optional

# 把仓库根加到 path，让 src.* 导入能跑（独立运行时）
# 优先级：当前 cwd → 脚本所在位置（兼容 -m 模式）
import os
def _find_repo_root() -> str:
    # 1. 当前 cwd 里有 src/ 标记
    cwd = os.getcwd()
    if os.path.isdir(os.path.join(cwd, "src", "image")):
        return cwd
    # 2. 脚本所在位置往上找 src/image 标记
    here = os.path.dirname(os.path.abspath(__file__))
    cur = here
    for _ in range(6):
        if os.path.isdir(os.path.join(cur, "src", "image")):
            return cur
        cur = os.path.dirname(cur)
    return cwd  # 兜底

_repo_root = _find_repo_root()
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from src.image.procedural.procedural_sprite_generator import ProceduralSpriteGenerator


class ImageMCPServer:
    """Image MCP 服务端（AI + 程序化双引擎）

    本类既可作为 MCP server 跑 stdio，
    也可作为普通 Python 类直接调 generate_* 方法。
    """

    def __init__(
        self,
        output_dir: str = r"D:\comfyui\output",
        step_api_key: Optional[str] = None,
        step_base_url: str = "https://api.stepfun.com/step_plan/v1",
        sensenova_api_key: Optional[str] = None,
        sensenova_base_url: str = "https://token.sensenova.cn/v1",
        prefer_provider: str = "step",
    ):
        self.output_dir = output_dir
        self.generator = ProceduralSpriteGenerator(output_dir=output_dir)

        # 加载 .env 文件
        self._load_env()
        
        # 从环境变量读取 API 配置
        if step_api_key is None:
            step_api_key = os.getenv("STEP_API_KEY", "")
        if sensenova_api_key is None:
            sensenova_api_key = os.getenv("SENSENOVA_API_KEY", "")
        if step_base_url == "https://api.stepfun.com/step_plan/v1":
            step_base_url = os.getenv("STEP_BASE_URL", step_base_url)
        if sensenova_base_url == "https://token.sensenova.cn/v1":
            sensenova_base_url = os.getenv("SENSENOVA_BASE_URL", sensenova_base_url)
        prefer_provider = os.getenv("IMAGE_PREFER_PROVIDER", prefer_provider)

        # 初始化 AI 图像客户端
        try:
            from src.image.ai_image_client import AIImageClient
            self.ai_client = AIImageClient(
                output_dir=output_dir,
                step_api_key=step_api_key or None,
                step_base_url=step_base_url,
                sensenova_api_key=sensenova_api_key or None,
                sensenova_base_url=sensenova_base_url,
                prefer_provider=prefer_provider,
            )
            self.ai_providers = self.ai_client.get_available_providers()
            if self.ai_providers:
                print(f"[image-mcp] AI providers loaded: {self.ai_providers}", file=sys.stderr)
        except Exception as e:
            print(f"[image-mcp] AI client init failed: {e}", file=sys.stderr)
            self.ai_client = None
            self.ai_providers = []
    
    def _load_env(self):
        """加载 .env 文件"""
        try:
            from dotenv import load_dotenv
            env_path = os.path.join(_repo_root, ".env")
            if os.path.exists(env_path):
                load_dotenv(env_path)
        except ImportError:
            # dotenv 未安装，手动加载
            env_path = os.path.join(_repo_root, ".env")
            if os.path.exists(env_path):
                with open(env_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            key, value = line.split("=", 1)
                            os.environ.setdefault(key.strip(), value.strip())

    # ─── 4 个 tool 方法（同步，避免 asyncio 包装问题）────────

    def generate_image(
        self,
        prompt: str,
        size: Optional[List[int]] = None,
        seed: Optional[int] = None,
        provider: Optional[str] = None,
    ) -> Dict[str, Any]:
        """生成图像（AI 优先，程序化兜底）

        Args:
            prompt: 图像描述
            size: [width, height]
            seed: 随机种子
            provider: 指定提供者（step 或 sensenova），None 表示自动选择
        """
        # 全局美术风格约束（幂等，AIImageClient 内部还会再拼一次也不会重复）：
        # 所有生图统一为类星露谷 2D 像素风
        from src.image.style import apply_art_style
        prompt = apply_art_style(prompt)

        # 优先使用 AI API
        if self.ai_client is not None:
            try:
                result = self.ai_client.generate_image(
                    prompt=prompt,
                    size=size,
                    seed=seed,
                    provider=provider,
                )
                if result.get("success"):
                    return result
            except Exception as e:
                print(f"[image-mcp] AI generation failed: {e}", file=sys.stderr)

        # 降级到程序化生成
        return self.generator.generate_image(prompt=prompt, size=size, seed=seed)

    def generate_sprite_sheet(
        self,
        character: Optional[Dict[str, Any]] = None,
        template: str = "humanoid",
        palette: Optional[List[str]] = None,
        pixel_size: int = 4,
        frames: int = 8,
        actions: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        return self.generator.generate_sprite_sheet(
            character=character,
            template=template,
            palette=palette,
            pixel_size=pixel_size,
            frames=frames,
            actions=actions,
        )

    def generate_tileset(
        self,
        theme: str = "forest",
        tile_size: int = 16,
        grid: Optional[List[int]] = None,
        seed: Optional[int] = None,
    ) -> Dict[str, Any]:
        return self.generator.generate_tileset(
            theme=theme,
            tile_size=tile_size,
            grid=grid,
            seed=seed,
        )

    def generate_animation(
        self,
        frames: int = 24,
        base_sprite: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        return self.generator.generate_animation(
            frames=frames,
            base_sprite=base_sprite,
        )

    # ─── MCP tool 注册（MCP SDK 可选）────────────────────────

    def tool_schemas(self) -> List[Dict[str, Any]]:
        """返回 MCP tool 描述（按 MCP 协议）"""
        return [
            {
                "name": "generate_image",
                "description": "生成图像（固定类星露谷 2D 像素风；AI 优先：Step Image Edit 2 / SenseNova U1.5 Lite，程序化兜底）",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "prompt": {"type": "string", "description": "图像描述"},
                        "size": {"type": "array", "items": {"type": "integer"}, "description": "[w, h]"},
                        "seed": {"type": "integer", "description": "随机种子（None 用 prompt 派生）"},
                        "provider": {
                            "type": "string",
                            "description": "AI 提供者（step 或 sensenova），None 表示自动选择",
                            "enum": ["step", "sensenova"],
                        },
                    },
                    "required": ["prompt"],
                },
            },
            {
                "name": "generate_sprite_sheet",
                "description": "生成角色 sprite sheet（多动作 × 多帧拼接）",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "character": {
                            "type": "object",
                            "properties": {
                                "template": {"type": "string", "enum": ["humanoid", "slime"]},
                                "palette": {"type": "array", "items": {"type": "string"}},
                                "pixel_size": {"type": "integer"},
                                "frames": {"type": "integer"},
                                "actions": {"type": "array", "items": {"type": "string"}},
                            },
                        },
                    },
                },
            },
            {
                "name": "generate_tileset",
                "description": "生成瓦片集（程序化 WFC 算法）",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "theme": {"type": "string", "enum": ["forest", "dungeon", "beach", "snow", "desert", "default"]},
                        "tile_size": {"type": "integer", "default": 16},
                        "grid": {"type": "array", "items": {"type": "integer"}, "description": "[cols, rows]"},
                        "seed": {"type": "integer"},
                    },
                },
            },
            {
                "name": "generate_animation",
                "description": "生成动画帧序列（优先 Aseprite，无则 passthrough）",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "frames": {"type": "integer", "default": 24},
                        "base_sprite": {"type": "object", "description": "基础精灵参数"},
                    },
                },
            },
        ]

    async def handle_mcp_request(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """简单的 MCP 请求路由（避免 mcp SDK 依赖）"""
        method = request.get("method", "")
        if method == "tools/list":
            return {"tools": self.tool_schemas()}
        if method == "tools/call":
            params = request.get("params", {})
            tool_name = params.get("name", "")
            args = params.get("arguments", {})
            if not hasattr(self, tool_name):
                return {"error": f"unknown tool: {tool_name}"}
            method_fn = getattr(self, tool_name)
            result = method_fn(**args)
            return {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=2)}]}
        return {"error": f"unknown method: {method}"}


# ─── 命令行入口（无需 MCP SDK）────────────────────────────────

def _main():
    parser = argparse.ArgumentParser(description="Image MCP Server (AI + procedural)")
    parser.add_argument("--output-dir", default=r"D:\comfyui\output",
                        help="输出目录（默认 D:\\comfyui\\output）")
    parser.add_argument("--stdio", action="store_true",
                        help="以 stdio MCP 模式启动（需要 mcp SDK）")

    # AI API 配置
    parser.add_argument("--step-api-key", default=os.getenv("STEP_API_KEY", ""),
                        help="Step API 密钥（或设置 STEP_API_KEY 环境变量）")
    parser.add_argument("--step-base-url", default=os.getenv("STEP_BASE_URL", "https://api.stepfun.com/step_plan/v1"),
                        help="Step API 基础 URL")
    parser.add_argument("--sensenova-api-key", default=os.getenv("SENSENOVA_API_KEY", ""),
                        help="SenseNova API 密钥（或设置 SENSENOVA_API_KEY 环境变量）")
    parser.add_argument("--sensenova-base-url", default=os.getenv("SENSENOVA_BASE_URL", "https://token.sensenova.cn/v1"),
                        help="SenseNova API 基础 URL")
    parser.add_argument("--prefer-provider", default="step",
                        choices=["step", "sensenova"],
                        help="优先使用的 AI 提供者")

    sub = parser.add_subparsers(dest="cmd")

    p_img = sub.add_parser("image", help="生成概念图")
    p_img.add_argument("prompt")
    p_img.add_argument("--size", type=int, nargs=2, default=[512, 512])
    p_img.add_argument("--provider", choices=["step", "sensenova"],
                       help="指定 AI 提供者")

    p_sprite = sub.add_parser("sprite", help="生成精灵表")
    p_sprite.add_argument("--template", default="humanoid")
    p_sprite.add_argument("--frames", type=int, default=8)
    p_sprite.add_argument("--pixel-size", type=int, default=4)

    p_tile = sub.add_parser("tileset", help="生成瓦片集")
    p_tile.add_argument("--theme", default="forest")
    p_tile.add_argument("--tile-size", type=int, default=16)
    p_tile.add_argument("--cols", type=int, default=8)
    p_tile.add_argument("--rows", type=int, default=8)

    p_anim = sub.add_parser("anim", help="生成动画")
    p_anim.add_argument("--frames", type=int, default=24)

    args = parser.parse_args()
    server = ImageMCPServer(
        output_dir=args.output_dir,
        step_api_key=args.step_api_key or None,
        step_base_url=args.step_base_url,
        sensenova_api_key=args.sensenova_api_key or None,
        sensenova_base_url=args.sensenova_base_url,
        prefer_provider=args.prefer_provider,
    )

    if args.stdio:
        _run_stdio(server)
        return

    if args.cmd == "image":
        r = server.generate_image(
            args.prompt, size=args.size, provider=args.provider
        )
    elif args.cmd == "sprite":
        r = server.generate_sprite_sheet(
            template=args.template, frames=args.frames, pixel_size=args.pixel_size,
        )
    elif args.cmd == "tileset":
        r = server.generate_tileset(
            theme=args.theme, tile_size=args.tile_size, grid=[args.cols, args.rows],
        )
    elif args.cmd == "anim":
        r = server.generate_animation(frames=args.frames)
    else:
        parser.print_help()
        return

    print(json.dumps(r, ensure_ascii=False, indent=2))


def _run_stdio(server: ImageMCPServer):
    """以 stdio JSON-RPC 风格跑（MCP 协议简化版）"""
    print("[image-mcp] ready (procedural engine, stdio mode)", file=sys.stderr)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            print(json.dumps({"error": "invalid json"}))
            continue
        # 同步跑 async 处理器（这里没有真异步）
        resp = asyncio.run(server.handle_mcp_request(req))
        print(json.dumps(resp, ensure_ascii=False))
        sys.stdout.flush()


if __name__ == "__main__":
    _main()