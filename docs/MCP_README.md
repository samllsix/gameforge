# GameForge MCP (Model Context Protocol) 实现

## 概述

MCP (Model Context Protocol) 是一种标准化协议，用于 AI 模型与外部工具和服务进行通信。GameForge 实现了完整的 MCP 系统，包括：

1. **MCP 适配器** (in-process) - 轻量级操作，直接在进程内运行
2. **MCP 服务器** (stdio) - 重 IO 操作，通过子进程通信
3. **MCP 客户端** - 统一的客户端管理器
4. **配置系统** - YAML 配置文件支持

## 架构

```
┌─────────────────────────────────────────────────────────┐
│                    MCP Client Manager                   │
├─────────────────────────────────────────────────────────┤
│  ┌─────────────────┐  ┌─────────────────┐              │
│  │ In-Process      │  │ Stdio           │              │
│  │ Adapters        │  │ Servers         │              │
│  ├─────────────────┤  ├─────────────────┤              │
│  │ Knowledge MCP   │  │ Image MCP       │              │
│  │ Test MCP        │  │ Engine MCP      │              │
│  └─────────────────┘  │ Asset MCP       │              │
│                       └─────────────────┘              │
└─────────────────────────────────────────────────────────┘
```

## 实现的组件

### 1. MCP 适配器 (in-process)

#### Knowledge MCP Adapter
- **位置**: `src/mcp/adapters/knowledge_adapter.py`
- **功能**: Godot 知识库检索
- **工具**:
  - `search_knowledge` - 搜索知识库
  - `get_api_reference` - 获取 API 参考
  - `get_best_practice` - 获取最佳实践
  - `list_categories` - 列出分类

#### Test MCP Adapter
- **位置**: `src/mcp/adapters/test_adapter.py`
- **功能**: 测试管理和评测
- **工具**:
  - `generate_test_cases` - 生成测试用例
  - `run_tests` - 运行测试
  - `get_metrics` - 获取评测指标
  - `get_dashboard` - 获取仪表盘数据

### 2. MCP 服务器 (stdio)

#### Image MCP Server
- **位置**: `src/mcp/servers/image_server.py`
- **功能**: 美术资源生成
- **工具**:
  - `generate_image` - 生成概念图
  - `generate_sprite_sheet` - 生成精灵表
  - `generate_tileset` - 生成瓦片集
  - `generate_animation` - 生成动画

#### Engine MCP Server
- **位置**: `src/mcp/servers/engine_server.py`
- **功能**: Godot 引擎操作
- **工具**:
  - `compile_headless` - 无头编译检查
  - `build_scene` - 构建场景
  - `run_smoke_test` - 运行烟雾测试
  - `capture_screenshot` - 截图
  - `validate_tscn` - 验证 .tscn 文件
  - `get_engine_info` - 获取引擎信息

#### Asset MCP Server
- **位置**: `src/mcp/servers/asset_server.py`
- **功能**: 资源管理
- **工具**:
  - `register_asset` - 注册资源
  - `query_assets` - 查询资源
  - `deduplicate` - 去重
  - `convert_format` - 格式转换
  - `import_to_godot` - 导入到 Godot
  - `get_asset_info` - 获取资源信息

### 3. MCP 客户端

- **位置**: `src/mcp/client/`
- **功能**: 统一的客户端管理
- **组件**:
  - `MCPClient` - 基础客户端类
  - `MCPClientConfig` - 客户端配置
  - `MCPClientManager` - 客户端管理器

## 配置

### 配置文件位置

`config/config.yaml` 中的 MCP 配置段：

```yaml
mcp:
  enabled: true
  servers:
    # In-process 适配器
    knowledge:
      transport: in_process
      module: src.mcp.adapters.knowledge_adapter.KnowledgeMCPAdapter
    test:
      transport: in_process
      module: src.mcp.adapters.test_adapter.TestMCPAdapter
    
    # Stdio 服务器
    image:
      transport: stdio
      command: python
      args: ["-m", "src.mcp.servers.image_server"]
      cwd: ${GAMEFORGE_PROJECT_ROOT:.}
      timeout_ms: 30000
    engine:
      transport: stdio
      command: python
      args: ["-m", "src.mcp.servers.engine_server"]
      cwd: ${GAMEFORGE_PROJECT_ROOT:.}
      timeout_ms: 60000
    asset:
      transport: stdio
      command: python
      args: ["-m", "src.mcp.servers.asset_server"]
      cwd: ${GAMEFORGE_PROJECT_ROOT:.}
      timeout_ms: 10000
  
  client:
    pool_size: 3
    request_timeout_ms: 30000
    circuit_breaker:
      threshold: 5
      recovery_s: 60
```

## 使用示例

### 1. 使用适配器

```python
import asyncio
from src.mcp.adapters.knowledge_adapter import KnowledgeMCPAdapter

async def main():
    adapter = KnowledgeMCPAdapter()
    
    # 搜索知识
    result = await adapter.call_tool("search_knowledge", {
        "query": "player movement",
        "top_k": 3,
    })
    print(result.to_text())

asyncio.run(main())
```

### 2. 使用客户端管理器

```python
import asyncio
from src.mcp.client.manager import MCPClientManager

async def main():
    config = {
        "mcp": {
            "enabled": True,
            "servers": {
                "knowledge": {
                    "transport": "in_process",
                    "module": "src.mcp.adapters.knowledge_adapter.KnowledgeMCPAdapter",
                },
            },
        }
    }
    
    manager = MCPClientManager()
    manager.load_config_from_dict(config)
    await manager.initialize()
    
    # 列出所有工具
    tools = await manager.list_tools()
    print(f"可用工具: {[t['name'] for t in tools]}")
    
    # 调用工具
    result = await manager.call_tool("list_categories", {})
    print(result.to_text())
    
    await manager.shutdown()

asyncio.run(main())
```

### 3. 命令行使用

```bash
# 列出 Image MCP 服务器的工具
python -m src.mcp.servers.image_server

# 以 stdio 模式运行
python -m src.mcp.servers.engine_server --stdio

# 运行示例
python examples/mcp_example.py
```

## 测试

### 运行测试

```bash
# 运行 MCP 集成测试
python -m pytest tests/test_mcp_integration.py -v

# 运行所有测试
python -m pytest tests/ -v
```

### 测试覆盖

- MCP 适配器初始化
- 工具调用和结果解析
- MCP 服务器请求处理
- 客户端连接管理
- 配置加载

## 运行时语义

### 1. 传输模型

- **In-process**: 轻量级操作，直接在进程内运行，无 IPC 开销
- **Stdio**: 重 IO 操作，通过子进程通信，支持自动重连

### 2. 失败降级链

每个工具都有明确的失败降级策略：

- **Image MCP**: 外部 API 缺失 → 加载历史素材 → 返回复用结果
- **Engine MCP**: Godot 未运行 → 直接写 .tscn 文件 → 跳过编译
- **Asset MCP**: 写锁队列 → 审计日志 → 并发控制

### 3. 资源共享

Asset MCP 提供共享访问语义：

- **写锁粒度**: 单文件级，避免全局锁饿死
- **读操作**: 不持锁，但检查版本一致性
- **审计日志**: 所有写操作记录 trace-id

## 下一步工作

### 阶段 0: 能力补齐

- [ ] 实现 `src/image/ai_image_client.py` (AI 图像生成)
- [ ] 实现 `src/eval/metrics/collector.py` (指标收集)
- [ ] 实现 `src/eval/dashboard/aggregator.py` (仪表盘聚合)
- [ ] 抽出 `src/engine/godot/tscn_validator.py` (.tscn 校验器)
- [ ] 修正 `TestCase.engine` 默认值为 "godot"

### 阶段 1: 双跑比对

- [ ] 实现双跑比对系统
- [ ] 建立 `data/mcp_parity_log/` 目录
- [ ] 实现结果差异检测

### 阶段 2: MCP 优先

- [ ] 切换 LangGraph 工作流的 `tool_choice` 到 MCP
- [ ] 标记直调代码为 deprecated

### 阶段 3: 直调 deprecated

- [ ] 移除直接 import
- [ ] 清理资产

## 文件结构

```
src/mcp/
├── __init__.py
├── adapters/
│   ├── __init__.py
│   ├── base.py
│   ├── knowledge_adapter.py
│   └── test_adapter.py
├── client/
│   ├── __init__.py
│   ├── base.py
│   └── manager.py
└── servers/
    ├── __init__.py
    ├── image_server.py
    ├── engine_server.py
    └── asset_server.py
```

## 依赖

MCP 实现不需要额外依赖，使用 Python 标准库：

- `asyncio` - 异步编程
- `json` - JSON 处理
- `sqlite3` - 数据库
- `subprocess` - 子进程管理

如需使用 MCP SDK（可选），可安装：

```bash
pip install mcp
```

## 验收准则

1. **双跑一致性**: 50 次以上双跑对比，输出差异率 ≤ 1%
2. **延迟成本**: Knowledge/Test P95 延迟 < 20ms; 重 IO server P95 延迟 < 启动预算 1.5 倍
3. **故障恢复**: stdio 子进程被 kill 后，client 在 30s 内自动重连
4. **Asset 共享一致性**: 四 Agent 并发写同一资源不产生脏读/覆盖
5. **配置可关**: `mcp.enabled: false` 时所有 agent 退化到直调旧 API
