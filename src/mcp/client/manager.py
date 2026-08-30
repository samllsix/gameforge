"""MCP Client Manager - Manages multiple MCP client connections."""

from __future__ import annotations

import asyncio
import importlib
import json
import os
from typing import Any, Dict, List, Optional, Type

from .base import MCPClient, MCPClientConfig, MCPToolResult


class MCPClientManager:
    """Manages multiple MCP client connections."""
    
    def __init__(self, config_path: Optional[str] = None):
        self._clients: Dict[str, MCPClient] = {}
        self._adapters: Dict[str, Any] = {}
        self._config: Dict[str, Any] = {}
        
        if config_path:
            self.load_config(config_path)
    
    def load_config(self, config_path: str):
        """Load MCP configuration from file."""
        try:
            import yaml
            with open(config_path, "r", encoding="utf-8") as f:
                self._config = yaml.safe_load(f) or {}
        except Exception as e:
            print(f"Failed to load MCP config: {e}")
    
    def load_config_from_dict(self, config: Dict[str, Any]):
        """Load configuration from dictionary."""
        self._config = config
    
    async def initialize(self):
        """Initialize all configured MCP servers."""
        mcp_config = self._config.get("mcp", {})
        if not mcp_config.get("enabled", True):
            return
        
        servers = mcp_config.get("servers", {})
        
        for server_name, server_config in servers.items():
            await self._initialize_server(server_name, server_config)
    
    async def _initialize_server(self, name: str, config: Dict[str, Any]):
        """Initialize a single MCP server."""
        transport = config.get("transport", "stdio")
        
        if transport == "stdio":
            client_config = MCPClientConfig(
                name=name,
                transport="stdio",
                command=config.get("command"),
                args=config.get("args", []),
                cwd=config.get("cwd"),
                timeout_ms=config.get("timeout_ms", 30000),
                retry_max=config.get("retry", {}).get("max", 3),
                retry_backoff=config.get("retry", {}).get("backoff", "exponential"),
            )
            
            client = MCPClient(client_config)
            success = await client.connect()
            
            if success:
                self._clients[name] = client
            else:
                print(f"Failed to connect to MCP server: {name}")
        
        elif transport == "in_process":
            await self._load_in_process_adapter(name, config)
    
    async def _load_in_process_adapter(self, name: str, config: Dict[str, Any]):
        """Load an in-process adapter."""
        module_path = config.get("module")
        if not module_path:
            return
        
        try:
            # Import the adapter module
            parts = module_path.rsplit(".", 1)
            if len(parts) == 2:
                module_name, class_name = parts
                module = importlib.import_module(module_name)
                adapter_class = getattr(module, class_name)
                adapter = adapter_class()
                self._adapters[name] = adapter
            else:
                # Try to import as module
                module = importlib.import_module(module_path)
                if hasattr(module, "adapter"):
                    self._adapters[name] = module.adapter
        except Exception as e:
            print(f"Failed to load in-process adapter {name}: {e}")
    
    async def shutdown(self):
        """Shutdown all connections."""
        for client in self._clients.values():
            await client.disconnect()
        self._clients.clear()
        self._adapters.clear()
    
    async def list_tools(self, server_name: Optional[str] = None) -> List[Dict[str, Any]]:
        """List tools from one or all servers."""
        tools = []
        
        if server_name:
            # List from specific server
            if server_name in self._clients:
                client_tools = await self._clients[server_name].list_tools()
                for tool in client_tools:
                    tool["server"] = server_name
                tools.extend(client_tools)
            elif server_name in self._adapters:
                adapter = self._adapters[server_name]
                adapter_tools = adapter.get_tool_schemas()
                for tool in adapter_tools:
                    tool["server"] = server_name
                tools.extend(adapter_tools)
        else:
            # List from all servers
            for name, client in self._clients.items():
                try:
                    client_tools = await client.list_tools()
                    for tool in client_tools:
                        tool["server"] = name
                    tools.extend(client_tools)
                except Exception:
                    pass
            
            for name, adapter in self._adapters.items():
                try:
                    adapter_tools = adapter.get_tool_schemas()
                    for tool in adapter_tools:
                        tool["server"] = name
                    tools.extend(adapter_tools)
                except Exception:
                    pass
        
        return tools
    
    async def call_tool(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        server_name: Optional[str] = None,
    ) -> MCPToolResult:
        """Call a tool on a specific or best-matching server."""
        if server_name:
            # Call on specific server
            if server_name in self._clients:
                return await self._clients[server_name].call_tool(tool_name, arguments)
            elif server_name in self._adapters:
                adapter = self._adapters[server_name]
                return await adapter.call_tool(tool_name, arguments)
            else:
                return MCPToolResult(
                    content=[{"type": "text", "text": f"Server not found: {server_name}"}],
                    is_error=True,
                )
        
        # Try to find the tool in any server
        # First check adapters (in-process)
        for name, adapter in self._adapters.items():
            try:
                tools = adapter.get_tool_schemas()
                tool_names = [t["name"] for t in tools]
                if tool_name in tool_names:
                    return await adapter.call_tool(tool_name, arguments)
            except Exception:
                pass
        
        # Then check stdio clients
        for name, client in self._clients.items():
            try:
                tools = await client.list_tools()
                tool_names = [t["name"] for t in tools]
                if tool_name in tool_names:
                    return await client.call_tool(tool_name, arguments)
            except Exception:
                pass
        
        return MCPToolResult(
            content=[{"type": "text", "text": f"Tool not found: {tool_name}"}],
            is_error=True,
        )
    
    def get_server_status(self) -> Dict[str, Dict[str, Any]]:
        """Get status of all servers."""
        status = {}
        
        for name, client in self._clients.items():
            status[name] = {
                "type": "stdio",
                "connected": client.is_connected,
            }
        
        for name, adapter in self._adapters.items():
            status[name] = {
                "type": "in_process",
                "connected": True,
                "tools": len(adapter.get_tool_schemas()),
            }
        
        return status
    
    async def health_check(self) -> Dict[str, bool]:
        """Check health of all servers."""
        health = {}
        
        for name, client in self._clients.items():
            try:
                # Try to list tools as health check
                await client.list_tools()
                health[name] = True
            except Exception:
                health[name] = False
        
        for name in self._adapters:
            health[name] = True
        
        return health


# Global instance
_manager: Optional[MCPClientManager] = None


def get_manager() -> MCPClientManager:
    """Get or create the global MCP client manager."""
    global _manager
    if _manager is None:
        _manager = MCPClientManager()
    return _manager


async def initialize_mcp(config_path: Optional[str] = None):
    """Initialize the global MCP client manager."""
    manager = get_manager()
    if config_path:
        manager.load_config(config_path)
    await manager.initialize()
    return manager


async def shutdown_mcp():
    """Shutdown the global MCP client manager."""
    global _manager
    if _manager:
        await _manager.shutdown()
        _manager = None
