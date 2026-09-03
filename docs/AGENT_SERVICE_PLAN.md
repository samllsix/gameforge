# GameForge Agent 服务可执行方案

## 1. 服务总览

GameForge Agent 服务基于 LangGraph 多智能体工作流，提供代码生成、审查、测试、调试等能力。

### 已实现 Agent
| Agent | 职责 | 状态 |
|-------|------|------|
| Orchestrator | 任务调度与流程控制 | ✅ |
| GameDesigner | 游戏设计与 GDM 产出 | ✅ |
| Planner | 任务规划与资源分解 | ✅ |
| CodeGenerator | GDScript 代码生成 | ✅ |
| CodeReviewer | 代码审查 | ✅ |
| Refactor | 重构优化 | ✅ |
| TestGenerator | 测试用例生成 | ✅ |
| Debugger | 错误调试与修复 | ✅ |
| SceneGenerator | Godot 场景生成 | ✅ |
| MainReviewer | 端到端审查 | ✅ |

## 2. 当前运行状态

- **API 服务**: `http://127.0.0.1:8000` ✅ 已启动
- **健康检查**: `http://127.0.0.1:8000/health` ✅ 正常
- **Redis**: ⚠️ 未运行（可选，不影响核心功能）
- **LLM**: ⚠️ ping 超时（需配置 API key）

## 3. 可执行方案

### 方案 A：快速体验（推荐）

#### 步骤 1：配置 API Key
编辑 `.env` 文件，确保有以下配置：
```bash
# LLM API Key（至少配置一个）
DEEPSEEK_API_KEY=your_deepseek_api_key
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1

# 图像生成 API Key（可选）
STEP_API_KEY=your_step_api_key
SENSENOVA_API_KEY=your_sensenova_api_key
```

#### 步骤 2：启动服务
```bash
# 后端 API 服务（已启动）
python -m src.api.main

# 或使用 Docker
docker compose up
```

#### 步骤 3：测试 Agent 服务
```bash
# 测试健康检查
curl http://127.0.0.1:8000/health

# 测试 Agent 列表
curl http://127.0.0.1:8000/api/v1/agents

# 测试代码生成（同步）
curl -X POST "http://127.0.0.1:8000/api/v1/generate_sync" \
  -H "Content-Type: application/json" \
  -d '{
    "requirements": "创建一个2D平台跳跃游戏，玩家可以左右移动和跳跃",
    "project_name": "platformer_test",
    "engine": "godot"
  }'

# 测试代码生成（流式）
curl -X POST "http://127.0.0.1:8000/api/v1/generate_stream" \
  -H "Content-Type: application/json" \
  -d '{
    "requirements": "创建一个弹幕射击游戏",
    "project_name": "shooter_test",
    "engine": "godot"
  }'
```

#### 步骤 4：浏览器访问
- **API 文档**: `http://127.0.0.1:8000/docs`
- **健康检查**: `http://127.0.0.1:8000/health`

### 方案 B：完整开发环境

#### 步骤 1：安装依赖
```bash
# Python 依赖
pip install -r requirements.txt

# 开发依赖
pip install -e ".[dev]"

# Node.js 依赖（前端）
npm install
```

#### 步骤 2：配置环境
```bash
# 复制环境配置
cp .env.example .env

# 编辑 .env 文件
# - 配置 LLM API Key
# - 配置 Godot 路径
# - 配置数据库（可选）
```

#### 步骤 3：启动基础设施
```bash
# 启动 Redis（可选）
docker compose up -d redis

# 启动 PostgreSQL（可选）
docker compose up -d postgres
```

#### 步骤 4：启动后端服务
```bash
# 开发模式
python -m src.api.main

# 或使用 uvicorn 直接启动
uvicorn src.api.main:app --reload --port 8000
```

#### 步骤 5：启动前端服务（如有）
```bash
# 前端开发服务器
npm run dev

# 或访问静态页面
npm run preview
```

### 方案 C：Docker 部署（生产）

#### 步骤 1：构建镜像
```bash
docker build -f docker/Dockerfile -t gameforge:latest .
```

#### 步骤 2：启动服务
```bash
# 仅应用（SQLite）
docker compose up

# 完整栈（Redis + PostgreSQL）
docker compose --profile deps up
```

#### 步骤 3：验证服务
```bash
# 检查容器状态
docker compose ps

# 查看日志
docker compose logs -f app

# 测试健康检查
curl http://localhost:8000/health
```

### 方案 D：仅测试 MCP 服务

#### 步骤 1：启动 MCP 服务器
```bash
# Image MCP Server
python -m src.mcp.servers.image_server --stdio

# Engine MCP Server
python -m src.mcp.servers.engine_server --stdio

# Asset MCP Server
python -m src.mcp.servers.asset_server --stdio
```

#### 步骤 2：测试 MCP 客户端
```bash
# 运行 MCP 示例
python examples/mcp_example.py

# 运行 MCP 集成测试
python -m pytest tests/test_mcp_integration.py -v

# 测试 AI 图像生成
python examples/test_ai_image.py
```

## 4. 核心 API 端点

### 4.1 Agent 工作流
| 端点 | 方法 | 描述 |
|------|------|------|
| `/api/v1/generate` | POST | 异步启动工作流 |
| `/api/v1/generate_sync` | POST | 同步执行工作流 |
| `/api/v1/generate_stream` | POST | 流式执行工作流 |
| `/api/v1/agents` | GET | 列出所有 Agent |
| `/api/v1/tasks/{task_id}` | GET | 查询任务状态 |

### 4.2 预览与导出
| 端点 | 方法 | 描述 |
|------|------|------|
| `/api/v1/preview/frame` | GET | 获取预览帧 |
| `/api/v1/preview/stats` | GET | 获取预览统计 |
| `/api/v1/concepts` | GET | 列出概念 |
| `/api/v1/concepts/random` | GET | 随机概念 |

### 4.3 健康与监控
| 端点 | 方法 | 描述 |
|------|------|------|
| `/health` | GET | 健康检查 |
| `/api/v1/stats` | GET | 服务统计 |
| `/metrics` | GET | Prometheus 指标 |

## 5. 测试 Checklist

### 5.1 基础功能测试
- [ ] 健康检查端点正常
- [ ] Agent 列表正常返回
- [ ] 同步代码生成正常
- [ ] 流式代码生成正常
- [ ] 任务状态查询正常

### 5.2 Agent 工作流测试
- [ ] GameDesigner 正常产出 GDM
- [ ] Planner 正常生成任务计划
- [ ] CodeGenerator 正常生成 GDScript
- [ ] CodeReviewer 正常审查代码
- [ ] TestGenerator 正常生成测试
- [ ] Debugger 正常调试修复

### 5.3 MCP 服务测试
- [ ] Knowledge MCP 适配器正常
- [ ] Test MCP 适配器正常
- [ ] Image MCP Server 正常
- [ ] Engine MCP Server 正常
- [ ] Asset MCP Server 正常

### 5.4 集成测试
```bash
# 运行所有测试
python -m pytest tests/ -v

# 仅运行 MCP 测试
python -m pytest tests/test_mcp_integration.py -v

# 运行 Agent 测试
python -m pytest tests/agents/ -v
```

## 6. 常见问题

### Q1: LLM ping 超时
**原因**: `.env` 中 API Key 未配置或网络不通
**解决**:
1. 检查 `.env` 中的 `DEEPSEEK_API_KEY`
2. 测试 API 连通性: `curl https://api.deepseek.com/v1/models`
3. 检查代理设置

### Q2: Redis 连接失败
**原因**: Redis 未运行
**解决**:
```bash
# 启动 Redis
docker compose up -d redis

# 或跳过 Redis（使用内存队列）
# 修改 config/config.yaml 中的 redis 配置
```

### Q3: Agent 执行卡住
**原因**: LLM 响应慢或工作流死循环
**解决**:
1. 检查 LLM API 状态
2. 查看日志: `docker compose logs -f app`
3. 调整超时配置

### Q4: MCP 服务器无法连接
**原因**: MCP 服务器未启动或配置错误
**解决**:
1. 检查 MCP 服务器进程
2. 验证 config/config.yaml 中的 MCP 配置
3. 测试 MCP 服务器: `python -m src.mcp.servers.image_server`

## 7. 下一步优化

### 短期（1-2 周）
- [ ] 修复 SenseNova API 模型名称问题
- [ ] 添加 Agent 执行超时控制
- [ ] 完善错误降级链
- [ ] 添加更多测试用例

### 中期（1 个月）
- [ ] 实现双跑比对系统
- [ ] 添加 Agent 执行监控
- [ ] 优化 LLM 调用重试策略
- [ ] 添加 Agent 执行历史记录

### 长期（3 个月）
- [ ] 实现分布式 Agent 调度
- [ ] 添加 Agent 性能分析
- [ ] 实现 Agent 热插拔
- [ ] 添加 Agent  Marketplace

## 8. 联系支持

如遇到问题，请检查：
1. **日志**: `docker compose logs -f app` 或控制台输出
2. **健康检查**: `curl http://127.0.0.1:8000/health`
3. **API 文档**: `http://127.0.0.1:8000/docs`
4. **测试套件**: `python -m pytest tests/ -v`
