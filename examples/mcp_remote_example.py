"""GameForge - 远程 MCP 服务示例

演示如何在不部署本地模型的情况下运行 MCP 服务。
"""

import asyncio
import json
import os
import sys
from pathlib import Path

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


async def example_cloud_api():
    """示例：使用云端 API"""
    print("\n=== 使用云端 API 示例 ===")
    
    # 检查 API 密钥
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        print("[WARNING] 未设置 DEEPSEEK_API_KEY，跳过云端 API 示例")
        print("请在 .env 文件中设置：DEEPSEEK_API_KEY=your_api_key")
        return
    
    print(f"[OK] 找到 DEEPSEEK_API_KEY: {api_key[:10]}...")
    
    # 使用云端 API 的 MCP 适配器
    from src.mcp.adapters.knowledge_adapter import KnowledgeMCPAdapter
    
    adapter = KnowledgeMCPAdapter()
    
    # 搜索知识
    print("\n1. 使用云端 API 搜索知识...")
    result = await adapter.call_tool("search_knowledge", {
        "query": "player movement",
        "top_k": 3,
    })
    print(f"结果: {result.to_text()[:200]}...")


async def example_in_process_adapters():
    """示例：使用本地适配器（无需模型）"""
    print("\n=== 使用本地适配器示例（无需模型）===")
    
    from src.mcp.adapters.knowledge_adapter import KnowledgeMCPAdapter
    from src.mcp.adapters.test_adapter import TestMCPAdapter
    import tempfile
    
    # Knowledge 适配器
    print("\n1. Knowledge 适配器（无需模型）...")
    knowledge_adapter = KnowledgeMCPAdapter()
    
    tools = knowledge_adapter.get_tool_schemas()
    print(f"   可用工具: {[t['name'] for t in tools]}")
    
    result = await knowledge_adapter.call_tool("list_categories", {})
    print(f"   分类: {result.to_text()[:100]}...")
    
    # Test 适配器
    print("\n2. Test 适配器（无需模型）...")
    with tempfile.TemporaryDirectory() as tmpdir:
        test_adapter = TestMCPAdapter(data_dir=tmpdir)
        
        tools = test_adapter.get_tool_schemas()
        print(f"   可用工具: {[t['name'] for t in tools]}")
        
        result = await test_adapter.call_tool("get_dashboard", {"time_range": "all"})
        print(f"   仪表盘: {result.to_text()[:100]}...")


async def example_docker_mcp():
    """示例：Docker 容器化 MCP 服务"""
    print("\n=== Docker 容器化 MCP 服务示例 ===")
    
    print("""
要使用 Docker 运行 MCP 服务：

1. 启动 MCP 服务容器：
   docker compose up -d mcp-image mcp-engine mcp-asset

2. 检查容器状态：
   docker compose ps

3. 查看日志：
   docker compose logs -f mcp-image

4. 测试连接：
   curl http://localhost:3000/health
    """)


async def example_http_mcp():
    """示例：HTTP/SSE 远程 MCP 服务"""
    print("\n=== HTTP/SSE 远程 MCP 服务示例 ===")
    
    print("""
要使用 HTTP 传输连接远程 MCP 服务：

1. 启动 HTTP MCP 服务器：
   python -m src.mcp.servers.http_server

2. 配置 config.yaml：
   mcp:
     servers:
       remote-image:
         transport: http
         url: http://localhost:3000/mcp/image
         timeout_ms: 30000

3. 使用客户端连接：
   from src.mcp.client.manager import MCPClientManager
   manager = MCPClientManager()
   manager.load_config(config_path)
   await manager.initialize()
    """)


async def example_hybrid_deployment():
    """示例：混合部署"""
    print("\n=== 混合部署示例 ===")
    
    from src.mcp.client.manager import MCPClientManager
    
    # 混合配置：本地适配器 + 远程服务器
    config = {
        "mcp": {
            "enabled": True,
            "servers": {
                # 本地适配器（无需模型）
                "knowledge": {
                    "transport": "in_process",
                    "module": "src.mcp.adapters.knowledge_adapter.KnowledgeMCPAdapter",
                },
                "test": {
                    "transport": "in_process",
                    "module": "src.mcp.adapters.test_adapter.TestMCPAdapter",
                },
                # 远程服务器（需要模型，但可以部署在其他机器）
                # "image": {
                #     "transport": "http",
                #     "url": "http://mcp-image-server:3000/mcp/image",
                #     "timeout_ms": 30000,
                # },
            },
        }
    }
    
    manager = MCPClientManager()
    manager.load_config_from_dict(config)
    
    print("1. 初始化 MCP 管理器...")
    await manager.initialize()
    
    print("\n2. 列出所有工具...")
    tools = await manager.list_tools()
    print(f"   可用工具: {len(tools)} 个")
    for tool in tools:
        print(f"   - {tool['name']} ({tool.get('server', 'N/A')})")
    
    print("\n3. 调用工具...")
    result = await manager.call_tool("list_categories", {})
    print(f"   结果: {result.to_text()[:100]}...")
    
    print("\n4. 获取服务器状态...")
    status = manager.get_server_status()
    for name, info in status.items():
        print(f"   {name}: {info}")
    
    await manager.shutdown()
    print("\n[OK] 混合部署示例完成!")


async def main():
    """运行所有示例"""
    print("GameForge MCP 远程服务示例")
    print("=" * 60)
    
    await example_in_process_adapters()
    await example_cloud_api()
    await example_docker_mcp()
    await example_http_mcp()
    await example_hybrid_deployment()
    
    print("\n" + "=" * 60)
    print("所有示例完成!")
    print("\n快速开始:")
    print("1. 配置 .env 文件中的 API 密钥")
    print("2. 运行: python examples/mcp_remote_example.py")
    print("3. 或使用 Docker: docker compose up")


if __name__ == "__main__":
    asyncio.run(main())
