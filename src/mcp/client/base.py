"""Base MCP Client - Foundation for MCP server connections."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union


@dataclass
class MCPClientConfig:
    """Configuration for MCP client connection."""
    
    name: str
    transport: str  # "stdio" or "in_process"
    command: Optional[str] = None  # For stdio transport
    args: Optional[List[str]] = None  # For stdio transport
    cwd: Optional[str] = None  # Working directory
    timeout_ms: int = 30000  # Request timeout in milliseconds
    retry_max: int = 3  # Maximum retry attempts
    retry_backoff: str = "exponential"  # "linear" or "exponential"
    
    # For in-process adapters
    module: Optional[str] = None  # Python module path
    
    # Health check
    health_check_path: Optional[str] = None
    health_check_interval_ms: int = 5000


@dataclass
class MCPToolResult:
    """Result from an MCP tool invocation."""
    
    content: List[Dict[str, Any]] = field(default_factory=list)
    is_error: bool = False
    error_message: Optional[str] = None
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MCPToolResult":
        """Create from MCP protocol response."""
        return cls(
            content=data.get("content", []),
            is_error=data.get("isError", False),
            error_message=data.get("error"),
        )
    
    def to_text(self) -> str:
        """Extract text content."""
        texts = []
        for item in self.content:
            if item.get("type") == "text":
                texts.append(item.get("text", ""))
        return "\n".join(texts)
    
    def to_json(self) -> Any:
        """Extract JSON content if available."""
        text = self.to_text()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text


class MCPClient:
    """Base client for connecting to MCP servers."""
    
    def __init__(self, config: MCPClientConfig):
        self.config = config
        self._connected = False
        self._process: Optional[subprocess.Popen] = None
        self._request_id = 0
        self._pending_requests: Dict[int, asyncio.Future] = {}
        self._reader_task: Optional[asyncio.Task] = None
        self._stderr_task: Optional[asyncio.Task] = None
    
    async def connect(self) -> bool:
        """Connect to the MCP server."""
        if self.config.transport == "stdio":
            return await self._connect_stdio()
        elif self.config.transport == "in_process":
            return await self._connect_in_process()
        return False
    
    async def _connect_stdio(self) -> bool:
        """Connect via stdio transport."""
        if not self.config.command:
            return False
        
        try:
            cmd = [self.config.command] + (self.config.args or [])
            self._process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=self.config.cwd,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
            
            # Start reader task and stderr drain task.
            # stderr PIPE 必须持续排空，否则子进程写满管道缓冲区后会永久阻塞。
            self._reader_task = asyncio.create_task(self._read_stdout())
            self._stderr_task = asyncio.create_task(self._drain_stderr())
            self._connected = True
            
            # Send initialize request
            await self._send_request("initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {
                    "name": "gameforge-mcp-client",
                    "version": "0.1.0",
                },
            })
            
            return True
        except Exception as e:
            print(f"Failed to connect to MCP server: {e}", file=sys.stderr)
            return False
    
    async def _connect_in_process(self) -> bool:
        """Connect to in-process adapter."""
        # In-process adapters are loaded directly
        self._connected = True
        return True
    
    async def disconnect(self):
        """Disconnect from the MCP server."""
        self._connected = False
        
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
        
        if self._stderr_task:
            self._stderr_task.cancel()
            try:
                await self._stderr_task
            except asyncio.CancelledError:
                pass
        
        if self._process:
            if self._process.stdin:
                try:
                    self._process.stdin.close()
                except Exception:
                    pass
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
                try:
                    # kill 后必须 wait 回收子进程，否则留下僵尸/未释放句柄
                    self._process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
        
        # Cancel pending requests
        for future in self._pending_requests.values():
            future.cancel()
        self._pending_requests.clear()
    
    async def _read_stdout(self):
        """Read from server stdout."""
        if not self._process or not self._process.stdout:
            return
        
        while True:
            try:
                line = await asyncio.get_event_loop().run_in_executor(
                    None, self._process.stdout.readline
                )
                if not line:
                    break
                
                line = line.strip()
                if not line:
                    continue
                
                try:
                    response = json.loads(line)
                    request_id = response.get("id")
                    if request_id and request_id in self._pending_requests:
                        future = self._pending_requests.pop(request_id)
                        if not future.done():
                            future.set_result(response)
                except json.JSONDecodeError:
                    pass
            except Exception as e:
                # 读循环意外终止时不能静默吞掉，否则所有 pending 请求只会等到超时
                print(f"MCP stdout reader stopped: {e}", file=sys.stderr)
                break
    
    async def _drain_stderr(self):
        """持续排空子进程 stderr，防止管道缓冲区写满导致子进程阻塞。"""
        if not self._process or not self._process.stderr:
            return
        loop = asyncio.get_running_loop()
        while True:
            try:
                line = await loop.run_in_executor(
                    None, self._process.stderr.readline
                )
            except Exception:
                return
            if not line:
                return
    
    async def _send_request(
        self, method: str, params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Send a request to the server."""
        if not self._connected:
            return {"error": "Not connected"}
        
        self._request_id += 1
        request_id = self._request_id
        
        request = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params or {},
        }
        
        if self.config.transport == "stdio" and self._process:
            # Send via stdin
            future = asyncio.get_event_loop().create_future()
            self._pending_requests[request_id] = future
            
            try:
                def _write_request():
                    self._process.stdin.write(json.dumps(request) + "\n")
                    self._process.stdin.flush()

                # stdin.write/flush 是阻塞调用，放到线程池执行避免卡死事件循环
                await asyncio.get_running_loop().run_in_executor(None, _write_request)
            except Exception as e:
                return {"error": f"Failed to send request: {str(e)}"}
            
            # Wait for response
            try:
                response = await asyncio.wait_for(
                    future, timeout=self.config.timeout_ms / 1000
                )
                return response
            except asyncio.TimeoutError:
                return {"error": "Request timeout"}
        else:
            # For in-process, this would be handled differently
            return {"error": "Not implemented for in_process transport"}
    
    async def list_tools(self) -> List[Dict[str, Any]]:
        """List available tools from the server."""
        response = await self._send_request("tools/list")
        return response.get("tools", [])
    
    async def call_tool(
        self, tool_name: str, arguments: Dict[str, Any]
    ) -> MCPToolResult:
        """Call a tool on the server."""
        response = await self._send_request("tools/call", {
            "name": tool_name,
            "arguments": arguments,
        })
        
        if "error" in response:
            return MCPToolResult(
                content=[{"type": "text", "text": response["error"]}],
                is_error=True,
            )
        
        return MCPToolResult.from_dict(response)
    
    @property
    def is_connected(self) -> bool:
        """Check if client is connected."""
        return self._connected
