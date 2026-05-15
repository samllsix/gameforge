# GameForge — 游戏研发全流程AI Agent协作平台

## Project Overview

GameForge是一个覆盖策划→开发→测试→修复全链路的Multi-Agent系统，让AI Agent能理解游戏策划文档、生成代码、自动测试、自我调试。

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    GameForge Platform                    │
├─────────────────────────────────────────────────────────┤
│  【多角色协作层】                                        │
│  策划Agent ←→ 程序Agent ←→ 美术Agent ←→ QA Agent      │
├─────────────────────────────────────────────────────────┤
│  【Multi-Agent协调层】(LangGraph + AutoGen)              │
│  Orchestrator Agent (规划与调度)                         │
├─────────────────────────────────────────────────────────┤
│  【专业Agent层】                                         │
│  Code Generator | Test Generator | Refactor | Review    │
├─────────────────────────────────────────────────────────┤
│  【工具与执行层】                                        │
│  Unity/Unreal Editor API | Git | CI/CD | 沙箱执行环境   │
├─────────────────────────────────────────────────────────┤
│  【记忆与知识层】                                        │
│  向量数据库 | 项目规范库 | 历史经验库                    │
└─────────────────────────────────────────────────────────┘
```

## Tech Stack

- **Agent Framework**: LangGraph (状态管理) + AutoGen (多Agent对话)
- **LLM**: Claude 3.5 Sonnet (代码生成) + GPT-4 (任务规划)
- **Code Analysis**: Tree-sitter (AST解析)
- **Vector DB**: Qdrant
- **Web Framework**: FastAPI
- **Database**: SQLAlchemy + SQLite/PostgreSQL
- **Cache**: Redis
- **Monitoring**: LangSmith + Prometheus

## Project Structure

```
game_project/
├── src/                          # 源代码
│   ├── agents/                   # Agent实现
│   │   ├── orchestrator/         # 编排Agent
│   │   ├── planner/              # 规划Agent
│   │   ├── code_generator/       # 代码生成Agent
│   │   ├── code_reviewer/        # 代码审查Agent
│   │   ├── test_generator/       # 测试生成Agent
│   │   ├── debugger/             # 调试Agent
│   │   └── refactor/             # 重构Agent
│   ├── core/                     # 核心模块
│   │   ├── state/                # 状态管理
│   │   ├── graph/                # LangGraph图定义
│   │   ├── tools/                # 工具集
│   │   └── memory/               # 记忆系统
│   ├── engine/                   # 游戏引擎集成
│   │   ├── unity/                # Unity相关
│   │   ├── unreal/               # Unreal相关
│   │   ├── compiler/             # 编译器接口
│   │   └── sandbox/              # 沙箱执行
│   ├── eval/                     # 评测体系
│   │   ├── metrics/              # 评测指标
│   │   ├── test_cases/           # 测试用例
│   │   └── dashboard/            # 评测面板
│   ├── api/                      # API层
│   │   ├── routes/               # 路由
│   │   ├── schemas/              # 数据模型
│   │   └── middleware/           # 中间件
│   └── utils/                    # 工具函数
├── config/                       # 配置文件
│   ├── config.yaml               # 主配置
│   ├── prompts/                  # Prompt模板
│   └── templates/                # 代码模板
├── tests/                        # 测试
│   ├── unit/                     # 单元测试
│   ├── integration/              # 集成测试
│   └── e2e/                      # 端到端测试
├── data/                         # 数据
│   ├── knowledge_base/           # 知识库
│   ├── eval_datasets/            # 评测数据集
│   └── examples/                 # 示例
├── docker/                       # Docker配置
├── scripts/                      # 脚本
└── docs/                         # 文档
```

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

### Running Tests

```bash
# Run all tests
pytest

# Run specific test types
pytest tests/unit/
pytest tests/integration/
pytest -m "not slow"

# With coverage
pytest --cov=src --cov-report=html
```

### Code Quality

```bash
# Format code
black src/ tests/
isort src/ tests/

# Lint
ruff check src/ tests/
mypy src/
```

## Key Concepts

### Agent State (LangGraph)

```python
class GameDevState(TypedDict):
    task_plan: List[Task]
    code_generated: Dict[str, str]
    test_results: TestReport
    fix_history: List[FixRecord]
```

### Core Flow

1. 策划提交文档 → Planner解析并拆解任务
2. Code Generator生成代码 → Reviewer审查
3. Test Generator创建测试 → 沙箱执行
4. 失败则触发Debugger → 分析错误 → 自动修复
5. 循环直到通过或人工介入

### Evaluation Metrics

- **代码可用率**: 首次通过率、编译通过率、功能完成度
- **代码质量**: 圈复杂度、命名规范、设计模式、性能
- **任务完成效率**: 修复轮次、人工介入次数、端到端时间

## Prompt Templates

Prompt模板存放在 `config/prompts/` 目录下，使用Jinja2格式：

- `planner_system.txt` - 规划Agent系统提示
- `code_generator_system.txt` - 代码生成Agent系统提示
- `reviewer_system.txt` - 代码审查Agent系统提示
- `debugger_system.txt` - 调试Agent系统提示

## Common Commands

```bash
# Start specific agent
python -m src.agents.code_generator --task "Create player movement script"

# Run evaluation
python -m src.eval.run --dataset data/eval_datasets/

# Generate code from design doc
python -m src.cli generate --input docs/design.md --engine unity

# Run multi-agent workflow
python -m src.cli workflow --config config/workflow.yaml
