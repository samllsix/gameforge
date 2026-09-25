# M2 · Agent 层调研（三层诊断）

> **调研范围**：`src/agents/` 全部 8 个 Agent 文件 + `src/core/graph/workflow.py` 中与 Agent 交互的部分。规模、接线状态、调用点均经代码核对。
>
> **一句话结论**：Agent 层与 Workflow 层的**职责划分是对的**（前者产内容、后者管流程），但两边各有病症且**病症不同**——Agent 层是**编制残缺**（该到岗的人不在），Workflow 层是**越界 + 重复**（干了 8 类执行活，还和 orchestrator 重复了派活）。因此改造必须分两步：先补编制，再瘦身。

---

## 1. 设计理念：三层各司其职

原设计（README 亦如此宣传）是一个三层结构：

```
第一层  工作流编排（workflow.py）     管"什么时候、按什么顺序、失败了怎么办"
            ↓ 只通过 execute() / plan() / run_pipeline() 调用
第二层  Agent 层（src/agents/）       管"这一步具体产出什么内容"
            ↓ 每个 Agent 一个类、一份 prompt、一套 LLM 配置
第三层  LLM / Godot / 文件系统        被 Agent 调用的外部能力
```

**这个划分是正确的，不建议推倒重来。** 改的是接线，不是设计。

对应到"软件团队"的比喻：编排层是施工表，Agent 层是各工种的手艺，LLM 是他们手里的工具。

---

## 2. 三层诊断

### 第一层 / Agent 层：编制残缺（该到岗的人不在）

**单个 Agent 的手艺并不差**——`game_designer` 真能产出 GDM，`planner` 真能拆出带依赖的任务计划，`code_generator` 真能写 GDScript。问题在于**编制表与实际到岗情况对不上**：

| 设计里的角色 | 对应文件 | 规模 | 到岗情况 |
|---|---|---|---|
| 需求分析师 | `requirement_analyzer/` | 181 行 | ✅ 在岗，归 leader 调度 |
| 游戏设计师 | `game_designer/` | 480 行 | ✅ 在岗 |
| 计划员 | `planner/` | 491 行 | ✅ 在岗 |
| 技术 leader | `orchestrator/` | 159 行 | 🔇 **在编不在岗**——实例化了但从未调用 |
| 代码工人 | `code_generator/` | **1319 行** | ✅ 在岗，但一人干了 6 个人的活 |
| — 代码审查 | 无独立文件 | — | 🔇 折叠为 `_review()` |
| — 重构 | 无独立文件 | — | 🔇 折叠为 `_refactor()` |
| — 测试生成 | 无独立文件 | — | 🔇 折叠为 `_generate_tests()` |
| — 终审 | 无独立文件 | — | 🔇 折叠为 `_final_check()` |
| — 调试 | 无独立文件 | — | 🔇 折叠为 `fix_code()` |
| UI 专家 | `ui_generator/` | **759 行** | ❌ **零接线**（含 `ui_tools.py` 474 行 + 700 行测试，全部空转） |
| 音频专家 | `audio_generator/` | 404 行 | ❌ **零接线** |
| 场景工人 | `scene_generator/` | 766 行 | ⚠️ 在岗但走**侧门**（图外并行），不归 leader 调度 |

**结论：这个团队只有 3 个独立人格（代码工人、场景工、以及被折叠的审核组），其余都是幻影。**

代码证据：`src/core/state/game_state.py:60` 的 `AgentType` 枚举只有 6 个值，注释直言"精简为 6 个核心 Agent"——是主动收缩，不是漏写。

### 第二层 / Workflow 层：越界 + 重复

`workflow.py` 共 **39 个私有方法**，其中真正属于"编排"的只有 1 个。剩下 30 多个是**本不该由编排层承担的执行逻辑**：

| workflow 自己扛的执行活 | 方法位置 | 本该归谁 |
|---|---|---|
| 派活（且与 orchestrator 重复） | `_orchestrator_node` :368 | `OrchestratorAgent`（已存在却空转） |
| 安全扫描 + 拦截后重生成 | `_gd_guard_scan` :127 | 独立"安全审查"角色 |
| 运行时冒烟 | `_runtime_smoke_test` :876 | 独立"测试"角色 |
| playtest 试玩回放 | `_playtest` :975 | 独立"试玩"角色 |
| 编译 / 构建 / 落盘 | `_try_godot_pipeline` :1116 | 独立"构建"角色 |
| 后处理（校验/补产物/评测/记忆） | `_post_process` :1447 | 拆给多个角色 |
| 配方命中与沉淀 | `_run_recipe` :1534 / `_bake_if_verified` :1604 | 独立"资产管理"角色 |
| 沙箱与路径解析 | `_sandbox_project_config` :403 / `_godot_project_config` :419 | 独立"环境"角色 |

**越界的直接证据**：workflow 直接伸手调 `code_generator.fix_code()` 达 **5 处**（:168 / :733 / :861 / :950 / :1068）——安全闸门、编译、冒烟、playtest 四条路径各自直接调 Agent 的公开方法，完全绕开了"节点"这层抽象。

**重复的直接证据**：

```python
# workflow.py:48  只是 new 了出来
self.orchestrator = OrchestratorAgent(config)

# workflow.py:368  真干活的是自己那份，零引用 self.orchestrator
ready_tasks = self._get_all_ready_tasks(task_plan)
```

`grep "self.orchestrator" src/core/graph/workflow.py` 只命中 :48 一处（实例化），没有任何 `self.orchestrator.execute(...)`。

### 第三层 / 折叠角色：无独立人格的代价

README 宣称的 10 个 Agent 中，5 个审核类角色被折叠进 `code_generator`（1319 行）内部：

```
run_pipeline()  ← src/agents/code_generator/__init__.py:962
   ├─ _generate()
   ├─ _review()          :1078   ← 原 code_reviewer
   ├─ _refactor()        :1163   ← 原 refactor
   ├─ _generate_tests()  :1190   ← 原 test_generator
   ├─ _final_check()     :1238   ← 原 main_reviewer
   └─ fix_code()         :145    ← 原 debugger
```

代价包括：**没有独立 LLM 配置**（都跟着 code_generator 的 temperature=0.2）、**没有独立故障隔离**（`_review` 抛异常会连带整个 pipeline 挂）、**在架构图和时序图上完全不可见**（协作不可观测）。

---

## 3. 三层病症对比与改造路径

| 层 | 病症 | 性质 | 改法 |
|---|---|---|---|
| Agent 层 | 编制残缺：1 个空转、5 个折叠、2 个没接线 | **缺人** | 补编制——接线 + 独立成 Agent |
| Workflow 层 | 8 类执行活 + 派活重复 | **越界** | 瘦身——执行活下沉，自己只留顺序/分支/降级 |
| 折叠角色 | 5 个角色无独立人格 | **隐形** | 从工人肚子里接生出来 |

**顺序必须是：先补编制，再瘦身。** 原因：编制补齐后，workflow 那 8 类执行活才有下沉的目的地；反过来先瘦身会无处可去。

---

## 4. 问题清单（供汇总）

| 编号 | 问题 | 严重度 | 状态 |
|---|---|---|---|
| M2-01 | `OrchestratorAgent` 实例化但从未调用，派活逻辑 duplicated 在 workflow | **高** | 未处理 |
| M2-02 | `ui_generator`（759 行 + 474 行工具 + 700 行测试）与 `audio_generator`（404 行）零接线 | **高** | 未处理 |
| M2-03 | 5 个审核角色折叠进 code_generator，无独立配置/故障隔离/可观测性 | **高** | 未处理 |
| M2-04 | workflow 直接调 `fix_code()` 5 处，破坏分层抽象 | 中 | 未处理 |
| M2-05 | workflow 扛了 8 类执行活（扫描/冒烟/试玩/构建/后处理/配方/沙箱） | 中 | 未处理 |
| M2-06 | `scene_generator` 走图外并行，绕过 leader 任务分发 | 中 | 未处理 |
| M2-07 | code_generator 1319 行，是全项目第二大文件 | 中 | 未处理（M2-03 的后果） |

## 5. 改造建议

**第一步：补编制（中成本，主要是搬代码）**

1. M2-01：删除 workflow 里的 `_get_all_ready_tasks` / `_orchestrator_node` 重复实现，改为调 `OrchestratorAgent.get_next_task()`——它已经写好了，只是没人用
2. M2-02：把 `ui_generator` / `audio_generator` 挂到 leader 的任务分派上；同时 `_NON_CODE_TASK_TYPES`（`workflow.py:526`）里的 `ui` / `documentation` 不再"跳过但标记完成"
3. M2-06：`scene_generator` 归队，场景类任务走正常分发而非图外并行

**第二步：接生折叠角色（中成本）**

4. M2-03：把 `_review`/`_refactor`/`_generate_tests`/`_final_check`/`fix_code` 从 code_generator 拆成独立 Agent 类，恢复 README 描述的团队形态，同时把 1319 行降下来
5. M2-04：workflow 停止直接调 `fix_code()`，改为向统一的"反馈接口"提错误，由编排层决定路由给哪个修复角色

**第三步：workflow 瘦身（中高成本）**

6. M2-05：把 8 类执行活下沉为独立模块/Agent，workflow 只保留"阶段顺序 + 分支 + 降级 + 事件推送"
7. 建议把阶段顺序抽成一张显式阶段表（数据驱动），让 `run()` 与 `run_with_streaming()` 退化为同一个执行器

---

## 附：验证方式

```bash
# M2-01：orchestrator 只有实例化、没有 execute
grep -n "self.orchestrator" src/core/graph/workflow.py

# M2-02：两个专家零接线（应为空）
grep -rn "ui_generator\|audio_generator" src/core/graph/workflow.py

# M2-04：fix_code 被调 5 处
grep -n "fix_code(" src/core/graph/workflow.py

# M2-03：5 个折叠角色的位置
grep -n "async def _\(review\|refactor\|generate_tests\|final_check\)\|async def fix_code" \
  src/agents/code_generator/__init__.py

# M2-07：code_generator 规模
wc -l src/agents/code_generator/__init__.py

# Agent 编制总览
ls src/agents/; grep -n "class AgentType" -A 10 src/core/state/game_state.py
```
