# GameForge MCP 部署指南

## 概述

本文档介绍如何在不部署本地模型的情况下运行 GameForge MCP 服务。

## 方案一：使用云端 LLM API（推荐）

GameForge 已经支持多种云端 LLM API，MCP 服务可以直接使用这些 API。

### 1.1 配置云端 API

在 `.env` 文件中配置 API 密钥：

```bash
# DeepSeek API（推荐）
DEEPSEEK_API_KEY=your_deepseek_api_key

# OpenAI API
OPENAI_API_KEY=your_openai_api_key

# 智谱 API
ZHIPU_API_KEY=your_zhipu_api_key

# Kimi API
KIMI_API_KEY=your_kimi_api_key
```

### 1.2 修改 MCP 配置

编辑 `config/config.yaml`，将 MCP 服务器配置为使用云端 API：

```yaml
mcp:
  enabled: true
  servers:
    # 使用云端 API 的 MCP 服务器
    image:
      transport: stdio
      command: python
      args: ["-m", "src.mcp.servers.image_server"]
      env:
        - IMAGE_API_PROVIDER=deepseek  # 使用 DeepSeek 的图像生成
        - DEEPSEEK_API_KEY=${DEEPSEEK_API_KEY}
    
    # 知识库使用本地适配器（无需模型）
    knowledge:
      transport: in_process
      module: src.mcp.adapters.knowledge_adapter.KnowledgeMCPAdapter
    
    # 测试使用本地适配器（无需模型）
    test:
      transport: in_process
      module: src.mcp.adapters.test_adapter.TestMCPAdapter
```

### 1.3 启动服务

```bash
# 安装依赖
pip install -r requirements.txt

# 启动 API 服务
python -m src.api.main

# 或使用 Docker
docker compose up
```

## 方案二：使用预构建的 MCP 服务器

社区有许多现成的 MCP 服务器，可以直接集成。

### 2.1 常用的 MCP 服务器

| 服务器 | 功能 | 安装方式 |
|--------|------|----------|
| `@modelcontextprotocol/server-filesystem` | 文件系统访问 | `npm install -g @modelcontextprotocol/server-filesystem` |
| `@modelcontextprotocol/server-github` | GitHub API | `npm install -g @modelcontextprotocol/server-github` |
| `@modelcontextprotocol/server-postgres` | PostgreSQL | `npm install -g @modelcontextprotocol/server-postgres` |
| `@modelcontextprotocol/server-sqlite` | SQLite | `npm install -g @modelcontextprotocol/server-sqlite` |

### 2.2 集成预构建服务器

修改 `config/config.yaml`：

```yaml
mcp:
  enabled: true
  servers:
    # 使用预构建的文件系统服务器
    filesystem:
      transport: stdio
      command: npx
      args: ["-y", "@modelcontextprotocol/server-filesystem", "/path/to/allowed/dir"]
    
    # 使用预构建的 GitHub 服务器
    github:
      transport: stdio
      command: npx
      args: ["-y", "@modelcontextprotocol/server-github"]
      env:
        - GITHUB_TOKEN=${GITHUB_TOKEN}
```

## 方案三：Docker 容器化部署

将 MCP 服务器容器化，实现隔离部署。

### 3.1 创建 MCP 服务器 Dockerfile

创建 `docker/Dockerfile.mcp`：

```dockerfile
FROM python:3.11-slim

WORKDIR /app

# 安装系统依赖
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       curl \
       nodejs \
       npm \
    && rm -rf /var/lib/apt/lists/*

# 安装 Python 依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 安装 Node.js MCP 服务器
RUN npm install -g \
    @modelcontextprotocol/server-filesystem \
    @modelcontextprotocol/server-github

# 复制 MCP 服务器代码
COPY src/mcp/ src/mcp/
COPY config/ config/

# 创建非 root 用户
RUN groupadd -r mcp && useradd -r -g mcp mcp
USER mcp

# 暴露端口（如果使用 HTTP 传输）
EXPOSE 3000

# 启动 MCP 服务器
CMD ["python", "-m", "src.mcp.servers.image_server", "--stdio"]
```

### 3.2 更新 docker-compose.yml

添加 MCP 服务：

```yaml
services:
  # ... 现有服务 ...
  
  # MCP Image 服务器
  mcp-image:
    build:
      context: .
      dockerfile: docker/Dockerfile.mcp
    container_name: gameforge-mcp-image
    command: ["python", "-m", "src.mcp.servers.image_server", "--stdio"]
    volumes:
      - gameforge-assets:/app/assets
    networks:
      - gameforge
  
  # MCP Engine 服务器
  mcp-engine:
    build:
      context: .
      dockerfile: docker/Dockerfile.mcp
    container_name: gameforge-mcp-engine
    command: ["python", "-m", "src.mcp.servers.engine_server", "--stdio"]
    volumes:
      - gameforge-projects:/app/projects
    networks:
      - gameforge
  
  # MCP Asset 服务器
  mcp-asset:
    build:
      context: .
      dockerfile: docker/Dockerfile.mcp
    container_name: gameforge-mcp-asset
    command: ["python", "-m", "src.mcp.servers.asset_server", "--stdio"]
    volumes:
      - gameforge-assets:/app/assets
      - gameforge-data:/app/data
    networks:
      - gameforge

volumes:
  gameforge-assets:
  gameforge-projects:
```

### 3.3 启动容器化 MCP 服务

```bash
# 启动所有服务
docker compose up -d

# 仅启动 MCP 服务
docker compose up -d mcp-image mcp-engine mcp-asset

# 查看日志
docker compose logs -f mcp-image
```

## 方案四：HTTP/SSE 远程 MCP 服务器

MCP 协议支持 HTTP/SSE 传输，可以连接远程服务器。

### 4.1 创建 HTTP MCP 服务器

创建 `src/mcp/servers/http_server.py`：

```python
"""HTTP MCP Server - 通过 HTTP/SSE 提供 MCP 服务"""

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
import json
import asyncio

app = FastAPI()

# MCP 服务器实例
servers = {}

@app.post("/mcp/{server_name}")
async def handle_mcp_request(server_name: str, request: Request):
    """处理 MCP 请求"""
    if server_name not in servers:
        return {"error": f"Server not found: {server_name}"}
    
    body = await request.json()
    server = servers[server_name]
    response = await server.handle_mcp_request(body)
    return response

@app.get("/mcp/{server_name}/sse")
async def sse_endpoint(server_name: str):
    """SSE 端点"""
    async def event_generator():
        while True:
            # 这里应该实现真正的 SSE 逻辑
            yield f"data: {json.dumps({'type': 'ping'})}\n\n"
            await asyncio.sleep(1)
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream"
    )

@app.get("/health")
async def health_check():
    """健康检查"""
    return {"status": "ok", "servers": list(servers.keys())}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=3000)
```

### 4.2 配置远程 MCP 服务器

修改 `config/config.yaml`：

```yaml
mcp:
  enabled: true
  servers:
    # 远程 HTTP MCP 服务器
    remote-image:
      transport: http
      url: http://mcp-image-server:3000/mcp/image
      timeout_ms: 30000
    
    remote-engine:
      transport: http
      url: http://mcp-engine-server:3000/mcp/engine
      timeout_ms: 60000
```

### 4.3 启动 HTTP MCP 服务器

```bash
# 安装 FastAPI
pip install fastapi uvicorn

# 启动 HTTP MCP 服务器
python -m src.mcp.servers.http_server
```

## 方案五：混合部署（推荐生产环境）

结合多种方案，实现最佳性能和可用性。

### 5.1 架构设计

```
┌─────────────────────────────────────────────────────────┐
│                    GameForge API                         │
├─────────────────────────────────────────────────────────┤
│  MCP Client Manager                                      │
├─────────────────────────────────────────────────────────┤
│  ┌─────────────────┐  ┌─────────────────┐              │
│  │ 本地适配器       │  │ 远程服务器       │              │
│  │ (Knowledge,     │  │ (Image, Engine, │              │
│  │  Test)          │  │  Asset)         │              │
│  └─────────────────┘  └─────────────────┘              │
└─────────────────────────────────────────────────────────┘
```

### 5.2 混合配置

```yaml
mcp:
  enabled: true
  servers:
    # 本地适配器（无需模型，轻量级）
    knowledge:
      transport: in_process
      module: src.mcp.adapters.knowledge_adapter.KnowledgeMCPAdapter
    
    test:
      transport: in_process
      module: src.mcp.adapters.test_adapter.TestMCPAdapter
    
    # 远程服务器（重 IO，需要模型）
    image:
      transport: http
      url: http://mcp-image-service:3000/mcp/image
      timeout_ms: 30000
      retry:
        max: 3
        backoff: exponential
    
    engine:
      transport: http
      url: http://mcp-engine-service:3000/mcp/engine
      timeout_ms: 60000
      health_check:
        path: /health
        interval_ms: 5000
    
    asset:
      transport: http
      url: http://mcp-asset-service:3000/mcp/asset
      timeout_ms: 10000
```

### 5.3 部署步骤

```bash
# 1. 启动本地服务
python -m src.api.main

# 2. 启动远程 MCP 服务（在其他机器或容器中）
docker compose up -d mcp-image mcp-engine mcp-asset

# 3. 验证服务
curl http://localhost:3000/health
curl http://localhost:8000/health
```

## 方案六：使用云服务提供商

### 6.1 阿里云 MCP 服务

```yaml
mcp:
  servers:
    aliyun-image:
      transport: http
      url: https://mcp.aliyun.com/v1/image
      auth:
        type: bearer
        token: ${ALIYUN_MCP_TOKEN}
```

### 6.2 腾讯云 MCP 服务

```yaml
mcp:
  servers:
    tencent-image:
      transport: http
      url: https://mcp.tencent.com/v1/image
      auth:
        type: api_key
        header: X-API-Key
        key: ${TENCENT_MCP_API_KEY}
```

## 快速开始（最简单方式）

如果你只想快速体验 MCP 功能，使用以下步骤：

### 1. 安装依赖

```bash
cd game_project
pip install -r requirements.txt
```

### 2. 配置 API 密钥

```bash
# 创建 .env 文件
cp .env.example .env

# 编辑 .env 文件，添加 API 密钥
DEEPSEEK_API_KEY=your_api_key_here
```

### 3. 运行示例

```bash
# 运行 MCP 示例
python examples/mcp_example.py

# 运行测试
python -m pytest tests/test_mcp_integration.py -v
```

### 4. 启动 API 服务

```bash
# 启动 GameForge API
python -m src.api.main

# 或使用 Docker
docker compose up
```

## 故障排除

### 问题 1：MCP 服务器无法连接

```bash
# 检查服务状态
docker compose ps

# 查看日志
docker compose logs mcp-image

# 测试连接
curl http://localhost:3000/health
```

### 问题 2：API 密钥无效

```bash
# 检查环境变量
echo $DEEPSEEK_API_KEY

# 测试 API 连接
curl -H "Authorization: Bearer $DEEPSEEK_API_KEY" \
     https://api.deepseek.com/v1/models
```

### 问题 3：端口冲突

```bash
# 检查端口占用
netstat -tulpn | grep :3000

# 修改端口配置
# 编辑 docker-compose.yml 中的 ports 配置
```

## 性能优化

### 1. 连接池配置

```yaml
mcp:
  client:
    pool_size: 5  # 增加连接池大小
    request_timeout_ms: 10000  # 减少超时时间
    circuit_breaker:
      threshold: 10  # 增加熔断阈值
      recovery_s: 30  # 减少恢复时间
```

### 2. 缓存配置

```python
# 在 MCP 服务器中添加缓存
from functools import lru_cache

@lru_cache(maxsize=128)
def cached_knowledge_search(query: str):
    return search_knowledge(query)
```

### 3. 异步优化

```python
# 使用异步并发调用
import asyncio

async def parallel_mcp_calls():
    results = await asyncio.gather(
        mcp_client.call_tool("tool1", args1),
        mcp_client.call_tool("tool2", args2),
        mcp_client.call_tool("tool3", args3),
    )
    return results
```

## 总结

| 方案 | 适用场景 | 复杂度 | 成本 |
|------|----------|--------|------|
| 云端 API | 开发/测试 | 低 | 中 |
| 预构建服务器 | 快速原型 | 低 | 低 |
| Docker 容器化 | 生产环境 | 中 | 中 |
| HTTP/SSE 远程 | 分布式部署 | 中 | 高 |
| 混合部署 | 生产环境 | 高 | 高 |
| 云服务提供商 | 企业级 | 中 | 高 |

**推荐方案**：对于大多数场景，使用 **云端 API + 本地适配器** 的混合方案是最佳选择。
