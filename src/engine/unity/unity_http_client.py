"""GameForge - Unity Editor HTTP客户端

通过HTTP与Unity Editor内的HTTP服务器通信，实现场景生成和文件导入。
"""

import asyncio
import json
import logging
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger(__name__)


class UnityHTTPClient:
    """Unity Editor HTTP客户端"""

    def __init__(self, host: str = "localhost", port: int = 8765, timeout: float = 60.0):
        self.base_url = f"http://{host}:{port}"
        self.timeout = timeout

    async def check_health(self) -> bool:
        """检查Unity Editor HTTP服务器是否运行"""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{self.base_url}/api/health")
                if resp.status_code == 200:
                    data = resp.json()
                    return data.get("status") == "ok"
        except Exception:
            pass
        return False

    async def send_scene(self, scene_desc: dict) -> dict:
        """发送场景描述到Unity Editor构建场景

        Args:
            scene_desc: 场景描述JSON

        Returns:
            Unity返回的结果 {"status": "success", "scene_path": "...", "object_count": N}
        """
        max_retries = 15  # 最多重试15次（编译可能需要较长时间）
        retry_delay = 2.0  # 每次等待2秒

        for attempt in range(max_retries):
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    resp = await client.post(
                        f"{self.base_url}/api/scene/generate",
                        json=scene_desc,
                        headers={"Content-Type": "application/json"},
                    )
                    data = resp.json()

                    # Unity正在编译，等待后重试
                    if data.get("status") == "pending":
                        logger.info(f"Unity compiling scripts, retrying in {retry_delay}s (attempt {attempt + 1}/{max_retries})")
                        await asyncio.sleep(retry_delay)
                        continue

                    if resp.status_code == 200:
                        logger.info(f"Scene generated: {data.get('scene_path')}, {data.get('object_count')} objects")
                        return data
                    else:
                        error = data.get("error", "Unknown error")
                        logger.error(f"Scene generation failed: {error}")
                        return {"status": "error", "error": error}
            except httpx.ConnectError:
                logger.warning("Unity Editor HTTP server not running")
                return {"status": "error", "error": "Unity Editor未启动或HTTP服务器未运行"}
            except httpx.TimeoutException:
                logger.warning("Scene generation request timed out")
                return {"status": "error", "error": "请求超时，Unity Editor可能正忙"}
            except Exception as e:
                logger.error(f"Scene generation error: {e}")
                return {"status": "error", "error": str(e)}

        return {"status": "error", "error": "场景生成超时（编译等待超时）"}

    async def import_files(self, files: Dict[str, str]) -> dict:
        """发送代码文件到Unity Editor导入

        Args:
            files: 文件字典 {"Assets/Scripts/X.cs": "文件内容", ...}

        Returns:
            Unity返回的结果
        """
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(
                    f"{self.base_url}/api/import",
                    json={"files": files},
                    headers={"Content-Type": "application/json"},
                )
                return resp.json()
        except httpx.ConnectError:
            return {"status": "error", "error": "Unity Editor未启动"}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def compile_scripts(self) -> dict:
        """请求Unity Editor编译脚本

        Returns:
            {"status": "success"|"error", "errors": [...], "warnings": [...]}
        """
        max_retries = 20
        retry_delay = 2.0

        for attempt in range(max_retries):
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    resp = await client.post(f"{self.base_url}/api/compile")
                    data = resp.json()

                    if data.get("status") == "compiling":
                        logger.info(f"Unity compiling, retrying in {retry_delay}s (attempt {attempt + 1}/{max_retries})")
                        await asyncio.sleep(retry_delay)
                        continue

                    return data
            except httpx.ConnectError:
                return {"status": "error", "error": "Unity Editor未启动", "errors": []}
            except Exception as e:
                return {"status": "error", "error": str(e), "errors": []}

        return {"status": "error", "error": "编译超时", "errors": []}

    async def get_compile_errors(self) -> dict:
        """获取Unity编译错误

        Returns:
            {"errors": [{"file": "...", "line": N, "message": "...", "code": "CS..."}], "warnings": [...]}
        """
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(f"{self.base_url}/api/compile/errors")
                return resp.json()
        except httpx.ConnectError:
            return {"status": "error", "error": "Unity Editor未启动", "errors": []}
        except Exception as e:
            return {"status": "error", "error": str(e), "errors": []}
