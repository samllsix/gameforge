"""GameForge - Godot HTTP 客户端

与运行中的 Godot 编辑器内嵌 HTTP 服务器通信。
用于实时导入代码、触发编译、构建场景等操作。
"""

import asyncio
import json
import os
from typing import Any, Dict, List, Optional

import structlog

logger = structlog.get_logger()

try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False


class GodotHTTPClient:
    """Godot 编辑器 HTTP 客户端

    与 Godot 编辑器内嵌的 GameForge HTTP 插件通信。
    默认端口: 8765
    """

    def __init__(
        self,
        base_url: str = "http://localhost:8765",
        timeout: float = 30.0,
        api_token: Optional[str] = None,
    ):
        if not HAS_HTTPX:
            raise ImportError("需要安装 httpx: pip install httpx")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.api_token = api_token if api_token is not None else os.getenv("GAMEFORGE_GODOT_HTTP_TOKEN", "")
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> "httpx.AsyncClient":
        if self._client is None or self._client.is_closed:
            headers = {"X-API-Key": self.api_token} if self.api_token else None
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=httpx.Timeout(self.timeout),
                headers=headers,
            )
        return self._client

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def check_health(self) -> bool:
        """检查 Godot 编辑器 HTTP 服务是否可用

        使用较短的连接超时（2s 连接 / 3s 总超时），避免 Godot 未运行且端口被
        静默丢弃时，健康检查一直挂到客户端全局超时（默认 60s）才返回。
        """
        try:
            client = await self._get_client()
            resp = await client.get(
                "/api/health",
                timeout=httpx.Timeout(3.0, connect=2.0),
            )
            return resp.status_code == 200
        except Exception:
            return False

    async def get_server_info(self) -> Dict[str, Any]:
        """获取 Godot 编辑器信息"""
        try:
            client = await self._get_client()
            resp = await client.get(
                "/api/health",
                timeout=httpx.Timeout(3.0, connect=2.0),
            )
            return resp.json() if resp.status_code == 200 else {}
        except Exception as e:
            return {"error": str(e)}

    async def import_files(self, files: Dict[str, str]) -> Dict[str, Any]:
        """导入文件到 Godot 项目

        Args:
            files: 文件字典 {相对路径: 内容}

        Returns:
            导入结果
        """
        try:
            client = await self._get_client()
            resp = await client.post("/api/import", json={"files": files})
            return resp.json()
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def compile_scripts(self, max_retries: int = 20, retry_delay: float = 2.0) -> Dict[str, Any]:
        """触发 Godot 脚本编译并等待结果

        Args:
            max_retries: 最大重试次数
            retry_delay: 重试间隔（秒）

        Returns:
            编译结果
        """
        try:
            client = await self._get_client()

            # 触发编译
            resp = await client.post("/api/compile")
            if resp.status_code != 200:
                return {"status": "error", "error": f"编译请求失败: {resp.status_code}"}

            result = resp.json()

            # 如果编译还在进行中，轮询等待
            for _ in range(max_retries):
                status = result.get("status", "")
                if status not in ("pending", "compiling"):
                    return result
                await asyncio.sleep(retry_delay)
                resp = await client.get("/api/compile/status")
                if resp.status_code == 200:
                    result = resp.json()

            return result
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def get_compile_errors(self) -> List[Dict[str, Any]]:
        """获取编译错误列表"""
        try:
            client = await self._get_client()
            resp = await client.get("/api/compile/errors")
            if resp.status_code == 200:
                data = resp.json()
                return data.get("errors", [])
            return []
        except Exception:
            return []

    async def send_scene(self, scene_desc: Dict[str, Any], tscn_text: str = None, max_retries: int = 15, retry_delay: float = 2.0) -> Dict[str, Any]:
        """发送场景到 Godot 编辑器构建。

        Args:
            scene_desc: 场景描述 JSON
            tscn_text: 若提供，则为 Python 侧已生成的合法 .tscn 文本，插件优先直接落盘
            max_retries: 最大重试次数
            retry_delay: 重试间隔（秒）

        Returns:
            构建结果
        """
        try:
            client = await self._get_client()
            # 优先使用 Python 侧构建的 .tscn 文本（绕开插件端类型错配）
            payload = (
                {"tscn": tscn_text, "scene_name": scene_desc.get("scene_name", "GameScene")}
                if tscn_text else scene_desc
            )
            resp = await client.post("/api/scene/generate", json=payload)
            if resp.status_code != 200:
                return {"status": "error", "error": f"场景构建请求失败: {resp.status_code}"}

            result = resp.json()

            # 轮询等待构建完成
            for _ in range(max_retries):
                status = result.get("status", "")
                if status not in ("pending", "building"):
                    return result
                await asyncio.sleep(retry_delay)
                task_id = result.get("task_id", "")
                if task_id:
                    resp = await client.get(f"/api/scene/status/{task_id}")
                    if resp.status_code == 200:
                        result = resp.json()

            return result
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def reload_project(self) -> Dict[str, Any]:
        """触发 Godot 重新加载项目"""
        try:
            client = await self._get_client()
            resp = await client.post("/api/project/reload")
            return resp.json() if resp.status_code == 200 else {"status": "error"}
        except Exception as e:
            return {"status": "error", "error": str(e)}
