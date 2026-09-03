# GameForge 演进路线图（分阶段实施）

## 现状评估

| 模块 | 现状 | 成熟度 |
|------|------|--------|
| GDM (Game Design Model) | ✅ 已有结构化 JSON Schema | 高 |
| Recipe 系统 | ✅ P1 语义级复用，成功项目沉淀 | 高 |
| Memory 系统 | ✅ ProjectMemory + ConversationMemory | 中 |
| LangGraph 编排 | ✅ 条件路由 + 并行场景 | 中 |
| Sandbox 隔离 | ✅ 本地 Job Object + Docker 可选 | 高 |
| 自动验证 | ✅ gd-guard + 运行时冒烟 | 高 |

## 核心判断

GameForge 已度过 "Demo 阶段"，进入 **"自进化工厂"** 阶段。

下一步核心问题不是：
- ❌ "我有几个 Agent"
- ✅ "Agent 是否能持续积累经验，并越来越少修改"

## 演进三阶段

### Phase 1：减少无效调用（收益最大，实施难度低）

**目标**：在现有架构上做最小改动，获得最大效率提升。

| 优化项 | 实施难度 | 收益 | 说明 |
|--------|---------|------|------|
| Requirement Analyzer | 低 | 高 | 新增前置节点，标准化需求输入 |
| Game DSL  formalization | 低 | 高 | GDM → YAML DSL，减少 Agent 间自然语言传递 |
| Incremental Generation | 中 | 高 | 增量修改，避免全量重生成 |
| Agent Memory 增强 | 低 | 中 | 记忆结构化，提升复用精度 |

### Phase 2：提升生成质量（难度中等）

| 优化项 | 实施难度 | 收益 | 说明 |
|--------|---------|------|------|
| Evaluation Agent | 中 | 高 | 技术 + 游戏性 + 美术三维评估 |
| World Agent | 中 | 中 | 场景生成升级为世界构建 |
| Dynamic Task Graph | 高 | 中 | 固定 DAG → 动态依赖图 |

### Phase 3：自演化能力（难度高，长期价值）

| 优化项 | 实施难度 | 收益 | 说明 |
|--------|---------|------|------|
| Recipe Evolution | 高 | 高 | 配方自动进化和评分 |
| Bug Knowledge Base | 中 | 中 | 错误模式库，自动修复 |
| Auto Repair Memory | 中 | 中 | Debug 记忆持久化 |

## 实施顺序建议

```
Phase 1（当前）
├── 1.1 Requirement Analyzer
├── 1.2 Game DSL
├── 1.3 Incremental Generation
└── 1.4 Memory Enhancement

Phase 2（中期）
├── 2.1 Evaluation Agent
├── 2.2 World Agent
└── 2.3 Dynamic Task Graph

Phase 3（长期）
├── 3.1 Recipe Evolution
├── 3.2 Bug Knowledge Base
└── 3.3 Auto Repair Memory
```

## Phase 1 详细设计

### 1.1 Requirement Analyzer（需求解析层）

**位置**：LangGraph 入口，`game_designer` 之前

**输入**：用户自然语言需求
**输出**：结构化 Game Spec（DSL）

```yaml
# 输出示例
game:
  genre: tower_defense
  camera: 2D_top_down
  difficulty: medium

player:
  hp: 100
  actions: [plant, collect, upgrade]

enemy:
  types: [zombie, cone_zombie]
  wave_system: true

resources:
  - sun
  - coins

mechanics:
  - resource_generation
  - wave_attack
  - upgrade_tree
```

**实现**：
- 新建 `src/agents/requirement_analyzer/__init__.py`
- 作为 LangGraph 新节点 `requirement_analyzer`
- 输出 `game_spec` 字段到 state
- `game_designer` 读取 `game_spec` 而非原始需求

### 1.2 Game DSL（游戏定义模型）

**现状**：GDM 是 JSON，Agent 间传递自然语言 + JSON 混合

**目标**：统一为 YAML DSL

```yaml
# game.yaml
game:
  title: "植物大战僵尸"
  genre: tower_defense
  engine: godot4
  camera: 2D_top_down

player:
  hp: 100
  resources: [sun]

entities:
  - id: sunflower
    type: plant
    cost: 50
    hp: 100
    components: [sprite, collision, resource_generator]

  - id: zombie
    type: enemy
    hp: 100
    speed: 50
    components: [sprite, collision, pathfinding]

systems:
  - id: resource_system
    type: economy
    resources: [sun]

  - id: wave_system
    type: spawning
    max_waves: 10

scenes:
  - id: main_menu
    type: ui

  - id: level_1
    type: gameplay
    size: [1920, 1080]
```

**优势**：
- 所有 Agent 读写同一 DSL
- 减少 LLM 幻觉（结构化输入）
- 版本控制友好
- 人类可读可编辑

### 1.3 Incremental Generation（增量生成）

**现状**：每次生成全量文件

**目标**：基于影响分析，只生成变化的文件

**流程**：
```
用户: "增加 Boss 战"
    ↓
Impact Analyzer
    ↓
影响范围:
  - scenes/boss.tscn (新增)
  - scripts/boss.gd (新增)
  - scripts/combat.gd (修改)
  - scenes/level_1.tscn (修改)
    ↓
只重新生成/修改这 4 个文件
```

**依赖图**：
```python
# 简易实现：基于文件引用分析
class DependencyGraph:
    def analyze_impact(self, changed_files: List[str], all_files: List[str]) -> List[str]:
        # 解析 .tscn/.gd 中的引用关系
        # 返回受影响文件集合
```

### 1.4 Memory Enhancement（记忆增强）

**现状**：ProjectMemory 存储 decisions/patterns/errors/learnings

**增强**：
- 结构化记忆模板
- 跨项目记忆
- 记忆检索优化（向量化）

```python
class EnhancedProjectMemory:
    def record_generation(self, files: List[str], success: bool, iterations: int):
        """记录生成过程"""

    def record_bug_pattern(self, error: str, fix: str, affected_files: List[str]):
        """记录 Bug 模式"""

    def get_similar_solution(self, problem: str) -> Optional[Dict]:
        """检索相似问题的历史解决方案"""
```

## 实施优先级

| 周次 | 任务 | 预期收益 |
|------|------|---------|
| Week 1 | Requirement Analyzer | 减少 LLM 理解偏差 |
| Week 1-2 | Game DSL  formalization | 统一 Agent 间通信格式 |
| Week 2-3 | Incremental Generation | 减少 30-50% 生成时间 |
| Week 3-4 | Memory Enhancement | 提升跨会话复用率 |

## 预期效果

完成 Phase 1 后：

| 指标 | 当前 | 目标 |
|------|------|------|
| Agent 调用次数 | 5-8 次/需求 | 3-5 次/需求 |
| 生成成功率 | ~70% | >90% |
| 返工次数 | 2-3 次 | 0-1 次 |
| 配方复用率 | ~20% | >40% |

## 下一步行动

1. 确认 Phase 1 范围
2. 从 Requirement Analyzer 开始实施
3. 每个模块添加单元测试
4. 逐步替换现有流程
