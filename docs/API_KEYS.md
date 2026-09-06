# GameForge API Key 获取指南

本文档说明 GameForge 使用到的全部第三方 API Key 如何获取、配置在哪、以及如何验证。
所有密钥统一放在项目根目录的 `.env` 文件（把 `.env.example` 复制一份即可），
GameForge 启动时会通过 `python-dotenv` 自动加载。

> 密钥不会写入 `config/config.yaml`——该文件只通过 `api_key_env` 字段
> **引用**环境变量名（如 `llm.providers.stepfun.api_key_env: STEPFUN_API_KEY`），
> 实际密钥从 `.env` 读取。这样密钥不进版本库，运维也能用注入方式覆盖。

---

## 1. 配置总览

| `.env` 键名 | 用途（Agent / 模块） | 提供方 | 获取地址 |
| --- | --- | --- | --- |
| `MIMO_API_KEY` | 通用 LLM（OpenAI 兼容，旧默认 provider） | 小米 MiMo | https://mimo.mi.com |
| `DEEPSEEK_API_KEY` | 代码生成/测试生成/重构（备用） | DeepSeek | https://platform.deepseek.com |
| `ZHIPU_API_KEY` | Debugger Agent（glm-4.5-air） | 智谱 AI | https://open.bigmodel.cn |
| `KIMI_API_KEY` | CodeReviewer Agent | Moonshot Kimi | https://platform.moonshot.cn |
| `STEPFUN_API_KEY` | 世界模型 / 默认 LLM（step-3.7-flash） | 阶跃星辰 StepFun | https://platform.stepfun.com |
| `STEPFUN_TTS_API_KEY` | TTS 对话/旁白（stepaudio-2.5-tts） | 阶跃星辰 StepFun | https://platform.stepfun.com |
| `STEP_API_KEY` | Step 图像生成（ImageEdit 2） | 阶跃星辰 StepFun | https://platform.stepfun.com |
| `SENSENOVA_API_KEY` | 图像生成备选（SenseNova U1.5 Lite） | 商汤 SenseNova | https://platform.sensenova.cn |
| `QDRANT_API_KEY` | 向量数据库（仅云版需要） | Qdrant | https://cloud.qdrant.io |
| `LANGCHAIN_API_KEY` | LangSmith 链路追踪（可选） | LangChain | https://smith.langchain.com |
| `GAMEFORGE_API_KEYS` | 本服务 HTTP API 的访问密钥（非 LLM 密钥） | 自建 | 见 [5. 服务自身访问密钥](#5-服务自身访问密钥) |

> `REDIS_PASSWORD` / `DBMY_PASSWORD` / `SECRET_KEY` 属于自建基础设施密钥，
> 由本机部署者自行设置，不在第三方平台申请范围内。

---

## 2. 各家申请步骤

### 2.1 阶跃星辰 StepFun（默认 LLM / TTS / 图像）

GameForge 的默认模型链（planner / code_generator / code_reviewer / test_generator /
refactor / scene_generator / game_designer）都走 StepFun，**建议优先申请**。

1. 打开 [StepFun 开放平台](https://platform.stepfun.com) 并注册/登录；
2. 进入「控制台 → API Keys」（参考[官方文档](https://platform.stepfun.com/docs/zh/guides/organization/api-keys)），
   创建项目 API Key；
3. 一个账号可创建多个 Key，分别为不同用途（LLM / 图像 / TTS）建 Key，
   填到下面的键位：
   - 世界模型 → `STEPFUN_API_KEY`
   - 图像生成 → `STEP_API_KEY`
   - TTS → `STEPFUN_TTS_API_KEY`
4. 计费：先充值，模型按 token 计费；新用户通常有免费额度（以平台公告为准）。

快速开始见[官方文档](https://platform.stepfun.com/docs/zh/step-plan/quick-start)。

### 2.2 小米 MiMo

1. 打开 [MiMo API 开放平台](https://mimo.mi.com)；
2. 注册后进入控制台创建 API Key（参考[首次调用文档](https://mimo.mi.com/docs/zh-CN/quick-start/summary/first-api-call)）；
3. 填入 `MIMO_API_KEY`。OpenAI 兼容端点国内为 `MIMO_BASE_URL=https://token-plan-cn.xiaomimimo.com/v1`。

### 2.3 DeepSeek

1. 打开 [DeepSeek 开放平台](https://platform.deepseek.com) 注册；
2. 左侧「API Keys」→ 创建新密钥（密钥只在创建时完整显示，请立即复制）；
3. 填入 `DEEPSEEK_API_KEY`，端点 `DEEPSEEK_BASE_URL=https://api.deepseek.com/v1`。

### 2.4 智谱 Zhipu（GLM）

1. 打开 [智谱 AI 开放平台](https://open.bigmodel.cn) 注册并进行实名认证；
2. 「API Keys」页创建密钥，填入 `ZHIPU_API_KEY`；
3. Debugger Agent 使用的模型是 `glm-4.5-air`（见 `config.yaml` 的
   `llm.models.debugger`），计费便宜、适合调试循环。

### 2.5 Moonshot Kimi

1. 打开 [Moonshot 开放平台](https://platform.moonshot.cn) 注册；
2. 「API Key 管理」创建密钥，填入 `KIMI_API_KEY`。

### 2.6 商汤 SenseNova（图像备选）

1. 打开 [SenseNova 开放平台](https://platform.sensenova.cn) 注册；
2. 控制台创建密钥，填入 `SENSENOVA_API_KEY`；
3. 端点 `SENSENOVA_BASE_URL=https://token.sensenova.cn/v1`。

### 2.7 Qdrant（向量数据库）

- 本地 Docker/自建：不需要 Key，`QDRANT_API_KEY` 留空即可；
- 云端托管：在 [Qdrant Cloud](https://cloud.qdrant.io) 创建集群后，
  从「API Keys」页复制，填入 `QDRANT_API_KEY`。

### 2.8 LangSmith（可选，链路追踪）

在 [smith.langchain.com](https://smith.langchain.com) 注册后创建 API Key，
填入 `LANGCHAIN_API_KEY`，同时把 `LANGCHAIN_TRACING_V2` 保持为 `true`。
不使用时设 `LANGCHAIN_TRACING_V2=false` 即可关闭，不影响主流程。

---

## 3. 配置到项目

```bash
# 1. 复制模板（已存在的 .env 不会被覆盖）
cp .env.example .env        # Windows: copy .env.example .env

# 2. 编辑 .env，把 your_xxx_api_key_here 换成真实密钥
```

配置加载链：

```
.env ──python-dotenv──▶ 进程环境变量 ──config.yaml 的 api_key_env 字段──▶ 对应模块
```

例如 `llm.providers.stepfun.api_key_env: STEPFUN_API_KEY` 表示
「stepfun provider 的密钥从环境变量 STEPFUN_API_KEY 读取」。

| 模块 | config.yaml 位置 | 读取的 .env 键 |
| --- | --- | --- |
| LLM 路由 | `llm.providers.*.api_key_env` | MIMO / DEEPSEEK / ZHIPU / KIMI / SENSENOVA / STEPFUN |
| 图像生成 | `mcp.servers.image.env` | STEP_API_KEY / SENSENOVA_API_KEY |
| TTS | `audio.providers.stepfun.api_key_env` | STEPFUN_TTS_API_KEY |

---

## 4. 验证是否生效

启动服务后访问：

- `GET /health` → 响应中的 `llm_configured` 字段为 `true` 表示 LLM 配置齐备；
- 前端首页会提示「未配置 API Key」时，检查 `.env` 是否复制、键名是否拼写正确。

命令行快速自检：

```bash
python -m src.cli workflow --help          # 能正常打印帮助即表示导入链 OK
python -c "import os; from dotenv import load_dotenv; load_dotenv(); print('STEPFUN key:', bool(os.getenv('STEPFUN_API_KEY')))"
```

> 密钥值以 `your_` 开头的占位符会被部分模块视为「未配置」（如
> `src/utils/vector_store.py` 对 `QDRANT_API_KEY` 的处理），请勿把占位符当真实密钥。

---

## 5. 服务自身访问密钥

`GAMEFORGE_API_KEYS` 是 **GameForge HTTP 服务自己的访问控制密钥**，不是任何第三方
API 的密钥。格式为 `key1:name1,key2:name2`（冒号分隔密钥与备注、逗号分隔多组）。
仅生产环境（`GAMEFORGE_ENV=production`）强制启用；开发环境留空即可。

---

## 6. 常见问题

- **401 Unauthorized**：密钥过期/填错，或 `api_key_env` 指向的键在 `.env` 中不存在。
- **支付/限额**：各家免费额度与计费规则不同，请以各平台控制台为准。
- **Key 泄漏**：`.env` 已在 `.gitignore` 中，不要手动把密钥写进 `config.yaml` 或代码。