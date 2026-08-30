"""GameForge - MCP Integration Tests

Tests for the MCP (Model Context Protocol) integration.
"""

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


class TestMCPAdapters:
    """Test MCP adapters (in-process)."""
    
    def test_knowledge_adapter_initialization(self):
        """Test Knowledge MCP adapter initialization."""
        from src.mcp.adapters.knowledge_adapter import KnowledgeMCPAdapter
        
        adapter = KnowledgeMCPAdapter()
        assert adapter.name == "knowledge"
        assert adapter.description == "Godot knowledge base operations for game development"
        
        tools = adapter.get_tool_schemas()
        tool_names = [t["name"] for t in tools]
        
        assert "search_knowledge" in tool_names
        assert "get_api_reference" in tool_names
        assert "get_best_practice" in tool_names
        assert "list_categories" in tool_names
    
    @pytest.mark.asyncio
    async def test_knowledge_adapter_search(self):
        """Test Knowledge adapter search functionality."""
        from src.mcp.adapters.knowledge_adapter import KnowledgeMCPAdapter
        
        adapter = KnowledgeMCPAdapter()
        
        # Test search (may return empty if no knowledge base)
        result = await adapter.call_tool("search_knowledge", {
            "query": "player movement",
            "top_k": 3,
        })
        
        assert result is not None
        assert not result.is_error or "No knowledge" in result.to_text()
    
    @pytest.mark.asyncio
    async def test_knowledge_adapter_list_categories(self):
        """Test Knowledge adapter list categories."""
        from src.mcp.adapters.knowledge_adapter import KnowledgeMCPAdapter
        
        adapter = KnowledgeMCPAdapter()
        
        result = await adapter.call_tool("list_categories", {})
        
        assert result is not None
        assert not result.is_error
        
        data = result.to_json()
        assert "categories" in data
        assert len(data["categories"]) > 0
    
    def test_test_adapter_initialization(self):
        """Test Test MCP adapter initialization."""
        from src.mcp.adapters.test_adapter import TestMCPAdapter
        
        adapter = TestMCPAdapter()
        assert adapter.name == "test"
        assert adapter.description == "Test management and evaluation operations"
        
        tools = adapter.get_tool_schemas()
        tool_names = [t["name"] for t in tools]
        
        assert "generate_test_cases" in tool_names
        assert "run_tests" in tool_names
        assert "get_metrics" in tool_names
        assert "get_dashboard" in tool_names
    
    @pytest.mark.asyncio
    async def test_test_adapter_generate_cases(self):
        """Test Test adapter generate test cases."""
        from src.mcp.adapters.test_adapter import TestMCPAdapter
        
        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = TestMCPAdapter(data_dir=tmpdir)
            
            result = await adapter.call_tool("generate_test_cases", {
                "category": "all",
                "count": 3,
            })
            
            assert result is not None
            assert not result.is_error
            
            data = result.to_json()
            assert "cases" in data
            assert len(data["cases"]) > 0
    
    @pytest.mark.asyncio
    async def test_test_adapter_get_dashboard(self):
        """Test Test adapter get dashboard."""
        from src.mcp.adapters.test_adapter import TestMCPAdapter
        
        adapter = TestMCPAdapter()
        
        result = await adapter.call_tool("get_dashboard", {
            "time_range": "all",
        })
        
        assert result is not None
        assert not result.is_error
        
        data = result.to_json()
        assert "summary" in data


class TestMCPServers:
    """Test MCP servers (stdio)."""
    
    def test_image_server_initialization(self):
        """Test Image MCP server initialization."""
        from src.mcp.servers.image_server import ImageMCPServer
        
        server = ImageMCPServer()
        
        tools = server.tool_schemas()
        tool_names = [t["name"] for t in tools]
        
        assert "generate_image" in tool_names
        assert "generate_sprite_sheet" in tool_names
        assert "generate_tileset" in tool_names
        assert "generate_animation" in tool_names
    
    @pytest.mark.asyncio
    async def test_image_server_handle_request(self):
        """Test Image MCP server request handling."""
        from src.mcp.servers.image_server import ImageMCPServer
        
        server = ImageMCPServer()
        
        # Test tools/list request
        response = await server.handle_mcp_request({
            "method": "tools/list",
        })
        
        assert "tools" in response
        assert len(response["tools"]) == 4
    
    def test_engine_server_initialization(self):
        """Test Engine MCP server initialization."""
        from src.mcp.servers.engine_server import EngineMCPServer
        
        server = EngineMCPServer()
        
        tools = server.tool_schemas()
        tool_names = [t["name"] for t in tools]
        
        assert "compile_headless" in tool_names
        assert "build_scene" in tool_names
        assert "run_smoke_test" in tool_names
        assert "capture_screenshot" in tool_names
        assert "validate_tscn" in tool_names
        assert "get_engine_info" in tool_names
    
    @pytest.mark.asyncio
    async def test_engine_server_get_info(self):
        """Test Engine MCP server get engine info."""
        from src.mcp.servers.engine_server import EngineMCPServer
        
        server = EngineMCPServer()
        
        result = await server._get_engine_info()
        
        assert "configured" in result
        assert "version" in result
    
    def test_asset_server_initialization(self):
        """Test Asset MCP server initialization."""
        from src.mcp.servers.asset_server import AssetMCPServer
        
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            audit_log = os.path.join(tmpdir, "audit.log")
            
            server = AssetMCPServer(
                output_dir=tmpdir,
                db_path=db_path,
                audit_log=audit_log,
            )
            
            tools = server.tool_schemas()
            tool_names = [t["name"] for t in tools]
            
            assert "register_asset" in tool_names
            assert "query_assets" in tool_names
            assert "deduplicate" in tool_names
            assert "convert_format" in tool_names
            assert "import_to_godot" in tool_names
            assert "get_asset_info" in tool_names
    
    @pytest.mark.asyncio
    async def test_asset_server_register_asset(self):
        """Test Asset MCP server register asset."""
        from src.mcp.servers.asset_server import AssetMCPServer
        
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            audit_log = os.path.join(tmpdir, "audit.log")
            
            server = AssetMCPServer(
                output_dir=tmpdir,
                db_path=db_path,
                audit_log=audit_log,
            )
            
            result = await server._register_asset(
                name="test_sprite",
                path="test_sprite.png",
                type="image",
                tags=["test", "sprite"],
                trace_id="test_123",
            )
            
            assert result["status"] == "success"
            assert "asset_id" in result
            assert result["name"] == "test_sprite"


class TestMCPClient:
    """Test MCP client functionality."""
    
    def test_client_config(self):
        """Test MCP client configuration."""
        from src.mcp.client.base import MCPClientConfig
        
        config = MCPClientConfig(
            name="test",
            transport="stdio",
            command="python",
            args=["-m", "src.mcp.servers.image_server"],
        )
        
        assert config.name == "test"
        assert config.transport == "stdio"
        assert config.command == "python"
    
    def test_tool_result(self):
        """Test MCP tool result parsing."""
        from src.mcp.client.base import MCPToolResult
        
        result = MCPToolResult(
            content=[{"type": "text", "text": '{"status": "success"}'}],
            is_error=False,
        )
        
        assert not result.is_error
        assert result.to_text() == '{"status": "success"}'
        assert result.to_json() == {"status": "success"}
    
    def test_manager_initialization(self):
        """Test MCP client manager initialization."""
        from src.mcp.client.manager import MCPClientManager
        
        manager = MCPClientManager()
        assert manager._clients == {}
        assert manager._adapters == {}
    
    @pytest.mark.asyncio
    async def test_manager_with_config(self):
        """Test MCP client manager with configuration."""
        from src.mcp.client.manager import MCPClientManager
        
        config = {
            "mcp": {
                "enabled": True,
                "servers": {
                    "knowledge": {
                        "transport": "in_process",
                        "module": "src.mcp.adapters.knowledge_adapter.KnowledgeMCPAdapter",
                    },
                    "test": {
                        "transport": "in_process",
                        "module": "src.mcp.adapters.test_adapter.TestMCPAdapter",
                    },
                },
            }
        }
        
        manager = MCPClientManager()
        manager.load_config_from_dict(config)
        
        await manager.initialize()
        
        # Check that adapters were loaded
        assert "knowledge" in manager._adapters
        assert "test" in manager._adapters
        
        # Check server status
        status = manager.get_server_status()
        assert "knowledge" in status
        assert "test" in status
        assert status["knowledge"]["type"] == "in_process"
        
        await manager.shutdown()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
