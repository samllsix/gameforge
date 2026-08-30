"""Knowledge MCP Adapter - In-process adapter for knowledge base operations."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .base import MCPAdapter, MCPToolResult


class KnowledgeMCPAdapter(MCPAdapter):
    """In-process MCP adapter for Godot knowledge base operations.
    
    This adapter wraps the knowledge lookup functions to provide
    a consistent MCP interface for knowledge retrieval operations.
    """
    
    def __init__(self):
        super().__init__(
            name="knowledge",
            description="Godot knowledge base operations for game development",
        )
        self._register_tools()
    
    def _register_tools(self) -> None:
        """Register all knowledge-related tools."""
        
        # Tool: search_knowledge
        self.register_tool(
            name="search_knowledge",
            description="Search Godot knowledge base by query string",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query for Godot knowledge",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "Number of results to return",
                        "default": 3,
                    },
                },
                "required": ["query"],
            },
            handler=self._search_knowledge,
        )
        
        # Tool: get_api_reference
        self.register_tool(
            name="get_api_reference",
            description="Get API reference for a specific Godot node or class",
            input_schema={
                "type": "object",
                "properties": {
                    "node_name": {
                        "type": "string",
                        "description": "Name of the Godot node or class",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "Number of results to return",
                        "default": 1,
                    },
                },
                "required": ["node_name"],
            },
            handler=self._get_api_reference,
        )
        
        # Tool: get_best_practice
        self.register_tool(
            name="get_best_practice",
            description="Get best practices for a specific game development topic",
            input_schema={
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "description": "Topic to search for best practices",
                    },
                    "category": {
                        "type": "string",
                        "description": "Category filter (e.g., 'input', 'physics', 'signals')",
                        "enum": [
                            "input", "physics", "signals", "animation",
                            "ui", "audio", "scripting", "scene",
                        ],
                    },
                },
                "required": ["topic"],
            },
            handler=self._get_best_practice,
        )
        
        # Tool: list_categories
        self.register_tool(
            name="list_categories",
            description="List available knowledge categories",
            input_schema={
                "type": "object",
                "properties": {},
            },
            handler=self._list_categories,
        )
    
    async def _search_knowledge(
        self, query: str, top_k: int = 3
    ) -> MCPToolResult:
        """Search the Godot knowledge base."""
        try:
            from src.core.knowledge.lookup import lookup_godot_knowledge
            
            results = lookup_godot_knowledge(query, top_k=top_k)
            return MCPToolResult.success(
                data={"results": results, "count": len(results)},
                text=f"Found {len(results)} knowledge entries for query: {query}",
            )
        except Exception as e:
            return MCPToolResult.error(f"Failed to search knowledge: {str(e)}")
    
    async def _get_api_reference(
        self, node_name: str, top_k: int = 1
    ) -> MCPToolResult:
        """Get API reference for a Godot node or class."""
        try:
            from src.core.knowledge.lookup import lookup_godot_knowledge
            
            # Search specifically for the node/class name
            results = lookup_godot_knowledge(node_name, top_k=top_k)
            
            # Filter results that are more likely to be API references
            api_results = [
                r for r in results
                if node_name.lower() in str(r.get("title", "")).lower()
                or node_name.lower() in str(r.get("content", "")).lower()
            ]
            
            if not api_results and results:
                api_results = results[:1]  # Return best match if no exact match
            
            return MCPToolResult.success(
                data={"node": node_name, "references": api_results},
                text=f"API references for {node_name}",
            )
        except Exception as e:
            return MCPToolResult.error(f"Failed to get API reference: {str(e)}")
    
    async def _get_best_practice(
        self, topic: str, category: Optional[str] = None
    ) -> MCPToolResult:
        """Get best practices for a topic."""
        try:
            from src.core.knowledge.lookup import lookup_godot_knowledge
            
            # Search for the topic
            results = lookup_godot_knowledge(topic, top_k=5)
            
            # Filter by category if specified
            if category:
                filtered = []
                for r in results:
                    tags = str(r.get("tags", "")).lower()
                    if category.lower() in tags:
                        filtered.append(r)
                results = filtered if filtered else results[:2]
            
            return MCPToolResult.success(
                data={
                    "topic": topic,
                    "category": category,
                    "best_practices": results,
                },
                text=f"Best practices for {topic}",
            )
        except Exception as e:
            return MCPToolResult.error(f"Failed to get best practice: {str(e)}")
    
    async def _list_categories(self) -> MCPToolResult:
        """List available knowledge categories."""
        # Predefined categories based on Godot development areas
        categories = [
            {"id": "input", "name": "Input", "description": "Input handling and events"},
            {"id": "physics", "name": "Physics", "description": "Physics engine and collision"},
            {"id": "signals", "name": "Signals", "description": "Signal system and events"},
            {"id": "animation", "name": "Animation", "description": "Animation system"},
            {"id": "ui", "name": "UI", "description": "User interface and controls"},
            {"id": "audio", "name": "Audio", "description": "Audio system and sound"},
            {"id": "scripting", "name": "Scripting", "description": "GDScript and programming"},
            {"id": "scene", "name": "Scene", "description": "Scene tree and nodes"},
        ]
        
        return MCPToolResult.success(
            data={"categories": categories},
            text=f"Available categories: {', '.join(c['name'] for c in categories)}",
        )
