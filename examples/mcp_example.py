"""GameForge - MCP Example Script

Example demonstrating how to use the MCP (Model Context Protocol) system.
"""

import asyncio
import json
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


async def example_knowledge_adapter():
    """Example using the Knowledge MCP adapter."""
    print("\n=== Knowledge MCP Adapter Example ===")
    
    from src.mcp.adapters.knowledge_adapter import KnowledgeMCPAdapter
    
    adapter = KnowledgeMCPAdapter()
    
    # List available tools
    tools = adapter.get_tool_schemas()
    print(f"Available tools: {[t['name'] for t in tools]}")
    
    # Search for knowledge
    print("\n1. Searching for 'player movement'...")
    result = await adapter.call_tool("search_knowledge", {
        "query": "player movement",
        "top_k": 3,
    })
    print(f"Result: {result.to_text()[:200]}...")
    
    # List categories
    print("\n2. Listing categories...")
    result = await adapter.call_tool("list_categories", {})
    data = result.to_json()
    print(f"Categories: {[c['name'] for c in data.get('categories', [])]}")
    
    # Get API reference
    print("\n3. Getting API reference for 'CharacterBody2D'...")
    result = await adapter.call_tool("get_api_reference", {
        "node_name": "CharacterBody2D",
        "top_k": 1,
    })
    print(f"Result: {result.to_text()[:200]}...")


async def example_test_adapter():
    """Example using the Test MCP adapter."""
    print("\n=== Test MCP Adapter Example ===")
    
    from src.mcp.adapters.test_adapter import TestMCPAdapter
    import tempfile
    
    with tempfile.TemporaryDirectory() as tmpdir:
        adapter = TestMCPAdapter(data_dir=tmpdir)
        
        # List available tools
        tools = adapter.get_tool_schemas()
        print(f"Available tools: {[t['name'] for t in tools]}")
        
        # Generate test cases
        print("\n1. Generating test cases...")
        result = await adapter.call_tool("generate_test_cases", {
            "category": "platformer",
            "count": 2,
        })
        data = result.to_json()
        print(f"Generated {len(data.get('cases', []))} test cases")
        
        # Get metrics
        print("\n2. Getting metrics for 'my_game'...")
        result = await adapter.call_tool("get_metrics", {
            "project_name": "my_game",
            "metrics": ["compile_success", "code_quality"],
        })
        data = result.to_json()
        print(f"Overall score: {data.get('overall_score', 0):.1f}")
        
        # Get dashboard
        print("\n3. Getting dashboard data...")
        result = await adapter.call_tool("get_dashboard", {
            "time_range": "last_week",
        })
        data = result.to_json()
        print(f"Total tests: {data.get('summary', {}).get('total_tests', 0)}")


async def example_engine_server():
    """Example using the Engine MCP server."""
    print("\n=== Engine MCP Server Example ===")
    
    from src.mcp.servers.engine_server import EngineMCPServer
    
    server = EngineMCPServer()
    
    # List available tools
    tools = server.tool_schemas()
    print(f"Available tools: {[t['name'] for t in tools]}")
    
    # Get engine info
    print("\n1. Getting engine info...")
    result = await server._get_engine_info()
    print(f"Engine configured: {result.get('configured', False)}")
    print(f"Version: {result.get('version_string', 'Unknown')}")
    
    # Validate tscn
    print("\n2. Validating .tscn content...")
    tscn_content = """[gd_scene load_steps=2 format=3 uid="uid://abc123"]

[ext_resource type="Script" path="res://scripts/main.gd" id="1_abc"]

[node name="Main" type="Node2D"]
script = ExtResource("1_abc")
"""
    result = await server._validate_tscn(tscn_content=tscn_content)
    print(f"Valid: {result.get('valid', False)}")
    print(f"Errors: {result.get('errors', [])}")


async def example_asset_server():
    """Example using the Asset MCP server."""
    print("\n=== Asset MCP Server Example ===")
    
    from src.mcp.servers.asset_server import AssetMCPServer
    import tempfile
    
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = tmpdir + "/test.db"
        audit_log = tmpdir + "/audit.log"
        
        server = AssetMCPServer(
            output_dir=tmpdir,
            db_path=db_path,
            audit_log=audit_log,
        )
        
        # List available tools
        tools = server.tool_schemas()
        print(f"Available tools: {[t['name'] for t in tools]}")
        
        # Register an asset
        print("\n1. Registering asset...")
        result = await server._register_asset(
            name="player_sprite",
            path="player.png",
            type="image",
            tags=["player", "character", "sprite"],
            metadata={"width": 32, "height": 32},
            trace_id="example_001",
        )
        print(f"Asset ID: {result.get('asset_id', 'N/A')}")
        
        # Query assets
        print("\n2. Querying assets...")
        result = await server._query_assets(
            tags=["player"],
            type="image",
        )
        print(f"Found {result.get('count', 0)} assets")
        
        # Get asset info
        if result.get("count", 0) > 0:
            asset_id = result["assets"][0]["id"]
            print(f"\n3. Getting asset info for {asset_id}...")
            result = await server._get_asset_info(asset_id)
            asset = result.get("asset", {})
            print(f"Asset name: {asset.get('name', 'N/A')}")


async def example_client_manager():
    """Example using the MCP client manager."""
    print("\n=== MCP Client Manager Example ===")
    
    from src.mcp.client.manager import MCPClientManager
    
    # Create manager with configuration
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
    
    # Initialize
    print("1. Initializing MCP manager...")
    await manager.initialize()
    
    # List all tools
    print("\n2. Listing all tools...")
    tools = await manager.list_tools()
    print(f"Total tools available: {len(tools)}")
    for tool in tools:
        print(f"  - {tool['name']} (server: {tool.get('server', 'N/A')})")
    
    # Call a tool
    print("\n3. Calling 'list_categories' tool...")
    result = await manager.call_tool("list_categories", {})
    print(f"Result: {result.to_text()[:100]}...")
    
    # Get server status
    print("\n4. Server status:")
    status = manager.get_server_status()
    for name, info in status.items():
        print(f"  {name}: {info}")
    
    # Shutdown
    print("\n5. Shutting down...")
    await manager.shutdown()
    print("Done!")


async def main():
    """Run all examples."""
    print("GameForge MCP Examples")
    print("=" * 50)
    
    await example_knowledge_adapter()
    await example_test_adapter()
    await example_engine_server()
    await example_asset_server()
    await example_client_manager()
    
    print("\n" + "=" * 50)
    print("All examples completed!")


if __name__ == "__main__":
    asyncio.run(main())
