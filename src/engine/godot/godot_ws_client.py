"""GameForge - Godot WebSocket 客户端

与 Godot 编辑器内嵌的 WebSocket 服务器通信。
支持流式输出和实时进度推送。
"""

import asyncio
import json
from typing import Any, Callable, Dict, List, Optional

import structlog

logger = structlog.get_logger()

try:
    import websockets
    HAS_WEBSOCKETS = True
except ImportError:
    HAS_WEBSOCKETS = False


class GodotWSClient:
    """Godot 编辑器 WebSocket 客户端

    与 Godot 编辑器内嵌的 GameForge WebSocket 插件通信。
    支持实时事件推送和流式生成。
    默认端口: 8766
    """

    def __init__(self, ws_url: str = "ws://localhost:8766"):
        if not HAS_WEBSOCKETS:
            raise ImportError("需要安装 websockets: pip install websockets")
        self.ws_url = ws_url
        self._ws = None
        self._event_handlers: Dict[str, List[Callable]] = {}
        self._connected = False
        self._receive_task: Optional[asyncio.Task] = None

    async def connect(self) -> bool:
        """连接到 Godot 编辑器 WebSocket 服务"""
        try:
            self._ws = await websockets.connect(self.ws_url)
            self._connected = True
            self._receive_task = asyncio.create_task(self._receive_loop())
            logger.info("godot_ws_connected", url=self.ws_url)
            return True
        except Exception as e:
            logger.warning("godot_ws_connect_failed", error=str(e))
            self._connected = False
            return False

    async def disconnect(self):
        """断开连接"""
        self._connected = False
        if self._receive_task:
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass
        if self._ws:
            await self._ws.close()
            self._ws = None

    @property
    def is_connected(self) -> bool:
        return self._connected and self._ws is not None

    def on(self, event_type: str, handler: Callable):
        """注册事件处理器

        Args:
            event_type: 事件类型（如 'compile_result', 'scene_complete', 'progress'）
            handler: 处理函数，签名为 async def handler(data: dict)
        """
        if event_type not in self._event_handlers:
            self._event_handlers[event_type] = []
        self._event_handlers[event_type].append(handler)

    def off(self, event_type: str, handler: Optional[Callable] = None):
        """移除事件处理器"""
        if handler is None:
            self._event_handlers.pop(event_type, None)
        elif event_type in self._event_handlers:
            self._event_handlers[event_type] = [
                h for h in self._event_handlers[event_type] if h != handler
            ]

    async def send(self, event_type: str, data: Dict[str, Any]) -> bool:
        """发送事件到 Godot 编辑器

        Args:
            event_type: 事件类型
            data: 事件数据

        Returns:
            是否发送成功
        """
        if not self.is_connected:
            return False
        try:
            message = json.dumps({"event": event_type, "data": data}, ensure_ascii=False)
            await self._ws.send(message)
            return True
        except Exception as e:
            logger.error("godot_ws_send_failed", error=str(e))
            self._connected = False
            return False

    async def send_and_wait(self, event_type: str, data: Dict[str, Any],
                            response_event: str, timeout: float = 30.0) -> Optional[Dict[str, Any]]:
        """发送事件并等待特定响应

        Args:
            event_type: 发送的事件类型
            data: 事件数据
            response_event: 期望的响应事件类型
            timeout: 超时时间（秒）

        Returns:
            响应数据，超时返回 None
        """
        future = asyncio.get_event_loop().create_future()

        async def _response_handler(resp_data: dict):
            if not future.done():
                future.set_result(resp_data)

        self.on(response_event, _response_handler)
        try:
            if not await self.send(event_type, data):
                # 发送已失败（未连接/对端断开），立即返回而不是傻等满 timeout
                return None
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            return None
        finally:
            self.off(response_event, _response_handler)

    async def import_files_stream(self, files: Dict[str, str]) -> bool:
        """通过 WebSocket 流式导入文件

        Args:
            files: 文件字典 {相对路径: 内容}

        Returns:
            是否发送成功
        """
        return await self.send("import_files", {"files": files})

    async def compile_stream(self) -> bool:
        """通过 WebSocket 触发编译"""
        return await self.send("compile", {})

    async def build_scene_stream(self, scene_desc: Dict[str, Any]) -> bool:
        """通过 WebSocket 触发场景构建"""
        return await self.send("build_scene", scene_desc)

    async def _receive_loop(self):
        """接收消息循环"""
        try:
            async for message in self._ws:
                try:
                    data = json.loads(message)
                    event_type = data.get("event", "unknown")
                    event_data = data.get("data", {})
                    await self._dispatch_event(event_type, event_data)
                except json.JSONDecodeError:
                    logger.warning("godot_ws_invalid_json", message=message[:200])
        except Exception as e:
            if self._connected:
                logger.error("godot_ws_receive_error", error=str(e))
        finally:
            self._connected = False

    async def _dispatch_event(self, event_type: str, data: Dict[str, Any]):
        """分发事件到处理器"""
        handlers = self._event_handlers.get(event_type, [])
        for handler in handlers:
            try:
                if asyncio.iscoroutinefunction(handler):
                    await handler(data)
                else:
                    handler(data)
            except Exception as e:
                logger.error("godot_ws_handler_error", event=event_type, error=str(e))

        # 也触发通配符处理器
        wildcard_handlers = self._event_handlers.get("*", [])
        for handler in wildcard_handlers:
            try:
                if asyncio.iscoroutinefunction(handler):
                    await handler({"event": event_type, "data": data})
                else:
                    handler({"event": event_type, "data": data})
            except Exception:
                pass
