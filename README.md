# GameForge — 游戏研发全流程AI Agent协作平台

<div align="center">

![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)
![LangGraph](https://img.shields.io/badge/LangGraph-Latest-green.svg)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-teal.svg)
![Unity](https://img.shields.io/badge/Unity-2022+-black.svg)
![License](https://img.shields.io/badge/License-MIT-yellow.svg)

**让AI Agent帮你完成游戏开发全流程**

[快速开始](#快速开始) • [功能特性](#功能特性) • [系统架构](#系统架构) • [使用指南](#使用指南)

</div>

---

## 项目简介

GameForge 是一个基于 **Multi-Agent架构** 的游戏研发AI协作平台，覆盖 **策划→开发→测试→修复** 全链路。通过多个专业AI Agent的协作，实现从需求文档到可运行游戏代码的自动化生成。

### 核心价值

- 🤖 **智能协作**：多个专业Agent分工协作，模拟真实游戏开发团队
- 🎮 **游戏专用**：深度理解Unity/Unreal引擎，生成符合规范的游戏代码
- 🔄 **自动闭环**：生成→测试→修复的自动迭代，持续优化代码质量
- 📊 **量化评测**：多维度评测体系，客观衡量生成代码质量

---

## 功能特性

### 1. Multi-Agent协作引擎

```
┌─────────────────────────────────────────────────────────┐
│  策划Agent ←→ 程序Agent ←→ 美术Agent ←→ QA Agent      │
└─────────────────────────────────────────────────────────┘
```

- **规划Agent**：解析需求文档，生成结构化任务计划
- **代码生成Agent**：生成Unity C# / Unreal C++代码
- **代码审查Agent**：检查代码质量、规范符合性
- **测试生成Agent**：自动生成单元测试和集成测试
- **调试Agent**：分析错误并生成修复方案

### 2. 游戏专用代码生成

- ✅ 理解GameObject、Component、Prefab等概念
- ✅ 遵循Unity/Unreal命名规范
- ✅ 支持ECS、MVC等架构模式
- ✅ 自动添加注释和文档

### 3. 自动化测试闭环

```
代码生成 → 测试生成 → 测试执行 → 错误分析 → 自动修复
    ↑                                          ↓
    └──────────────── 循环迭代 ←───────────────┘
```

### 4. 量化评测体系

| 评测维度 | 指标 |
|----------|------|
| 代码可用率 | 首次通过率、编译通过率、功能完成度 |
| 代码质量 | 圈复杂度、命名规范、设计模式、性能 |
| 任务效率 | 修复轮次、人工介入次数、端到端时间 |

---

## 系统架构

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

---

## 技术栈

| 类别 | 技术 |
|------|------|
| Agent框架 | LangGraph + AutoGen |
| LLM模型 | Mimo大模型 |
| Web框架 | FastAPI |
| 代码分析 | Tree-sitter |
| 向量数据库 | Qdrant |
| 监控 | LangSmith + Prometheus |

---

## 快速开始

### 环境要求

- Python 3.11-3.13
- Unity 2022+ (可选)

### 安装步骤

```bash
# 1. 克隆项目
git clone https://github.com/yourusername/gameforge.git
cd gameforge

# 2. 创建虚拟环境
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 3. 安装依赖
pip install -r requirements.txt

# 4. 配置环境变量
cp .env.example .env
# 编辑 .env 文件，填入API密钥

# 5. 验证安装
python -m src.cli status
```

### 使用示例

```bash
# 创建需求文件
echo "创建一个2D平台跳跃游戏，包含玩家移动、跳跃和计分系统" > game_requirements.txt

# 生成代码
python -m src.cli generate --input game_requirements.txt --output output

# 查看结果
tree output/
```

---

## 使用指南

### 命令行工具

```bash
# 生成代码
python -m src.cli generate --input <需求文件> --output <输出目录>

# 运行工作流
python -m src.cli workflow

# 查看状态
python -m src.cli status
```

### API服务

```bash
# 启动API服务器
python -m src.api.main

# 访问API文档
# http://localhost:8000/docs

# Docker Compose 默认映射到宿主机 8001
# 如端口被占用，复制 .env.example 为 .env 后修改 GAMEFORGE_HOST_PORT
# http://localhost:8001/docs
```

### 代码评测

```bash
# 运行评测脚本
python scripts/evaluate.py output

# 导入到Unity
python scripts/import_to_unity.py D:/Unity/GameForge
```

---

## 项目结构

```
game_project/
├── src/                          # 源代码
│   ├── agents/                   # Agent实现
│   │   ├── orchestrator/         # 编排Agent
│   │   ├── planner/              # 规划Agent
│   │   ├── code_generator/       # 代码生成Agent
│   │   ├── code_reviewer/        # 代码审查Agent
│   │   ├── test_generator/       # 测试生成Agent
│   │   └── debugger/             # 调试Agent
│   ├── core/                     # 核心模块
│   │   ├── state/                # 状态管理
│   │   ├── graph/                # LangGraph图定义
│   │   └── tools/                # 工具集
│   ├── api/                      # API层
│   └── utils/                    # 工具函数
├── config/                       # 配置文件
│   ├── config.yaml               # 主配置
│   └── prompts/                  # Prompt模板
├── scripts/                      # 脚本工具
├── tests/                        # 测试
├── output/                       # 生成代码输出
├── logs/                         # 运行日志
├── CLAUDE.md                     # 项目文档
├── requirements.txt              # Python依赖
└── pyproject.toml                # 项目配置
```

---

## 示例输出

### 生成的代码

```csharp
// PlayerController.cs
public class PlayerController : MonoBehaviour
{
    [SerializeField] private float _moveSpeed = 5f;
    [SerializeField] private float _jumpForce = 10f;

    private Rigidbody2D _rb;
    private bool _isGrounded;

    private void Awake()
    {
        _rb = GetComponent<Rigidbody2D>();
    }

    public void Move()
    {
        _rb.velocity = new Vector2(_moveInput * _moveSpeed, _rb.velocity.y);
    }

    public void Jump()
    {
        _rb.velocity = new Vector2(_rb.velocity.x, _jumpForce);
    }
}
```

### 评测报告

```
============================================================
GameForge 代码评测报告
============================================================
[文件统计]
  总文件数: 4
  源代码文件: 2
  测试文件: 2

[代码质量]
  [OK] 命名空间
  [OK] 代码注释
  [OK] 代码分组
  [OK] 空引用检查
  [OK] 序列化字段
  [OK] 命名规范

  质量得分: 100%

[评测结果]
  EXCELLENT (优秀)
============================================================
```

---

## 路线图

- [x] Multi-Agent协作引擎
- [x] Unity代码生成
- [x] 自动化测试生成
- [x] 代码评测体系
- [ ] Unreal引擎支持
- [ ] 可视化界面
- [ ] CI/CD集成
- [ ] 知识库管理
- [ ] 多人协作

---

## 贡献指南

欢迎贡献代码！请遵循以下步骤：

1. Fork 项目
2. 创建功能分支 (`git checkout -b feature/AmazingFeature`)
3. 提交更改 (`git commit -m 'Add AmazingFeature'`)
4. 推送到分支 (`git push origin feature/AmazingFeature`)
5. 创建 Pull Request

---

## 许可证

本项目采用 MIT 许可证 - 详见 [LICENSE](LICENSE) 文件

---

## 联系方式

- 项目链接: https://github.com/yourusername/gameforge
- 问题反馈: https://github.com/yourusername/gameforge/issues

---

## 致谢

- [LangGraph](https://github.com/langchain-ai/langgraph) - Agent框架
- [AutoGen](https://github.com/microsoft/autogen) - 多Agent对话
- [FastAPI](https://fastapi.tiangolo.com/) - Web框架
- [Unity](https://unity.com/) - 游戏引擎

---

<div align="center">

**如果这个项目对你有帮助，请给个 Star ⭐**

</div>
