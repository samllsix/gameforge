# GameForge — Godot 游戏研发全流程 AI Agent 工具链

## Project Overview

GameForge 是一个专注于 Godot 引擎的 AI 游戏开发工具链，覆盖策划→开发→测试→修复全链路。通过 Multi-Agent 系统让 AI Agent 能理解游戏策划文档、生成 GDScript 代码、构建 Godot 场景、自动测试和自我调试。

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    GameForge Platform                    │
├─────────────────────────────────────────────────────────┤
│  【Godot 编辑器插件层】(GDScript)                        │
│  UI Panel | HTTP Server | WebSocket Client              │
├─────────────────────────────────────────────────────────┤
│  【Multi-Agent协调层】(LangGraph)                        │
│  Orchestrator Agent (规划与调度)                         │
├─────────────────────────────────────────────────────────┤
│  【专业Agent层】                                         │
│  Code Generator | Test Generator | Refactor | Review    │
│  Game Designer | Scene Generator | Debugger             │
├─────────────────────────────────────────────────────────┤
│  【工具与执行层】                                        │
│  Godot Editor API | HTTP/WebSocket | 沙箱执行环境       │
├─────────────────────────────────────────────────────────┤
│  【记忆与知识层】                                        │
│  向量数据库 | 项目规范库 | 历史经验库                    │
└─────────────────────────────────────────────────────────┘
```

## Tech Stack

- **Agent Framework**: LangGraph (状态管理)
- **LLM**: 多 Provider 支持（Mimo, DeepSeek, 智谱, Kimi 等）
- **Code Analysis**: GDScript 语法检查
- **Vector DB**: Qdrant
- **Web Framework**: FastAPI
- **Database**: SQLAlchemy + SQLite/PostgreSQL
- **Cache**: Redis
- **Monitoring**: Prometheus
- **目标引擎**: Godot 4.x (兼容 3.x)

## Project Structure

```
game_project/
├── src/                          # 源代码
│   ├── agents/                   # Agent实现
│   │   ├── orchestrator/         # 编排Agent
│   │   ├── planner/              # 规划Agent
│   │   ├── code_generator/       # 代码生成Agent (GDScript)
│   │   ├── code_reviewer/        # 代码审查Agent
│   │   ├── test_generator/       # 测试生成Agent (GUT)
│   │   ├── debugger/             # 调试Agent
│   │   ├── refactor/             # 重构Agent
│   │   ├── game_designer/        # 游戏设计Agent
│   │   └── scene_generator/      # 场景生成Agent (.tscn)
│   ├── core/                     # 核心模块
│   │   ├── state/                # 状态管理
│   │   ├── graph/                # LangGraph图定义
│   │   ├── tools/                # 工具集 (GDScript)
│   │   └── memory/               # 记忆系统
│   ├── engine/                   # 游戏引擎集成
│   │   └── godot/                # Godot 引擎
│   │       ├── __init__.py       # GodotEditor (CLI模式)
│   │       ├── godot_http_client.py  # HTTP 客户端
│   │       ├── godot_ws_client.py    # WebSocket 客户端
│   │       ├── project_generator.py  # 项目生成器
│   │       ├── scene_builder.py      # 场景构建器
│   │       └── tscn_writer.py        # .tscn 序列化器
│   ├── eval/                     # 评测体系
│   ├── api/                      # API层 (FastAPI)
│   └── utils/                    # 工具函数
├── godot_plugin/                 # Godot 编辑器插件
│   └── addons/gameforge/
│       ├── plugin.cfg            # 插件配置
│       ├── plugin.gd             # 插件入口
│       ├── http_server.gd        # 内嵌 HTTP 服务器
│       ├── websocket_client.gd   # WebSocket 客户端
│       ├── ui_panel.gd           # UI 面板
│       └── settings.gd           # 设置管理
├── config/                       # 配置文件
│   ├── config.yaml               # 主配置
│   ├── prompts/                  # Prompt模板 (GDScript)
│   └── templates/godot/          # GDScript 模板
├── static/                       # 前端文件
├── tests/                        # 测试
├── data/                         # 数据
└── docs/                         # 文档
```

## Key Concepts

### Agent State (LangGraph)

```python
class GameDevState(TypedDict):
    task_plan: List[Task]
    code_generated: Dict[str, str]  # {路径: GDScript内容}
    test_results: TestReport
    fix_history: List[FixRecord]
    scene_description: Dict         # Godot 场景描述
    game_design_model: Dict         # GDM
```

### Core Flow

1. 用户提交需求 → GameDesigner 生成 GDM
2. Planner 解析 GDM → 生成任务计划
3. CodeGenerator 生成 GDScript 代码 → Reviewer 审查
4. SceneGenerator 生成 Godot 场景 (.tscn)
5. TestGenerator 生成 GUT 测试用例
6. 失败则触发 Debugger → 分析错误 → 自动修复
7. 循环直到通过或人工介入

### GDScript 代码生成

- 文件路径: `scripts/*.gd`
- 命名规范: snake_case (函数/变量), PascalCase (class_name), UPPER_SNAKE_CASE (常量)
- 生命周期: `_ready()`, `_process()`, `_physics_process()`
- 节点引用: `$NodePath`, `@onready`
- 信号: `signal name(params)`
- 类型注解: GDScript 2.0 强类型

### Godot 场景生成

- 文件格式: `.tscn` (Godot 文本场景格式)
- 节点类型: CharacterBody2D, StaticBody2D, Area2D, Sprite2D 等
- 碰撞: CollisionShape2D + Shape2D
- 动画: AnimatedSprite2D + SpriteFrames

## Development Workflow

### Setup

```bash
# Create virtual environment
python -m venv venv
source venv/bin/activate  # or venv\Scripts\activate on Windows

# Install dependencies
pip install -r requirements.txt
pip install -e ".[dev]"

# Copy and configure environment
cp .env.example .env
# Edit .env with your API keys

# Initialize database
python -m src.db.init

# Run development server
python -m src.api.main
```

### Godot 插件安装

1. 复制 `godot_plugin/addons/gameforge/` 到你的 Godot 项目的 `addons/` 目录
2. 在 Godot 编辑器中启用插件：项目 → 项目设置 → 插件 → GameForge → 启用
3. 底部面板会出现 GameForge 标签

### Running Tests

```bash
pytest
pytest tests/unit/
pytest tests/integration/
pytest -m "not slow"
```

### Code Quality

```bash
black src/ tests/
isort src/ tests/
ruff check src/ tests/
mypy src/
```

## Common Commands

```bash
# Start specific agent
python -m src.agents.code_generator --task "Create player movement script"

# Run evaluation
python -m src.eval.run --dataset data/eval_datasets/

# Generate code from design doc
python -m src.cli generate --input docs/design.md --engine godot

# Run multi-agent workflow
python -m src.cli workflow --config config/workflow.yaml
```
