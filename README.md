# GameForge — Godot 游戏研发全流程 AI Agent 工具链

<div align="center">

![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)
![LangGraph](https://img.shields.io/badge/LangGraph-0.2+-green.svg)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-teal.svg)
![Godot](https://img.shields.io/badge/Godot-4.6+-478cbf.svg)
![License](https://img.shields.io/badge/License-MIT-yellow.svg)

**让 AI Agent 帮你完成 Godot 游戏开发全流程 — 从需求到可运行场景**

[快速开始](#快速开始) · [功能特性](#功能特性) · [系统架构](#系统架构) · [API文档](#api-文档) · [项目结构](#项目结构)

</div>

---

## 项目简介

GameForge 是一个基于 **Multi-Agent 架构** 的 Godot 游戏研发 AI 工具链，覆盖 **策划 → 代码生成 → 场景构建 → 审查 → 测试 → 修复** 全链路。通过多个专业 AI Agent 的协作，实现从自然语言需求到可运行 Godot 项目的自动化生成。

### 核心价值

- **Multi-Agent 协作**：10 个专业 Agent 分工协作，模拟真实游戏开发团队
- **Godot 4.x 专用**：生成符合规范的 GDScript (`.gd`) 与场景文件 (`.tscn`)
- **场景 IR 生成**：从 GameDesignModel 到场景描述再到 .tscn 文件的完整管线
- **Headless 编译校验**：Godot headless 模式自动校验脚本编译与场景完整性
- **优雅降级**：LLM API 不可用时自动降级到模板生成，保证流程不中断
- **Web 界面**：内置 FastAPI + SSE 流式界面，实时查看 Agent 执行进度
- **量化评测**：多维度评测体系（编译通过率、Godot 兼容性、任务完成度）

---

## 功能特性

### 1. Multi-Agent 协作引擎

```
用户需求
  │
  ▼
GameDesigner ──▶ GameDesignModel (GDM)
  │                  ├─ genre / camera_mode / core_loop
  │                  ├─ entities (Player, Enemy, Coin, ...)
  │                  ├─ environment (Ground, Platform, ...)
  │                  └─ mvp_scope / win_conditions / fail_conditions
  ▼
Planner ──▶ TaskPlan (结构化任务列表)
  │
  ▼
SceneGenerator ──▶ Scene IR ──▶ .tscn 文件
  │                   ├─ 节点结构 (Node2D / CharacterBody2D / Area2D / ...)
  │                   ├─ SubResource 复用 (材质 / 碰撞体 / 网格)
  │                   └─ Script Stub 自动生成
  ▼
CodeGenerator ──▶ GDScript 文件
  │
  ▼
CodeReviewer ──▶ (fast_mode 可跳过)
  │
  ▼
TestGenerator ──▶ 测试用例 + Godot 引擎反馈
  │
  ▼
MainReviewer ──▶ 终审 + 反思回环 (可选)
```

### 2. 10 个专业 Agent

| Agent | 职责 | 使用的 LLM 模型 |
|-------|------|-----------------|
| GameDesigner | 游戏策划，生成 GameDesignModel | DeepSeek-v4-pro |
| Planner | 需求解析与任务规划 | DeepSeek-v4-pro |
| Orchestrator | 流程编排与任务调度 | Mimo-v2.5-pro |
| CodeGenerator | GDScript 代码生成 | DeepSeek-v4-pro |
| SceneGenerator | Godot 场景 IR → .tscn 生成 | DeepSeek-v4-pro |
| CodeReviewer | 代码质量审查 | DeepSeek-v4-pro |
| Refactor | 代码重构优化 | DeepSeek-v4-pro |
| TestGenerator | 测试生成，读取 Godot 引擎反馈 | DeepSeek-v4-pro |
| Debugger | 错误分析，可委派知识库查询 | GLM-4.5-air |
| MainReviewer | 终审，含反思回环 | DeepSeek-v4-pro |

### 3. 多智能体改造（可选开启）

通过 `config.yaml` 中的开关控制：

- **审查↔重构对话协商**（`review_refactor.dialogue_enabled`）：CodeReviewer 与 Refactor 进行多轮协商
- **反思回环**（`reflector.enabled`）：MainReviewer 触发 Reflector 进行反思迭代
- **消息总线**（`core/state/bus.py`）：Agent 间通过发布-订阅解耦通信
- **Debugger 动态委派**（`debugger_delegation`）：Debugger 委派知识库查询（默认开启）
- **Tester 引擎反馈**（`tester_engine_feedback`）：读取 Godot headless 真实编译结果（默认开启）

### 4. Godot 引擎集成

- **Headless 编译校验**：`GodotCompiler` 调用 `godot --headless --import` 验证脚本编译
- **场景构建器**：`SceneBuilder` + `TscnWriter` 将 Scene IR 写入 `.tscn` 文件
- **Godot 编辑器插件**：`addons/gameforge/` 提供 HTTP 服务 (端口 8765) 与 WebSocket 客户端 (端口 8766)
- **AI 原生插件**：`addons/ai_native/` 提供 AI 控制器组件
- **三种编译模式**：`auto`（自动选择）/ `headless`（命令行）/ `http`（编辑器插件）

### 5. 安全特性

- 8 层中间件栈：安全头 / CORS / GZip / 请求体限制 / 输入校验 / 指标 / 并发限制 / 限流
- API Key 认证（`GAMEFORGE_API_KEYS` 环境变量）
- 输入注入检测（2MB 请求限制，50000 字符输入限制）
- 60/min/IP 速率限制
- loopback 地址强制约束（非 loopback 必须设置 API Key）

---

## 系统架构

```
┌─────────────────────────────────────────────────────────────┐
│                    GameForge Platform                       │
├─────────────────────────────────────────────────────────────┤
│  【Web 界面层】  index.html + app.js + SSE 流式反馈          │
│                   /app · /api/v1/generate_stream            │
├─────────────────────────────────────────────────────────────┤
│  【API 层】  FastAPI + Uvicorn                              │
│    /api/v1/generate · /generate_stream · /generate_sync     │
│    /api/v1/task/{id} · /api/v1/agents · /health             │
├─────────────────────────────────────────────────────────────┤
│  【Multi-Agent 协调层】  LangGraph StateGraph               │
│    GameDevWorkflow: game_designer→planner→orchestrator→     │
│    code_generator→code_reviewer→test_generator→             │
│    main_reviewer→orchestrator                                │
├─────────────────────────────────────────────────────────────┤
│  【专业 Agent 层】  10 个 Agent + BaseAgent 基类             │
│    GameDesigner · Planner · CodeGenerator · SceneGenerator  │
│    CodeReviewer · Refactor · TestGenerator · Debugger       │
│    MainReviewer · Reflector                                  │
├─────────────────────────────────────────────────────────────┤
│  【LLM 适配层】  多 Provider 支持                           │
│    Mimo · DeepSeek · 智谱GLM · Kimi · SenseNova             │
├─────────────────────────────────────────────────────────────┤
│  【Godot 引擎层】  Headless 校验 + HTTP/WS 插件             │
│    GodotCompiler · SceneBuilder · TscnWriter                │
│    GodotHttpClient (8765) · GodotWsClient (8766)            │
├─────────────────────────────────────────────────────────────┤
│  【数据与存储层】                                           │
│    SQLite (SQLAlchemy) · Qdrant (可选) · Redis (可选)       │
├─────────────────────────────────────────────────────────────┤
│  【监控与日志】  structlog + LangSmith + Prometheus         │
└─────────────────────────────────────────────────────────────┘
```

---

## 技术栈

| 类别 | 技术 | 说明 |
|------|------|------|
| Agent 框架 | LangGraph + AutoGen | StateGraph 状态图驱动 |
| LLM (多 Provider) | Mimo / DeepSeek / GLM / Kimi / SenseNova | 按 Agent 角色分配模型 |
| Web 框架 | FastAPI + Uvicorn | 异步 API + SSE 流式 |
| 游戏引擎 | **Godot 4.6** | GDScript + .tscn 场景 |
| 数据库 | SQLite (SQLAlchemy) | 任务记录与生成历史 |
| 向量库 | Qdrant (可选) | 代码检索与知识库 |
| 缓存 | Redis (可选) | LLM 结果缓存 |
| 监控 | LangSmith + Prometheus + structlog | 链路追踪与指标 |
| 前端 | 原生 HTML / JS / CSS | 无框架依赖 |
| 容器化 | Docker + Docker Compose | 开发/生产部署 |

---

## 快速开始

### 环境要求

- Python 3.11-3.13
- Godot 4.6+（[下载地址](https://godotengine.org/download)）
- Windows / macOS / Linux

### 安装步骤

```bash
# 1. 克隆项目
git clone https://github.com/yourusername/gameforge.git
cd gameforge

# 2. 创建虚拟环境
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

# 3. 安装依赖
pip install -r requirements.txt
# 或安装为可编辑包（提供 gameforge CLI 命令）
pip install -e .

# 4. 配置环境变量
cp .env.example .env
# 编辑 .env，填入 LLM API Key 和 Godot 路径
```

### 配置 .env 文件

```ini
# LLM API Keys (至少配置一个)
MIMO_API_KEY=your_mimo_key
DEEPSEEK_API_KEY=your_deepseek_key

# LLM Base URLs
MIMO_BASE_URL=https://api.mimo.xiaomi.com/v1
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1

# Godot 引擎路径
GODOT_EDITOR_PATH=D:/godot/Godot_v4.6.3-stable_win64.exe/Godot_v4.6.3-stable_win64.exe
GODOT_PROJECT_PATH=D:/game_project

# 应用配置
APP_ENV=development
APP_DEBUG=true
APP_HOST=127.0.0.1
APP_PORT=8000
```

### 启动服务

**方式一：PowerShell 脚本（推荐，Windows）**

```powershell
.\start_server.ps1
```

脚本自动加载 `.env` 环境变量、设置开发模式 API Key，使用 `.venv` 中的 Python 启动 Uvicorn。

**方式二：直接命令**

```bash
python -m uvicorn src.api.main:app --host 127.0.0.1 --port 8000
```

**方式三：Docker Compose**

```bash
docker-compose up -d
# 默认映射到宿主机 8001 端口
# 访问 http://localhost:8001/docs
```

### 访问 Web 界面

启动服务后打开浏览器访问：

- **Web 界面**：http://127.0.0.1:8000/app
- **API 文档**：http://127.0.0.1:8000/docs
- **健康检查**：http://127.0.0.1:8000/health

---

## API 文档

### 核心接口

| 接口 | 方法 | 说明 |
|------|------|------|
| `/` | GET | 根信息 |
| `/health` | GET | 健康检查 + 并发统计 |
| `/app` | GET | Web 前端界面 |
| `/api/v1/generate` | POST | 异步生成（返回 task_id） |
| `/api/v1/generate_sync` | POST | 同步生成（等待完成） |
| `/api/v1/generate_stream` | POST | SSE 流式生成（实时推送 Agent 状态） |
| `/api/v1/task/{id}` | GET | 查询任务状态 |
| `/api/v1/task/{id}/wait` | POST | 等待任务完成 |
| `/api/v1/agents` | GET | 列出所有 Agent |
| `/api/v1/tasks` | GET | 历史任务列表 |
| `/api/v1/history/{id}` | GET | 生成历史详情 |
| `/api/v1/ext/compile` | POST | Godot 编译校验 |
| `/api/v1/ext/import` | POST | Godot 项目导入 |
| `/api/v1/ext/eval` | POST | 代码评测 |

### 生成请求示例

```bash
# 异步生成
curl -X POST http://127.0.0.1:8000/api/v1/generate \
  -H "Content-Type: application/json" \
  -d '{"requirements": "创建一个2D平台跳跃游戏，玩家可以移动跳跃，有计分系统", "fast_mode": true}'

# 查询任务状态
curl http://127.0.0.1:8000/api/v1/task/{task_id}
```

### CLI 工具

```bash
# 安装后可直接使用 gameforge 命令
gameforge generate --input game_requirements.txt --output output
gameforge workflow
gameforge status
```

---

## 项目结构

```
game_project/
├── src/                              # Python 源码
│   ├── cli.py                        # CLI 入口 (Click)
│   ├── api/                          # FastAPI 层
│   │   ├── main.py                   # 应用入口 + 路由 + 中间件
│   │   ├── security.py               # API Key 认证 + 输入校验
│   │   ├── middleware/               # 限流/并发/指标中间件
│   │   ├── routes/                   # 扩展路由 (compile/import/eval)
│   │   └── schemas/                  # Pydantic 请求/响应模型
│   ├── agents/                       # 多 Agent 实现
│   │   ├── base.py                   # BaseAgent 抽象基类
│   │   ├── orchestrator/             # 编排 Agent
│   │   ├── planner/                  # 规划 Agent
│   │   ├── game_designer/            # 游戏策划 Agent
│   │   ├── code_generator/           # 代码生成 Agent
│   │   ├── scene_generator/          # 场景生成 Agent
│   │   ├── code_reviewer/            # 代码审查 Agent
│   │   ├── refactor/                 # 重构 Agent
│   │   ├── test_generator/           # 测试生成 Agent
│   │   ├── debugger/                 # 调试 Agent
│   │   ├── reflector/                # 反思 Agent
│   │   ├── main_reviewer.py          # 主审查 Agent
│   │   ├── scene_ir.py               # 场景 IR 定义
│   │   └── scene_templates.py        # 场景模板
│   ├── core/                         # 核心模块
│   │   ├── concurrency.py            # 异步任务队列管理
│   │   ├── graph/workflow.py          # LangGraph 状态图
│   │   ├── state/                    # 状态管理
│   │   │   ├── game_state.py          # GameDevState TypedDict
│   │   │   └── bus.py                 # 消息总线 (发布-订阅)
│   │   ├── dialogue/                  # 多轮对话
│   │   ├── memory/                   # 记忆管理
│   │   ├── knowledge/                 # 知识库查询
│   │   └── tools/                    # 工具集
│   ├── adapters/                     # LLM 适配器
│   │   ├── openai_client.py          # OpenAI 兼容客户端
│   │   ├── mock_client.py            # Mock 客户端 (测试用)
│   │   └── factory.py                # 适配器工厂
│   ├── engine/                       # Godot 引擎集成
│   │   ├── godot/                    # Godot 工具链
│   │   │   ├── __init__.py           # GodotEditor + GodotCompiler
│   │   │   ├── godot_http_client.py  # HTTP 插件客户端 (端口 8765)
│   │   │   ├── godot_ws_client.py    # WebSocket 客户端 (端口 8766)
│   │   │   ├── project_generator.py  # 项目生成器
│   │   │   ├── scene_builder.py      # 场景构建器
│   │   │   └── tscn_writer.py        # .tscn 文件写入器
│   │   └── sandbox/                  # 沙箱执行
│   ├── db/                           # 数据库
│   │   ├── models.py                 # SQLAlchemy 模型
│   │   └── session.py                # SQLite 会话
│   ├── eval/                         # 评测体系
│   │   ├── metrics/                  # 评测指标
│   │   └── dashboard/                # 评测看板
│   └── utils/                        # 工具函数
│       ├── llm_client.py             # 统一 LLM 客户端
│       ├── code_validator.py         # 代码校验
│       ├── godot_compatibility_validator.py  # Godot 兼容性校验
│       ├── vector_store.py           # Qdrant 向量存储
│       └── logger.py                 # structlog 日志
│
├── config/                           # 配置文件
│   ├── config.yaml                   # 主配置 (LLM/Agent/Godot/安全)
│   └── prompts/                      # Prompt 模板 (11 个)
│       ├── global_system.txt          # 全局约束 (禁止 Unity, 只生成 Godot)
│       ├── code_generator_system.txt
│       ├── scene_generator_system.txt
│       └── ...
│
├── static/                           # Web 前端
│   ├── index.html                    # 主界面
│   ├── app.js                        # 前端逻辑
│   ├── style.css                     # 样式
│   └── lib/                          # 第三方库
│
├── scripts/                          # Godot GDScript (游戏代码)
│   ├── player.gd                     # 玩家控制器
│   ├── enemy.gd                      # 敌人 AI
│   ├── pickup.gd                     # 拾取物
│   ├── score_manager.gd              # 计分系统
│   ├── hud.gd                        # HUD 界面
│   └── ...
│
├── scenes/                           # Godot 场景文件
│   ├── Main.tscn                     # 主场景 (project.godot 入口)
│   ├── ForestPlatformer.tscn         # 森林平台跳跃
│   ├── GameScene.tscn                # 通用游戏场景
│   └── generated/                     # AI 生成的场景
│
├── autoload/                         # Godot Autoload 脚本
│   └── game_manager.gd              # 全局 GameManager 单例
│
├── addons/                           # Godot 插件
│   ├── gameforge/                    # GameForge 编辑器插件
│   │   ├── plugin.gd                 # 插件入口
│   │   ├── http_server.gd            # HTTP 服务 (端口 8765)
│   │   ├── websocket_client.gd       # WebSocket 客户端 (端口 8766)
│   │   ├── syntax_check.gd           # 语法校验
│   │   └── ui_panel.gd              # 编辑器 UI 面板
│   └── ai_native/                    # AI 原生控制器
│       ├── ai_controller.gd          # AIController (autoload)
│       └── ai_component.gd           # AI 组件
│
├── tests/                            # 测试
│   ├── unit/                         # 单元测试
│   └── conftest.py                   # pytest 配置
│
├── docker/                           # Docker 配置
│   ├── Dockerfile
│   └── prometheus.yml                # Prometheus 监控配置
│
├── config/templates/godot/           # Godot 代码模板 (7 个)
├── project.godot                     # Godot 项目配置
├── start_server.ps1                  # Windows 启动脚本
├── docker-compose.yml                # Docker Compose 配置
├── requirements.txt                  # Python 依赖
├── requirements-full.txt             # 完整依赖 (含可选组件)
├── pyproject.toml                    # 项目配置 (hatchling)
└── .env.example                      # 环境变量模板
```

---

## 示例输出

### 生成的 GDScript 代码

```gdscript
# player.gd — Player 玩家角色控制器
extends CharacterBody2D

@export var speed: float = 300.0
@export var jump_velocity: float = -400.0
@export var gravity_scale: float = 1.0

signal coin_collected()
signal damaged()

var _gravity: float = ProjectSettings.get_setting("physics/2d/default_gravity")
var _is_alive: bool = true
var _score_manager: Node = null

@onready var mesh: MeshInstance2D = get_node_or_null("Mesh")
@onready var collision_shape: CollisionShape2D = get_node_or_null("CollisionShape")

func _ready() -> void:
    _score_manager = get_tree().current_scene.get_node_or_null("ScoreManager")

func _physics_process(delta: float) -> void:
    if not _is_alive:
        return
    if not is_on_floor():
        velocity.y += _gravity * gravity_scale * delta
    if Input.is_action_just_pressed("jump") and is_on_floor():
        velocity.y = jump_velocity
    var direction := Input.get_axis("move_left", "move_right")
    if direction:
        velocity.x = direction * speed
        if mesh:
            mesh.flip_h = direction < 0
    else:
        velocity.x = move_toward(velocity.x, 0, speed)
    move_and_slide()
```

### 生成的场景文件

```ini
# ForestPlatformer.tscn — 32 个节点
[gd_scene load_steps=23 format=3]

[ext_resource type="Script" path="res://scripts/player.gd" id="1"]
[ext_resource type="Script" path="res://scripts/enemy.gd" id="3"]
[ext_resource type="Script" path="res://scripts/score_manager.gd" id="4"]

[node name="Player" type="CharacterBody2D" parent="."]
position = Vector2(-36.0, 0.0)
script = ExtResource("1")

[node name="Enemy1" type="CharacterBody2D" parent="."]
position = Vector2(18.0, 1.0)
script = ExtResource("3")

[node name="ScoreManager" type="Node" parent="."]
script = ExtResource("4")
```

### 评测报告

```json
{
  "project_name": "GameForge Project",
  "overall_score": 66.67,
  "metrics": [
    { "name": "compile_success", "value": 100, "unit": "%" },
    { "name": "task_completion", "value": 0, "unit": "%" },
    { "name": "godot_compatibility", "value": 100, "unit": "%" }
  ]
}
```

---

## 容错与降级

GameForge 在依赖服务不可用时自动降级，保证生成流程不中断：

| 场景 | 触发条件 | 降级策略 |
|------|----------|----------|
| LLM API 不可用 | 401/超时 | GameDesigner/SceneGenerator 跳过 LLM，使用 GDM + 模板生成 |
| Godot HTTP 不可用 | 端口 8765 无响应 | 直接写入 .tscn 文件到磁盘 |
| 脚本缺失 | 场景引用的 .gd 不存在 | 自动生成桩脚本 (script_stub) |
| 向量库未安装 | Qdrant 连接失败 | 禁用向量检索功能 |
| Redis 未安装 | Redis 连接失败 | 禁用 LLM 结果缓存 |
| Prometheus 未安装 | prometheus-client 缺失 | 禁用指标功能 |

---

## 测试

```bash
# 运行单元测试
pytest tests/unit/ -v

# 运行特定测试
pytest tests/unit/test_workflow.py -v

# 运行带覆盖率
pytest --cov=src --cov-report=html
```

---

## 路线图

- [x] Multi-Agent 协作引擎 (10 个 Agent)
- [x] Godot 4.x 代码与场景生成
- [x] LangGraph StateGraph 工作流
- [x] Web 界面 + SSE 流式反馈
- [x] Godot headless 编译校验
- [x] Godot 编辑器插件 (HTTP + WebSocket)
- [x] 量化评测体系
- [x] 审查↔重构对话协商
- [x] 反思回环
- [x] 消息总线 + Debugger 动态委派
- [x] Docker 容器化部署
- [x] 多 LLM Provider 支持 (5 家)
- [ ] 平台高度变化生成 (阶梯式 Y 坐标)
- [ ] 相机跟随玩家逻辑
- [ ] 桩脚本 ScoreManager 集成
- [ ] CI/CD 集成
- [ ] 知识库管理界面
- [ ] 多人协作

---

## 贡献指南

1. Fork 项目
2. 创建功能分支 (`git checkout -b feature/AmazingFeature`)
3. 提交更改 (`git commit -m 'Add AmazingFeature'`)
4. 推送到分支 (`git push origin feature/AmazingFeature`)
5. 创建 Pull Request

---

## 许可证

本项目采用 MIT 许可证 — 详见 [LICENSE](LICENSE) 文件

---

## 致谢

- [LangGraph](https://github.com/langchain-ai/langgraph) — Agent 状态图框架
- [AutoGen](https://github.com/microsoft/autogen) — 多 Agent 对话
- [FastAPI](https://fastapi.tiangolo.com/) — 异步 Web 框架
- [Godot Engine](https://godotengine.org/) — 游戏引擎

---

<div align="center">

**如果这个项目对你有帮助，请给个 Star ⭐**

</div>
