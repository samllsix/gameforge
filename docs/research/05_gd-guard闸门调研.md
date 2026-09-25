# M5 · gd-guard 安全闸门调研

> **调研范围**：`tools/gd-guard/src/main.rs`（447 行，Rust）+ `src/engine/godot/gd_guard.py`（118 行，Python 接线）+ workflow 与 API 层的调用点。
>
> **一句话结论**：这是全项目唯一一道主动安全防线，工程质量高（Rust 零依赖、信任边界精确到文件、有界反馈），但**能力上限被实现方式锁死**（子串匹配 ≠ 安全分析）、**失效时完全静默**（失败开放）、**漏接了唯一的真实代码执行面**（预览端点）。建议保留但重新定位：它现在的能力是"防 LLM 误用"，不配叫"安全闸门"。

---

## 1. 这一层做什么

在游戏运行/出包前扫描生成的 Godot 项目，命中危险规则即一票否决。

### 1.1 五类检查规则

| # | 检查项 | 规则 | 位置 |
|---|---|---|---|
| 1 | `.gd` 危险 API | 25 条黑名单逐行匹配（执行/文件/网络/联机/反射/探针） | `main.rs:21` `BLOCK_PATTERNS` |
| 2 | `.tscn` 路径逃逸 | `ext_resource` 必须 `res://` 且解析后不越出项目（拒绝 `..`/绝对路径/盘符） | `main.rs:154` `scan_tscn` |
| 3 | `.tscn` 内嵌脚本 | `sub_resource` 里的 GDScript 反转义后**一律按不可信扫** | `main.rs:210` |
| 4 | `project.godot` autoload | 仅允许指向 `res://addons/gameforge/` | `main.rs:244` |
| 5 | 原生二进制载荷 | 出现 `.dll/.so/.dylib/.gdextension/.pyd` 即拦 | `main.rs:301` |

### 1.2 信任边界：精确到文件（设计亮点）

```rust
const TRUSTED_FILES: &[&str] = &["res://addons/gameforge/runtime/player.gd", ...9 个...];
```

**不用目录前缀，逐个文件列白名单**，Rust 单测明确验证了防投毒意图：

```rust
assert!(!is_trusted("res://addons/gameforge/runtime/evil.gd"));        // 投毒进信任目录 → 不信任
assert!(!is_trusted("res://addons/gameforge/runtime_malicious/evil.gd")); // 仿冒目录名 → 不信任
```

### 1.3 现有调用链

```
workflow._gd_guard_scan()  （workflow.py:127）
   ├─ 扫描目录：sandbox task_dir > projects/<pid>/
   ├─ 项目未落盘 → 跳过（避免把"缺 project.godot"误判为 block）
   ├─ block → findings 喂 code_generator.fix_code() 重生成一轮 → 写盘 → 重扫
   │            └─ 仍 block → runnable=False，阻止运行/出包
   └─ 二进制缺失/异常 → pass（失败开放）

export 端点（api/main.py:1222）
   └─ 第 0 关：block → 502 stage=gd_guard，不出包
```

另外 `DANGEROUS_APIS` 被 `code_generator:393` 消费——**生成阶段就在提示词里禁用这些 API**，比生成后拦截便宜得多。这是双保险里更有效的那层。

---

## 2. 三个决策问题

> 这三个问题决定了这一层是"留、改、砍"，比规则清单重要。

### 2.1 不用 Rust 行不行？

**行。Rust 的两个价值里只剩一个站着。**

| Rust 的理由 | 是否成立 |
|---|---|
| 扫描快 | ❌ 虚。实际对象是一个项目几十个文件，Python 也是几十毫秒 |
| 静态二进制、零运行时依赖 | ⚠️ 一半。方便，但项目已依赖 Python+Godot+Qdrant/Redis，边际成本高 |
| **信任独立性（编译产物不可被随手改）** | ✅ **真**。且非纯理论：`_gd_guard_scan` 的修复回写用 `os.path.join(scan_dir, rel_path)`，`rel_path` 来自 LLM 输出——生成物确实可能影响仓库文件 |

**结论**：保留 Rust 的信任锚，但**把"本地编译"移到 CI**：

| 方案 | 信任独立性 | 本地工具链 | 跨平台 |
|---|---|---|---|
| 现状：每人 `cargo build` | 强 | 🔴 每人装 Rust（你刚为 336KB 二进制装了 100MB+ 工具链） | 🔴 已有命名 bug |
| **CI 编三平台二进制放 `bin/`** | 强 | 🟢 零 | 🟢 三平台都发 |

### 2.2 应该接到哪里？

**接了 2 个口，漏 3 个——漏的那个是唯一真实执行面。**

```
✅ workflow 冒烟前            workflow.py:127
✅ 导出门禁第 0 关            api/main.py:1222
❌ 预览渲染前                 api/main.py:909 调 write_project(:988) 后直接起 Godot 子进程
❌ 沙箱 merge 回主线前
△ 配方沉淀                   只存 verified 的，风险低可接受
```

**最该补的是预览端点**。它不是"看一眼"：`/api/v1/preview/frame` 会真的起 Godot 子进程跑那个场景。也就是说一条 GET 请求就能让服务端执行项目里的代码，而这条路径**完全不过闸门**；相比之下 workflow 那条至少是 POST + 有 body 限制 + 有输入校验。

**接入原则**：按"不可信代码 → 磁盘 → 被执行"的边界算，每个执行面前都该有一道。

### 2.3 必要吗？

**取决于产品形态，但它现在的能力配不上这个名字。**

| 形态 | 判断 |
|---|---|
| 单机自用（当前） | **价值有限**。你自己配 Key、自己看输出、游戏本地跑。而且生成提示词已经先禁过一遍，实际大概率不触发 |
| 多租户 / 发布 / SaaS | **必要**。① 有 Web 导出功能，出包即分发给所有人；② 策划文档是不可信输入，提示词注入是真实向量；③ LLM 输出天然不可信 |

**但前提是它得真是个闸门。** 现在的能力边界：

```
子串匹配      → "OS." + "execute" 拼串即绕过；API 跨行也绕过
每行只报一条  → 一行多个危险 API 会漏
失败开放      → 二进制没了静默放过，你不会有任何体感
```

它现在的能力是**"防止 LLM 不小心用错 API"**，不是"防止恶意代码执行"。

---

## 3. 问题清单（供汇总）

| 编号 | 问题 | 严重度 | 状态 |
|---|---|---|---|
| M5-01 | 子串匹配可被拼串/跨行轻易绕过，能力上限锁死 | **高** | 未处理 |
| M5-02 | 失败开放且**失效完全静默**（实测：跨平台命名 bug 曾让闸门长期静默失效） | **高** | 未处理 |
| M5-03 | **预览端点不过闸门**，却是唯一真实代码执行面（GET 即可触发） | **高** | 未处理 |
| M5-04 | 黑名单有遗漏（`Expression` 动态求值、`Object.call`、`get_method_list` 未拦） | 中 | 未处理 |
| M5-05 | 每行只报一条（`break`），同行多个危险 API 会漏到下一轮 | 中 | 未处理 |
| M5-06 | 双份黑名单无同步机制，已漂移（Python 提示词缺 `ClassDB.instantiate`） | 中 | 未处理 |
| M5-07 | 本地 `cargo build` 负担 + 跨平台命名差异 | 中 | 未处理 |
| M5-08 | 扫的是磁盘项目，与"内存里刚生成的代码"存在窗口 | 低 | 未处理 |
| M5-09 | 沙箱 merge 回主线前不过闸门 | 低 | 未处理 |

---

## 4. 修改策略（分四步，每步可独立验收）

### 第一步：让失效可见（最小改动，先做）

- **M5-02 / M5-07**：启动时（lifespan）探测 `find_guard()`，结果写入 `/health` 的 `gd_guard_available` 字段 + 启动日志 `warning`；CI 预编译三平台二进制放入 `tools/gd-guard/bin/<平台>/gd-guard[.exe]`，`find_guard()` 优先找它，找不到再退回本地 `target/`
- **验收**：删掉二进制后 `/health` 立刻显示不可用；macOS 上不做任何编译即可用闸门

### 第二步：补上真实执行面

- **M5-03**：`preview_frame` 里 `write_project` 之后、起 Godot 子进程之前，插入 `scan_project()`；block 时返回 403 且不渲染
- **M5-09**：`sandbox.merge()` 前扫一次 task_dir
- **验收**：构造一个含 `OS.execute` 的项目，`GET /api/v1/preview/frame` 返回 403 且日志无 Godot 进程启动

### 第三步：提升检测能力

- **M5-01 / M5-05**：去掉每行 `break`（改报所有命中）；增加常见变形检测——拼串常量折叠（`"OS." + "execute"`）、跨行拼接、十六进制/字符串构造
- **M5-04**：补齐 `Expression`、`Object.call`、`call_group` 等反射与动态调用入口
- **M5-06**：**黑名单改单一事实源**——以 Rust 的 `BLOCK_PATTERNS` 为准，构建时生成 Python 的 `DANGEROUS_APIS`（加一个单测断言两边一致）
- **验收**：`cargo test` 新增绕过用例（拼串/跨行）必须 block；Python 单测断言黑名单与 Rust 同步

### 第四步：定位校准（文档/命名）

- **2.3**：把 README/文档里的"安全闸门"改为"危险 API 静态闸门（防误用级别）"，明确它不防蓄意绕过
- **M5-08**：扫描对象改为"落盘后的项目 + 内存中待落盘代码"都过一遍（或统一为落盘前扫描）
- **验收**：文档不再宣称它提供安全保证

### 不做的

- 不上 AST/语义分析引擎（收益不抵成本，且需要引入 tree-sitter 等依赖）
- 不做网络隔离（依赖 gd-guard 拦 API 层 + Godot 侧无网络权限假设）

---

## 附：验证方式

```bash
# 五类规则位置
sed -n '21,47p' tools/gd-guard/src/main.rs          # 黑名单
sed -n '154,175p' tools/gd-guard/src/main.rs        # tscn 路径逃逸
sed -n '244,275p' tools/gd-guard/src/main.rs        # autoload 校验

# M5-06：双份黑名单漂移
diff <(sed -n '21,47p' tools/gd-guard/src/main.rs | grep -oE '"[A-Za-z.]+"' | tr -d '"' | sort) \
     <(.venv/bin/python -c "from src.engine.godot.gd_guard import DANGEROUS_APIS; [print(a) for a in sorted(DANGEROUS_APIS)]")

# M5-03：预览端点不过闸门
grep -n "gd_guard" src/api/main.py    # 只有 :1222 一处（导出门禁）

# 手动触发一次（需先 tools/gd-guard/bin/ 或本地 cargo build）
./tools/gd-guard/target/release/gd-guard scan projects/demo_jump_v2
```
