# GameForge 美术资源质量提升方案

> 目标：让生成的美术资源更接近市场审美，而不是"AI 拼凑感"。
> 结论先行：**当前瓶颈不在生图模型，而在"像素风"这件事从头到尾没有真正落地**——生成的是百万色照片，却用"最近邻 + 硬缩到 28~48px"去播放。补齐后处理是性价比最高的一刀。

本文件所有结论均有实测数据支撑（2026-09-15 实测，样本 `projects/GameForge_Project/assets/gen/`）。

---

## 一、实测证据：产物根本不是像素画

用 PIL 统计每张 AI 资源的独立颜色数：

| 资源 | 尺寸 | 独立颜色数 | 像素画应有规格 | 倍数 |
|---|---|---|---|---|
| background.png | 1360×768 | **75,347** | ~480×270 @ ≤32 色 | ~2300× |
| platform.png | 512×512 | **35,375** | 64×64 @ ≤16 色 | ~2200× |
| npc.png | 512×512 | **18,822** | 64×64 @ ≤24 色 | ~780× |
| player.png | 512×512 | **18,081** | 64×64 @ ≤24 色 | ~750× |
| decoration.png | 512×512 | 10,228 | 64×64 @ ≤16 色 | |
| enemy.png | 512×512 | 10,267 | 64×64 @ ≤16 色 | |
| enemy2.png | 512×512 | 9,535 | 64×64 @ ≤16 色 | |
| enemy3.png | 512×512 | 9,603 | 64×64 @ ≤16 色 | |
| pickup.png | 512×512 | 9,556 | 32×32 @ ≤16 色 | |
| ground.png | 512×512 | 5,163 | 64×64 @ ≤16 色 | |
| icon.png | 512×512 | 4,977 | 256×256 @ ≤32 色 | |

`player.png` 像素密度最高的前 20 个颜色全部是同一色相的 **±1 RGB 抖动**：

```
(148,129,109) (148,129,108) (147,128,108) (147,129,108) (149,129,109) ...
```

这是扩散模型 dithering 噪声的直接指纹。真正的像素画不会有两个只差 1 的相邻颜色。

**另一处实测**：`player.png` 的半透明像素数 = **0 px**（全部为 alpha=0 或 alpha=255），说明抠背景是硬阈值二值化，从一张 512px 抗锯齿图上切下来的边缘必然是"512 尺度下的锯齿"，而不是"像素艺术语下的边缘"。

### 玩家实际看到的是什么

`projects/*/scenes/main.tscn` 里写死了缩放系数：

```gdscript
scale = Vector2(0.0938, 0.0938)   # 48 / 512 —— 玩家 48px
scale = Vector2(0.0547, 0.0547)   # 28 / 512 —— 敌人 28px
scale = Vector2(0.0391, 0.0391)   # 20 / 512 —— 道具 20px
scale = Vector2(2.2588, 2.2500)   # 背景放大 2.26 倍 —— 1360×768 → 3072×1728
```

也就是说：**一张 18,081 色的 512px 图，被最近邻点采样丢弃 90%+ 像素，硬塞进 48px 显示**。
结果不是"像素画"，而是"糊掉的噪点块"。背景更糟——一张 75,347 色的照片被放大 2.26 倍铺满三屏。

`texture_filter = 1`（最近邻）本身是**正确的**（见 `tests/unit/test_asset_forge.py:36` 的注释"像素风最近邻采样"），问题不在滤镜，在于**源头就不是像素画**。

---

## 二、六个天花板（按影响排序）

### 天花板 1（最大）：AI 出图没有"像素化 + 调色板收敛"后处理

- `color_quantizer.quantize_to_palette`（16 色调色板量化器）**已经写好了**，但只在程序化精灵路径 `p2p_sprite.py:139` 被调用；`asset_forge.py:254-266` 的 AI 路径**完全没调用**。
- AI 路径的后处理只有两步：`_remove_background` + `resize(NEAREST)`。
- `style.py` 里的 `"warm limited color palette"` 只是 prompt 里的**口头建议**，模型听不听全靠运气，没有任何代码强制。
- 后果：11 张图各自一套色（1.8 万色 vs 5000 色 vs 7.5 万色），拼在一个场景里颜色互相打架。

### 天花板 2：主题调色板已存在，但美术生成完全没用上

- `genre_fusion.py:57-85` 为 25 个主题包都定义了 `palette_base`：`forest_green` / `neon_purple` / `lava_red` / `warm_beige` / `sky_blue` / `space_black`。
- 但这个字段只被**程序化几何**用到（`scene_to_godot._get_palette`）。AI 贴图路径拿到的是那条硬编码的全局风格串。
- 后果：程序化色块与 AI 贴图**可能处于不同调色板**，同一场景里两种色彩体系并存。

### 天花板 3：风格漏斗是一条主题无关的硬编码字符串，且视角可能写错

`src/image/style.py:10-16`：

```
GAMEFORGE_ART_STYLE = (
    "Stardew Valley inspired 2D pixel art style, "
    "cozy 16-bit retro farming-game aesthetic, top-down view, ..."
    "warm limited color palette, ..."
)
```

- **视角可能错**：`top-down view` 写死。项目主力品类 `platformer` 是横版**侧视**（`scene_to_godot.py` 的相机是侧视跟随），top-down 会让角色呈 45° 俯视，和碰撞体/物理观感对不上。
- **调性自相矛盾**：`space_night`（深空）、`neon_city`（赛博霓虹）、`graveyard`（墓地）、`volcano`（火山）这些主题，都会被追加 `cozy ... farming-game aesthetic` + `warm limited color palette`，直接和主题打架。
- **一个常量管 25 个主题**，共性是有了，个性被抹平——这也是"每次都像同一个游戏"的美术侧成因。

### 天花板 4：art_director 被明确要求"不许写风格词、最多 18 词"

`src/agents/art_director.py:213-222` 的 LLM 指令：

```
Write one short English image prompt (max 18 words, no style keywords, no quotes)
```

这是在**主动放弃美术控制力**：art_director 只决定"画什么"（内容），"怎么画"（材质/光位/轮廓/子风格）全丢给那条硬编码风格串。市场级美术的差距恰恰在于"怎么画"。

另外 `temperature=0.8` 偏高，同一主题两次运行视觉差异大。

### 天花板 5：一个槽位只生成一张，没有"角色一致性锚"

`forge_assets` 每个槽位只调一次 `generate_image`，无 best-of-N、无重试、无筛选。
player / npc / enemy 之间除了全局风格串没有任何共享设计语言，观感像"三个画师各画各的"。

### 天花板 6：敌人变体靠 PIL 色相偏移

`asset_forge.py:282-283`：

```python
if key == "enemy" and os.path.isfile(out_path):
    _hue_variants(out_path, 2)
```

同一剪影换色 = 教科书级"廉价复色"信号，玩家一眼看穿。实测 enemy / enemy2 / enemy3 颜色数 10,267 / 9,535 / 9,603——确实是同一张图的色相旋转。

---

## 三、P0 已实施（2026-09-17）

### 改了什么

| 文件 | 改动 |
|---|---|
| `src/image/procedural/pixel_pipeline.py` | **新增**。像素化管线：预乘 alpha 的 LANCZOS 降采样 → alpha 二值化 → 调色板量化 → NEAREST 整数倍放大；含 7 套主题调色板、median cut 自提取、4×4 有序抖动、有界色相对齐、特征色追加 |
| `src/engine/godot/asset_forge.py` | `_SPEC` 新增 `pixel`（原生像素尺寸，必须整除 `size`）；后处理接入 `pixelate`；新增 `_pixel_art_enabled` / `_theme_palette` / `_pixel_opts_for` / `_pixel_options`；`_hue_variants` 增加 palette 回量化 |
| `src/agents/art_director.py` | 新增 `resolve_palette_base()` —— 让 AI 贴图复用程序化几何的那套主题解析（补上 `palette_base` 只被几何使用的漏） |
| `tests/unit/test_pixel_pipeline.py` | **新增** 15 个测试：整数倍放大、调色板约束、alpha 二值化、透明区不污染边缘、明度守恒、色相位移上限、灰阶不被改动、特征色追加、三种模式装配、`_SPEC` 对齐 |
| `tests/unit/test_asset_forge.py`、`test_art_director.py` | 替身函数签名同步（新增第 5 个参数） |

### 三种量化模式

`GAMEFORGE_PIXEL_PALETTE` 控制默认模式，槽位可用 `_SPEC[key]["palette_mode"]` 覆写：

| 模式 | 做法 | 适合 |
|---|---|---|
| `harmonize`（默认） | 逐图 median cut 提色，**保留明暗层次**，再把色相有界（≤18°）对齐到主题 | 精灵、地块（细节优先） |
| `theme` | 硬量化到主题调色板，另可追加 ≤6 个"特征色" | **背景**（明暗层次分离最好） |
| `auto` | 纯逐图自提取，不做主题对齐 | 兜底 / 想让素材保留原味 |

主开关 `GAMEFORGE_PIXEL_ART=0` 可整体退回旧的"直接 NEAREST 缩放"行为。

槽位策略经实测对比后定为：`background → theme + accents（48 色）`，其余 → `harmonize（24 色）`。

### 实测效果（已有 11 张素材重跑）

| 资源 | 原生像素 | 处理前颜色数 | 处理后颜色数 | 压缩比 |
|---|---|---|---|---|
| player.png | 32×32 | 18,081 | **25** | 723× |
| npc.png | 32×32 | 18,822 | **25** | 753× |
| platform.png | 32×32 | 35,375 | **14** | 2527× |
| enemy.png | 32×32 | 10,267 | **24** | 428× |
| decoration.png | 32×32 | 10,228 | **25** | 409× |
| pickup.png | 16×16 | 9,556 | **10** | 956× |
| ground.png | 32×32 | 5,163 | **24** | 215× |
| icon.png | 128×128 | 4,977 | **12** | 415× |
| background.png | 340×192 | 75,347 | **≤54** | ~1400× |

**视觉结论（`docs/art-quality/sheet_background.png` 对比）**：原图是一张"照片感"的雨夜农场；处理后是层次分明、可读性强的 16-bit 像素画背景——天空/山丘/谷仓/树木的轮廓与明暗完全分离。这是本次改动最直观的收益。

**sprite 对比（`docs/art-quality/sheet_sprites.png`）**：`harmonize` 保留了每个素材的明暗细节（对比"全部 theme"那一行：纯主题量化会把地面压成 3 色平面块，玩家角色失去立体感）。

### 复现方式

```powershell
D:\game_project\.venv\Scripts\python.exe docs\art-quality\verify_pixel.py
```

该脚本走的是 `asset_forge` 的真实生产路径（`_theme_palette` / `_pixel_opts_for`），
只读现有产物，不触发生图、不修改项目文件。

### 已知遗留

- **尺寸体系未重定**：纹理仍是 512px，`.tscn` 仍是 `scale = 48/512`。原生 32px 放大 16 倍到 512 后，在 48px 显示时是 0.67 倍的非整数缩放。纹理本身已是干净像素画，但要做到像素级 1:1 映射，还需把 `_SPEC.size` 与 `.tscn` 缩放系数一起改成"原生尺寸 × 整数倍"。
- **未做生成侧约束**：P1（风格漏斗参数化）与 P2（art_director 放开风格词）尚未实施，所以模型出图时仍可能给出与主题冲突的配色，`harmonize` 只能事后兜住。

---

## 四、后续计划（按 性价比 = 效果 / 成本 排序）

| 优先级 | 改什么 | 改动面 | 成本 | 预期效果 |
|---|---|---|---|---|
| ~~**P0**~~ | ~~后处理补"像素化 + 调色板量化"~~ | — | — | **已完成**，见第三节 |
| ~~**P1**~~ | ~~风格漏斗参数化（视角/调色/主题形容词）~~ | — | — | **已完成**，见第三节 |
| ~~**P2**~~ | ~~art_director prompt 放开风格词、统一光照与描边~~ | — | — | **已完成**，见第三节 |
| ~~**P3**~~ | ~~player 作"风格锚" + i2i 生成 npc/enemy~~ | — | — | **已完成**，见下文 |
| ~~**P4**~~ | ~~敌变体改 i2i 生成，弃用色相偏移~~ | — | — | **已完成**，见下文 |
| **P5** | 尺寸体系重定（`_SPEC.size` + `.tscn` 缩放系数 ↔ 原生尺寸整数倍） | `_SPEC` + `scene_to_godot.py` | 中 | 中：像素级 1:1 映射 |

### P1/P2 已实施（2026-09-20）

| 文件 | 改动 |
|---|---|
| `src/image/style.py` | `apply_art_style` 升级为 `apply_art_style(prompt, *, genre, palette_base, camera)`；新增 `build_art_style()` 装配"像素风底座 + 视角短语 + 调色短语"。全局串删掉写死的 `top-down view` 与 `warm limited color palette`（前者对横版 platformer 是错视角，后者和深空/霓虹主题打架）；缺省视角改侧视（对齐 `CameraIR` 默认 `2d_side_view`），camera 缺省时按品类推断（platformer/runner → 侧视，shooter/tower_defense/rpg → 俯视） |
| `src/image/ai_image_client.py` | `generate_image` 新增 `genre` / `palette_base` / `camera` 显式参数（不进 provider 转发），透传给风格漏斗 |
| `src/mcp/servers/image_server.py` | `generate_image` 同步新增三个风格上下文参数（MCP tool schema 不变，外部调用方走默认） |
| `src/engine/godot/asset_forge.py` | `forge_assets` 解析风格上下文一次（`scene_ir.genre` / `resolve_palette_base` / `scene_ir.camera.mode`）随 `_generate_one` 透传；**补上注释承诺已久的第一级提示词优先级**——`art_prompts` 缺省时调 `plan_art(scene_ir)`（此前只有注释、无调用方，生产路径实际走的是逐槽 `_smart_prompt`）；plan_art 放在 provider 检查之后，无 AI key 不白付 LLM 调用 |
| `src/agents/art_director.py` | P2：LLM prompt 从 `no style keywords` 放开为允许简要画法词，并强制全槽位统一光位/描边/剪影（"consistent soft lighting from one direction, clean dark outline, readable silhouette, matching material feel"）；`temperature` 0.8 → 0.4；词上限 18 → 20 |
| `tests/unit/test_image_style.py` | 新增 8 个参数化测试（默认侧视、camera/genre 视角选择、palette 调色短语、未知值回落、带参幂等、装配顺序、MCP 透传） |
| `tests/unit/test_asset_forge.py` | 替身签名同步（`style_ctx`）；新增 `test_forge_wires_plan_art_and_style_ctx`（plan_art 接驳 + 风格上下文透传，farm 主题 → forest_green + 侧视）；`test_forge_generates_and_caches` 显式关 LLM 保证离线确定 |
| `tests/unit/test_art_director.py` | 替身签名同步；新增 `test_llm_prompt_allows_style_words_with_shared_design_language`（断言不再禁风格词、含光照/描边/剪影、temperature=0.4） |

**遗留**：`_smart_prompt`（asset_forge 逐槽智能提示）仍带 `no style keywords` + temperature 0.7 的限制；
P3~P5（i2i 一致性锚、敌人变体、尺寸体系重定）未动。

### P3/P4 已实施（2026-09-23）

| 文件 | 改动 |
|---|---|
| `src/image/ai_image_client.py` | `generate_image` 新增 `reference_image_paths`（本地图 → data URI，坏文件跳过），经 `image_urls` kwarg 透传；Step provider payload 透传 `image_urls`（`step-image-edit-2` 真编辑语义，参考图边缘完整保留） |
| `src/engine/godot/asset_forge.py` | `forge_assets` 改**两波生成**：第一波 8 槽位并行；player/enemy 落盘后第二波用 i2i 生成 npc（anchor 模式，参考图只贡献视觉语言）与 enemy2/enemy3（edit 模式，同剪影换色）。开关 `GAMEFORGE_I2I_ANCHOR` / `GAMEFORGE_I2I_VARIANTS`（默认开，关闭回旧行为）；第二波有 30s 保底时间片；预算默认 100→140s |
| `src/engine/godot/asset_forge.py` | `_generate_one` 新增 `reference_paths` / `out_key` / `same_subject`；i2i 失败自动退回无参考图重生成一次（`asset_forge.i2i_fallback_t2i`）；新增 `_variant_alpha_guard` 变体守门；`_hue_variants` 降级为保底路径（`_hue_variants_if_missing`，两个变体都缺才触发，避免写错文件名） |
| `tests/unit/test_ai_image_client.py` | **新增** 6 个测试：风格上下文透传（防 P1 回归）、缺省侧视、参考图 → data URI、坏路径跳过、`_reference_data_uris` 单测、Step provider payload 含 `image_urls` |
| `tests/unit/test_asset_forge.py` | 新增两波顺序与参考图断言、开关关闭回旧行为、i2i→t2i 回退、`_i2i_prompt` 两种包装、变体守门（清底 / IoU / 漂移拒绝）共 8 个测试；`test_forge_generates_and_caches` 更新为 11 次调用 + 变体产物 |

**实测（2026-09-23，真实 Step API 端到端，`_generate_one` 生产路径）**：

| 产物 | alpha 覆盖 | 主色色相 | 说明 |
|---|---|---|---|
| enemy（参考图） | 31% | 280°（紫） | 自制 32×32 两色史莱姆 |
| enemy2 | 27% | **15°（红）** | 换色生效，6 色，无灰底 |
| enemy3 | 27% | 150°（青） | 换色生效，9 色，无灰底 |
| npc | 58% | 60°（暖） | player 风格锚生效，17 色 |

**实测中发现并已修复的两个真 bug**：

1. **i2i 输出带不透明灰背景**（模型不保留透明通道，占画面 ~80%）。`_remove_background` 的四角一致性检查会保守跳过 → 精灵带灰底 box 进游戏。修复：`_variant_alpha_guard` 在覆盖度 > 90% 时用参考图 alpha 掩膜清底。
2. **变体剪影可能漂移**（实测 IoU 0.48~0.54，模型大致保住但不精确）。修复：IoU < 0.3 判定漂移，放弃该 i2i 结果，交回 `_hue_variants_if_missing` 色相保底——"视觉不重样且同族"由此成为确定性契约而非运气。

### P1 原方案（存档）

`apply_art_style(prompt)` 升级为 `apply_art_style(prompt, *, genre, palette_base, camera)`：

- 相机视角由 `scene_ir.camera.mode` 决定（side / top-down / isometric），不再写死 `top-down view`；
- 调色倾向由 `palette_base` 决定（现在 P0 只在后处理端用它，生成端还没用上）；
- 保留"像素风"这一层共性（保证 11 张图是同一个游戏），把 `cozy farming-game aesthetic` 降级为主题包自己的形容词。

---

## 五、诚实的边界

- P0~P4 已完成并实测验证（P0 后处理、P1/P2 prompt 层、P3/P4 i2i 一致性与变体，均有真实 API 端到端数据）。
- **天花板仍然存在**：生图模型本身的分布 + 没有人工挑图环节。市场级美术背后是画师挑选与修图。在"零人工"约束下，做到 P0~P4 基本就是这个体系的合理上限；i2i 锚定解决的是"像一个游戏"，解决不了"像市场级美术"。
- 本方案**不改善玩法与手感**——那两个问题有各自独立的成因，见 `docs/QUALITY_DIAGNOSIS.md`。

