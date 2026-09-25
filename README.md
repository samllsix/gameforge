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

GameForge 是一个基于 **Multi-Agent 架构** 的 Godot 游戏研发 AI 工具链，覆盖 **需求解析 → 策划 → 规划 → 代码/场景生成 → 审查/测试/修复 → 运行验证** 全链路。通过 LangGraph 状态图编排多个专业 Agent，实现从自然语言需求到可运行 Godot 项目的自动化生成。

### 核心价值

- **Multi-Agent 协作**：6 个核心图节点 Agent + 辅助能力（场景 / UI / 音频），审查、重构、测试、修复并入代码生成内部 Pipeline
- **Godot 4.x 专用**：生成符合规范的 GDScript (`.gd`) 与场景文件 (`.tscn`)
- **场景 IR 生成**：GameDesignModel → Scene IR → `.tscn`，与主工作流并行执行
- **Headless 编译校验**：Godot headless 模式自动校验脚本编译与场景完整性
- **Playtest 输入回放**：声明式动作脚本驱动真实玩家操作 + 进程内抓帧，"编译通过 ≠ 可玩"
- **VLM 视觉审查**：playtest 截图交给多模态模型按失败模式清单打分，高严重度问题触发修复闭环
- **安全闸门 gd-guard**：扫描生成脚本中的危险/禁止 API，拦截前先做一轮反馈重写
- **优雅降级**：LLM API 不可用时自动降级到模板生成，保证流程不中断；`GAMEFORGE_LLM_STUB=1` 可全链路离线冒烟
- **Web 界面**：内置 FastAPI + SSE 流式界面，实时查看 Agent 执行进度；支持预览帧、Web 构建、沙箱任务
- **量化评测**：产物级评测体系（运行时冒烟、playtest 证据、视觉审查、项目完整性 → `eval/summary.json`）

---

## 功能特性

### 1. Multi-Agent 协作引擎

```
用户需求
  │
  ▼
RequirementAnalyzer ──▶ Game Spec（结构化需求规格）
  │
  ▼
GameDesigner ──▶ GameDesignModel (GDM)
  │                  ├─ genre / camera_mode / core_loop
  │                  ├─ entities (Player, Enemy, Coin, ...)
  │                  ├─ environment (Ground, Platform, ...)
  │                  └─ mvp_scope / win_conditions / fail_conditions
  ▼
Planner ──▶ TaskPlan + asset_plan + genre_match
  │
  ▼
Orchestrator ⇄ CodeGenerator（Pipeline 内部 Phase）
  │              ├─ generate → review → refactor → test → fix → final_check
  │              └─ GDScript / UI / 配置产物，reducer 自动合并
  │
  ├─（并行）SceneGenerator ──▶ Scene IR ──▶ .tscn
  │
  ▼
后处理闭环（非图边）
  ├─ gd-guard 安全扫描（可触发一次重写复检）
  ├─ Godot headless 编译循环（最多 3 轮 fix）
  ├─ Runtime smoke test
  └─ Playtest 回放 + 帧证据 + VLM 视觉审查（可选）
```

图节点（`src/core/graph/workflow.py`）：

```
requirement_analyzer → game_designer → planner → orchestrator ⇄ code_generator → END
```

### 2. Agent 角色

| Agent | 职责 | 说明 |
|-------|------|------|
| RequirementAnalyzer | 自然语言 → Game Spec (DSL) | 失败时 `_fallback_spec` |
| GameDesigner | Spec → Game Design Model | 失败时 `_fallback_gdm` |
| Planner | GDM → 任务计划 / 资产计划 | 品类模板打分匹配 |
| Orchestrator | 按依赖调度 ready 任务 | 决定 continue / END |
| CodeGenerator | GDScript + 审查/重构/测试/修复 | 内部 Pipeline Phase |
| SceneGenerator | Scene IR → `.tscn` | 与主图并行；platformer/shooter/rpg 模板兜底 |

辅助能力（不在主图节点中，按配置触发）：

| 模块 | 路径 | 说明 |
|------|------|------|
| AudioGenerator | `src/agents/audio_generator/` | TTS 音频资产 |
| UIGenerator | `src/agents/ui_generator/` | UI 场景生成 |
| ArtDirector / GenreSpecs | `src/agents/art_director.py` 等 | 美术方向与品类规格 |

历史角色（code_reviewer / refactor / debugger / test_generator）已归并进 `CodeGeneratorAgent.run_pipeline()` 的 Phase，不再作为独立 LangGraph 节点。

### 3. Godot 引擎集成

- **Headless 编译校验**：`GodotEditor` 调用 `godot --headless --import` 验证脚本编译
- **场景构建器**：Scene IR → `GodotSceneBuilder` / `TscnWriter` 写入 `.tscn`
- **gd-guard**：脚本安全扫描，危险 API 拦截 + 有界反馈回路
- **Playtest**：输入回放 + 抓帧 + `report.json` 证据
- **Godot 编辑器插件**：`addons/gameforge/` 提供 HTTP 服务 (端口 8765) 与 WebSocket 客户端 (端口 8766)
- **AI 原生插件**：`addons/ai_native/` 提供 AI 控制器组件

### 4. MCP 工具链（可选）

`config/config.yaml` 中 `mcp.enabled` 控制，stdio / in_process 服务器：image、engine、asset、knowledge、test。

### 5. 沙箱与实时预览

- **Sandbox**：隔离工作区、任务级 modify / merge / rollback
- **Preview**：`/api/v1/preview/frame` 等接口提供运行帧预览
- **Export / Build**：项目导出、Web 构建、native 启停

### 6. 安全特性

- 多层中间件：安全头 / CORS / GZip / 请求体限制 / 输入校验 / 指标 / 并发限制 / 限流
- API Key 认证（`GAMEFORGE_API_KEYS` 环境变量）
- 输入注入检测（请求体与字符上限）
- loopback 地址强制约束（非 loopback 必须设置 API Key）

---

## 系统架构

```
┌─────────────────────────────────────────────────────────────┐
│                    GameForge Platform                       │
├─────────────────────────────────────────────────────────────┤
│  【Web 界面层】  index.html + app.js + SSE 流式反馈          │
│                   /app · /dashboard · /api/v1/generate_stream│
├─────────────────────────────────────────────────────────────┤
│  【API 层】  FastAPI + Uvicorn                              │
│    /api/v1/generate · generate_sync · generate_stream        │
│    /api/v1/plan · task · agents · history · preview ·        │
│    projects/…/export|builds|play|native · sandbox/…          │
│    /api/v1/ext/compile · import · eval                       │
├─────────────────────────────────────────────────────────────┤
│  【Multi-Agent 协调层】  LangGraph StateGraph               │
│    requirement_analyzer → game_designer → planner →          │
│    orchestrator ⇄ code_generator（+ 并行 scene_generator）   │
│    后处理：gd-guard / headless 编译循环 / smoke / playtest   │
├─────────────────────────────────────────────────────────────┤
│  【专业 Agent 层】  BaseAgent + 6 核心 + 辅助                │
│    RequirementAnalyzer · GameDesigner · Planner ·            │
│    Orchestrator · CodeGenerator (Pipeline) · SceneGenerator  │
│    AudioGenerator · UIGenerator                              │
├─────────────────────────────────────────────────────────────┤
│  【LLM 适配层】  多 Provider 支持                           │
│    Mimo · DeepSeek · 智谱GLM · Kimi · SenseNova · StepFun   │
├─────────────────────────────────────────────────────────────┤
│  【Godot 引擎层】  Headless 校验 + Playtest + gd-guard      │
│    GodotEditor · SceneBuilder · Playtest · VisualReview      │
│    GodotHttpClient (8765) · GodotWsClient (8766)             │
├─────────────────────────────────────────────────────────────┤
│  【工具与扩展】  MCP servers · Sandbox · Image/Audio · Eval  │
├─────────────────────────────────────────────────────────────┤
│  【数据与存储层】                                           │
│    MySQL（默认，SQLAlchemy）· Qdrant (可选) · Redis (可选)  │
├─────────────────────────────────────────────────────────────┤
│  【监控与日志】  structlog + LangSmith + Prometheus          │
└─────────────────────────────────────────────────────────────┘
```

---

## 技术栈

| 类别 | 技术 | 说明 |
|------|------|------|
| Agent 框架 | LangGraph | StateGraph 状态图驱动 |
| LLM (多 Provider) | Mimo / DeepSeek / GLM / Kimi / SenseNova / StepFun | `llm.models.{agent}` 按角色分配 |
| Web 框架 | FastAPI + Uvicorn | 异步 API + SSE 流式 |
| 游戏引擎 | **Godot 4.6** | GDScript + .tscn 场景 |
| 数据库 | MySQL（默认）/ PostgreSQL / SQLite | SQLAlchemy，`DATABASE_URL` 可切换 |
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
- MySQL 8+（默认任务历史存储；也可用 `DATABASE_URL` 切到 SQLite）

### 一键安装 Godot（可选）

版本钉死 + SHA512 校验 + 原子发布的安装器，保证 Agent/CI 拿到的引擎工具链可复现（`latest` 被明确拒绝）：

```bash
python tools/install_godot.py --version 4.6.3 --json
# 安装到 GAMEFORGE_GODOT_HOME（默认 tools/godot/godot-<版本>/），
# 并生成 godot_env.sh / godot_env.cmd（导出 GODOT_EDITOR_PATH）
python tools/install_godot.py --version 4.6.3 --check --reverify --json   # 只校验不下载
```

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
# 开发/测试
pip install -e ".[dev]"

# 4. 配置环境变量
cp .env.example .env
# 编辑 .env，填入 LLM API Key、数据库与 Godot 路径
```

### 配置 .env 文件

```ini
# LLM API Keys (至少配置一个；完整列表见 .env.example)
MIMO_API_KEY=your_mimo_key
DEEPSEEK_API_KEY=your_deepseek_key
STEP_API_KEY=your_step_key              # 图像生成
STEPFUN_TTS_API_KEY=your_stepfun_tts    # TTS

# Godot 引擎路径
GODOT_EDITOR_PATH=D:/godot/Godot_v4.6.3-stable_win64.exe/Godot_v4.6.3-stable_win64.exe
GODOT_PROJECT_PATH=D:/game_project

# 数据库（默认 MySQL；分项 env 优先于 DATABASE_URL）
DBMY_HOST=127.0.0.1
DBMY_PORT=3306
DBMY_USER=root
# DBMY_PASSWORD=...
DBMY_DATABASE=gameforge
# 或完整 URL：
# DATABASE_URL=sqlite:///./gameforge.db

# 应用配置（键名与 config/config.yaml 对应）
GAMEFORGE_ENV=development
GAMEFORGE_DEBUG=true
GAMEFORGE_HOST=127.0.0.1
GAMEFORGE_PORT=8000

# 可选：全链路 LLM 离线冒烟
# GAMEFORGE_LLM_STUB=1
```

> 各 API Key 的申请方式见 [docs/API_KEYS.md](docs/API_KEYS.md)。

### 启动服务

**方式一：PowerShell 脚本（推荐，Windows）**

```powershell
.\start_server.ps1
```

脚本自动加载 `.env` 环境变量、设置开发模式 API Key，使用 `.venv` 中的 Python 启动 Uvicorn。

**方式二：直接命令**

```bash
python -m uvicorn src.api.main:app --host 127.0.0.1 --port 8000
# 或
python -m src.cli serve
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
- **评测看板**：http://127.0.0.1:8000/dashboard
- **API 文档**：http://127.0.0.1:8000/docs
- **健康检查**：http://127.0.0.1:8000/health

---

## API 文档

### 核心接口

| 接口 | 方法 | 说明 |
|------|------|------|
| `/health` | GET | 健康检查 + 并发统计 |
| `/stats` · `/metrics` | GET | 运行统计 / Prometheus 指标 |
| `/app` · `/dashboard` | GET | Web 界面 / 评测看板 |
| `/api/v1/generate` | POST | 异步生成（返回 task_id，202） |
| `/api/v1/generate_sync` | POST | 同步生成（等待完成） |
| `/api/v1/generate_stream` | POST | SSE 流式生成（实时推送 Agent 状态） |
| `/api/v1/plan` | POST | 仅规划（不进入代码生成） |
| `/api/v1/task/{id}` | GET | 查询任务状态 |
| `/api/v1/task/{id}/wait` | POST | 等待任务完成 |
| `/api/v1/agents` | GET | 列出所有 Agent |
| `/api/v1/tasks` | GET | 历史任务列表 |
| `/api/v1/history` · `/history/{id}` · `/history/by_task/{id}` | GET | 生成历史 |
| `/api/v1/preview/frame` · `/preview/stats` | GET | 实时预览帧 / 统计 |
| `/api/v1/ext/compile` | POST | Godot 编译校验 |
| `/api/v1/ext/import` | POST | Godot 项目导入 |
| `/api/v1/ext/eval` | POST | 代码评测 |
| `/api/v1/projects/{id}/export` | POST | 项目导出 |
| `/api/v1/projects/{id}/builds/web` · `/play/build` | POST/GET | Web 构建 |
| `/api/v1/projects/{id}/native/*` · `/play/native/*` | GET/POST | 原生运行启停 / 日志 |
| `/api/v1/sandbox/{id}/*` | POST/GET/DELETE | 沙箱工作区 / 任务合并回滚 |

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
# 安装后可直接使用 gameforge 命令（或 python -m src.cli）
gameforge generate --input game_requirements.txt --output output
gameforge workflow
gameforge status
gameforge serve --host 127.0.0.1 --port 8000
gameforge eval --project GameForge Project
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
│   │   ├── requirement_analyzer/     # 需求解析 Agent
│   │   ├── game_designer/            # 游戏策划 Agent
│   │   ├── planner/                  # 规划 Agent
│   │   ├── orchestrator/             # 编排 Agent
│   │   ├── code_generator/           # 代码生成 + 内部 Pipeline
│   │   │   └── godot_templates.py    # GDScript 运行时模板
│   │   ├── scene_generator/          # 场景生成 Agent
│   │   ├── audio_generator/          # TTS 音频 Agent
│   │   ├── ui_generator/             # UI 生成 Agent
│   │   ├── art_director.py           # 美术方向
│   │   ├── genre_specs.py            # 品类规格
│   │   ├── scene_ir.py               # 场景 IR 定义
│   │   └── scene_templates.py        # 场景模板
│   ├── core/                         # 核心模块
│   │   ├── paths.py                  # 仓库 I/O 路径 (project_id/run_id)
│   │   ├── gdm.py                    # GDM 条目归一化
│   │   ├── recipes.py                # 已验证配方库 (语义级复用)
│   │   ├── incremental.py            # 增量生成
│   │   ├── concurrency.py            # 异步任务队列
│   │   ├── graph/workflow.py         # LangGraph 状态图
│   │   ├── state/game_state.py       # GameDevState TypedDict + reducers
│   │   ├── state/bus.py              # 消息总线
│   │   ├── memory/                   # 记忆管理
│   │   ├── knowledge/                # 知识库查询
│   │   ├── dsl/                      # 规格 DSL
│   │   └── tools/                    # 工具集
│   ├── adapters/                     # LLM 适配器
│   ├── engine/godot/                 # Godot 工具链
│   │   ├── __init__.py               # GodotEditor + 编译校验
│   │   ├── scene_builder.py          # 场景构建器
│   │   ├── gd_guard.py               # 脚本安全扫描
│   │   ├── playtest.py               # playtest 回放 + 帧证据
│   │   ├── visual_review.py          # VLM 视觉审查
│   │   └── godot_http_client.py      # 编辑器插件客户端
│   ├── sandbox/                      # 沙箱执行
│   ├── db/                           # 数据库 (MySQL/PG/SQLite)
│   ├── eval/                         # 评测体系
│   ├── mcp/                          # MCP servers / adapters
│   ├── image/ · models/              # 图像与音频模型抽象
│   └── utils/                        # 工具函数
│       ├── llm_client.py             # 统一 LLM 客户端 (熔断+重试+stub)
│       ├── unified_validator.py      # 统一代码校验
│       └── logger.py                 # structlog 日志
│
├── config/                           # 配置文件
│   ├── config.yaml                   # 主配置 (LLM/Agent/MCP/Sandbox/Playtest)
│   ├── prompts/                      # Prompt 模板 (~12 个)
│   │   ├── global_system.txt         # 全局约束 (禁止 Unity, 只生成 Godot)
│   │   ├── code_generator_system.txt
│   │   └── ...
│   └── templates/godot/              # Godot 代码模板 (7 个)
│
├── static/                           # Web 前端
├── scripts/ · scenes/ · autoload/    # 本仓库 Godot 侧资源
├── addons/                           # Godot 插件 (gameforge / ai_native)
├── projects/ · workspace/            # 生成项目与工作区产物
├── tests/                            # unit / integration / e2e
├── tools/install_godot.py            # Godot 版本钉死安装器
├── docs/                             # 设计与 API 文档
├── project.godot                     # Godot 项目配置
├── start_server.ps1                  # Windows 启动脚本
├── docker-compose.yml                # Docker Compose 配置
├── requirements.txt                  # Python 依赖
├── pyproject.toml                    # 项目配置 (hatchling + 工具链)
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
| LLM API 不可用 | 401/超时/熔断 | GameDesigner/SceneGenerator 等跳过 LLM，使用 GDM + 模板生成 |
| LLM stub 模式 | `GAMEFORGE_LLM_STUB=1` | 全部 LLM 调用立即拒绝，走确定性模板 |
| Godot HTTP 不可用 | 端口 8765 无响应 | 直接写入 `.tscn` 文件到磁盘 |
| 脚本缺失 | 场景引用的 `.gd` 不存在 | 自动生成桩脚本 (script_stub) |
| gd-guard 异常 | 扫描器不可用 | 失败开放，不阻塞主流程 |
| 向量库未安装 | Qdrant 连接失败 | 禁用向量检索功能 |
| Redis 未安装 | Redis 连接失败 | 禁用 LLM 结果缓存 |
| Prometheus 未安装 | prometheus-client 缺失 | 禁用指标功能 |

---

## 测试

```bash
# 运行单元测试
pytest tests/unit/ -v

# 运行特定测试
pytest tests/unit/test_gdm.py -v

# 跳过慢测试
pytest -m "not slow"

# 运行带覆盖率
pytest --cov=src --cov-report=html
```

---

## 路线图

- [x] Multi-Agent 协作引擎（6 核心图节点 + Pipeline 内审查/修复）
- [x] Godot 4.x 代码与场景生成
- [x] LangGraph StateGraph 工作流
- [x] Web 界面 + SSE 流式反馈
- [x] Godot headless 编译校验
- [x] Playtest 输入回放 + 帧证据
- [x] VLM 视觉审查（可选开关）
- [x] gd-guard 安全闸门
- [x] Godot 编辑器插件 (HTTP + WebSocket)
- [x] 量化评测体系
- [x] MCP 工具服务器（image/engine/asset/knowledge/test）
- [x] 沙箱工作区与 Web/Native 构建接口
- [x] Docker 容器化部署
- [x] 多 LLM Provider 支持（6 家）
- [ ] 知识库管理界面
- [ ] CI/CD 集成
- [ ] 多人协作

---

## 贡献指南

1. Fork 项目
2. 创建功能分支 (`git checkout -b feature/AmazingFeature`)
3. 提交更改 (`git commit -m 'Add AmazingFeature'`)
4. 推送到分支 (`git push origin feature/AmazingFeature`)
5. 创建 Pull Request

代码约定简述：

- Python 3.11+，全 `async`；阻塞的 Godot 调用用 `run_in_executor`
- `structlog` 结构化日志；中文模块 docstring + Google 风格 Args/Returns
- LLM 调用必须有确定性兜底（模板/规则），禁止流程硬失败
- 生成 GDScript：只许 Godot 4.x，PascalCase `class_name`、`snake_case` 成员、强类型注解
- 工具链：`black` / `isort` / `ruff`（line-length 88）；`pytest` `asyncio_mode=auto`

---

## 许可证

本项目采用 MIT 许可证

---

## 致谢

- [LangGraph](https://github.com/langchain-ai/langgraph) — Agent 状态图框架
- [FastAPI](https://fastapi.tiangolo.com/) — 异步 Web 框架
- [Godot Engine](https://godotengine.org/) — 游戏引擎

---

<div align="center">

**如果这个项目对你有帮助，请给个 Star ⭐**

</div>
