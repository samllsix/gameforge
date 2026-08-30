"""Base MCP Adapter - Foundation for in-process MCP adapters."""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union


@dataclass
class MCPToolResult:
    """Result from an MCP tool invocation."""
    
    content: List[Dict[str, Any]] = field(default_factory=list)
    is_error: bool = False
    error_message: Optional[str] = None
    
    @classmethod
    def success(cls, data: Any, text: Optional[str] = None) -> "MCPToolResult":
        """Create a successful result."""
        import json
        content = []
        if text:
            content.append({"type": "text", "text": text})
        if data is not None:
            if isinstance(data, dict):
                content.append({"type": "text", "text": json.dumps(data, ensure_ascii=False)})
            elif isinstance(data, list):
                content.append({"type": "text", "text": json.dumps(data, ensure_ascii=False)})
            else:
                content.append({"type": "text", "text": str(data)})
        return cls(content=content)
    
    @classmethod
    def error(cls, message: str) -> "MCPToolResult":
        """Create an error result."""
        return cls(
            content=[{"type": "text", "text": message}],
            is_error=True,
            error_message=message,
        )
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to MCP protocol format."""
        return {
            "content": self.content,
            "isError": self.is_error,
        }
    
    def to_text(self) -> str:
        """Extract text content."""
        import json
        texts = []
        for item in self.content:
            if item.get("type") == "text":
                texts.append(item.get("text", ""))
        return "\n".join(texts)
    
    def to_json(self) -> Any:
        """Extract JSON content if available."""
        import json
        text = self.to_text()
        
        # Try to parse the entire text as JSON
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        
        # Try to find the last JSON object in the text (usually the data)
        import re
        # Find all JSON objects (including nested ones)
        json_objects = []
        depth = 0
        start = -1
        for i, char in enumerate(text):
            if char == '{':
                if depth == 0:
                    start = i
                depth += 1
            elif char == '}':
                depth -= 1
                if depth == 0 and start != -1:
                    json_str = text[start:i+1]
                    try:
                        json_obj = json.loads(json_str)
                        json_objects.append(json_obj)
                    except:
                        pass
                    start = -1
        
        if json_objects:
            # Return the last JSON object (usually contains the data)
            return json_objects[-1]
        
        return text


class MCPAdapter(ABC):
    """Base class for in-process MCP adapters.
    
    In-process adapters provide the same interface as MCP servers
    but run within the same process to avoid IPC overhead for
    lightweight operations.
    """
    
    def __init__(self, name: str, description: str):
        self.name = name
        self.description = description
        self._tools: Dict[str, Dict[str, Any]] = {}
        self._tool_handlers: Dict[str, callable] = {}
    
    def register_tool(
        self,
        name: str,
        description: str,
        input_schema: Dict[str, Any],
        handler: callable,
    ) -> None:
        """Register a tool with the adapter.
        
        Args:
            name: Tool name
            description: Tool description
            input_schema: JSON Schema for input parameters
            handler: Async function that handles tool invocation
        """
        self._tools[name] = {
            "name": name,
            "description": description,
            "inputSchema": input_schema,
        }
        self._tool_handlers[name] = handler
    
    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        """Get all registered tool schemas."""
        return list(self._tools.values())
    
    async def call_tool(
        self, tool_name: str, arguments: Dict[str, Any]
    ) -> MCPToolResult:
        """Call a registered tool.
        
        Args:
            tool_name: Name of the tool to call
            arguments: Tool arguments
            
        Returns:
            MCPToolResult with the tool output
        """
        if tool_name not in self._tool_handlers:
            return MCPToolResult.error(f"Unknown tool: {tool_name}")
        
        try:
            handler = self._tool_handlers[tool_name]
            result = await handler(**arguments)
            return result
        except Exception as e:
            return MCPToolResult.error(f"Tool execution failed: {str(e)}")
    
    async def handle_request(
        self, method: str, params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Handle an MCP protocol request.
        
        Args:
            method: MCP method name
            params: Method parameters
            
        Returns:
            MCP protocol response
        """
        if method == "tools/list":
            return {"tools": self.get_tool_schemas()}
        
        if method == "tools/call":
            tool_name = params.get("name", "")
            arguments = params.get("arguments", {})
            result = await self.call_tool(tool_name, arguments)
            return result.to_dict()
        
        return {"error": f"Unknown method: {method}"}
    
    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}(name='{self.name}')>"
