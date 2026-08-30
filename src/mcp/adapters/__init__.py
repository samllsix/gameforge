"""MCP Adapters - In-process MCP server adapters for lightweight operations."""

from .base import MCPAdapter, MCPToolResult
from .knowledge_adapter import KnowledgeMCPAdapter
from .test_adapter import TestMCPAdapter

__all__ = [
    "MCPAdapter",
    "MCPToolResult",
    "KnowledgeMCPAdapter",
    "TestMCPAdapter",
]
