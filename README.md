# 🎵 MusicAgent — 零基础音乐创作提示词助手

让**没有系统学习过音乐的人**，也能通过对话得到一份专业、可直接投喂 AI 音乐生成工具（Suno / 网易天音 / ACE-Step 等）的**音乐创作提示词**：风格标签 + 歌词 + 编曲建议 + 评审意见。

> 你只需要说"帮我写一首毕业季的抒情歌，送给同班同学"，Agent 会以音乐制作人的身份完成需求拆解、风格定位、写词与编曲建议，并给出可粘贴到 Suno 的英文风格标签。

基于 [Full Stack FastAPI Template](https://github.com/fastapi/full-stack-fastapi-template) 构建。

---

## 1. 项目能做什么

| 能力 | 说明 |
|---|---|
| 🎭 分角色提示词工程 | Agent 分饰 **制作人 / 作词人 / 编曲师 / 听众评审** 四个角色，按创作阶段自动切换 |
| 🎸 风格提示词库 | 内置 **抒情、摇滚、流行、电子、国风、民谣、说唱** 7 种风格参数库，按用户输入自动识别注入 |
| 📄 知识增强（RAG） | 上传 txt/md 文档（歌词草稿、风格说明、乐理资料）自动入向量库，回答时检索增强 |
| 🖼️ 多模态 | 上传图片（专辑封面、场景照片）直接参与创作参考 |
| 💬 多会话管理 | 会话按用户隔离、自动创建、支持切换与删除 |
| ✅ 零基础友好 | 所有专业术语（BPM、和弦进行、调式）必须附带通俗解释——写进了系统提示词的硬性规则 |

## 2. 技术架构

```
React (Vite + TS + Tailwind + shadcn/ui)     DeepSeek 风格聊天界面
        │  FormData（文字 + 图片 + 文档）
        ▼
FastAPI  /api/v1/agent/chat（multipart）
        │
        ├─ 会话管理（PostgreSQL + SQLModel，按用户隔离）
        ├─ 文件分流：图片 → base64 多模态消息
        │            txt/md → 切块 → DashScope 向量化 → Chroma
        ├─ 风格识别：关键词命中 → 注入风格参数（prompt_templates.py）
        ▼
LangChain + ChatOpenAI（DashScope 兼容模式，默认 deepseek-v4-pro）
        ▼
结构化输出：需求解读 / 风格提示词 / 歌词 / 编曲建议 / 评审意见
```

**目录导览**

```
backend/app/
├── agent/
│   ├── prompt_templates.py   # ★ 提示词工程核心：角色库 + 风格库 + 风格识别
│   ├── agent_core.py         # LLM 调用、多模态消息组装、RAG 上下文注入
│   ├── routes.py             # /agent/chat（multipart）、sessions 增删查
│   └── config.py             # AGENT_* 配置
├── rag/
│   ├── retriever.py          # 文档入库（按会话隔离）与相似度检索
│   ├── embedding.py          # text-embedding-v4（DashScope）
│   ├── build_knowledge_base.py  # 全局知识库批量导入脚本
│   └── knowledge_base/       # 放入 txt/md 后运行脚本即可导入
frontend/src/
├── components/Agent/         # 聊天页面（附件、预览、Markdown 渲染）
└── routes/index.tsx          # 聊天页即首页 /
```

## 3. 分角色提示词工程（核心设计）

**设计思想**：一个 Agent，分饰四角。每个角色有独立的职责提示词（`ROLE_PROMPTS`），由主控角色（制作人）按对话阶段调度。

| 角色 | 职责 | 切换时机 |
|---|---|---|
| 🎼 音乐制作人（主控） | 把模糊想法拆解为主题/情绪/场景/受众；规划创作流程；整合产出；术语通俗化 | 默认视角 |
| ✍️ 作词人 | 按 [Verse]/[Pre-Chorus]/[Chorus]/[Bridge] 结构写词；具象意象；押韵方案 | 用户要写词、改词 |
| 🎹 编曲师 | BPM/拍号/调式建议及情绪理由；和弦进行；配器分层；英文风格标签 | 用户聊和弦、节奏、配器 |
| 🎧 听众评审 | 以普通听众视角检验：10 秒记忆点、情绪起伏、金句；输出 3 条优先级改进建议 | 一稿完成后自动自查 |

**固定输出规范**（写进系统提示词，保证每稿结构一致）：

1. 需求解读 — 一句话复述
2. 风格提示词 — 中文描述 + 英文 style tags（可直接粘贴 Suno）
3. 歌词 — 带 `[Verse]/[Chorus]/[Bridge]` 段落标注
4. 编曲建议 — BPM、调式、和弦进行、配器分层，各附通俗解释
5. 评审意见 — 优点 2 条 + 按优先级排序的改进建议 3 条

## 4. 音乐风格提示词库

每种风格是一组**音乐学参数**（`GENRE_PROFILES`），命中关键词即自动注入系统提示词：

| 风格 | 识别关键词示例 | 核心参数（节选） |
|---|---|---|
| 抒情 Ballad | 抒情 / 情歌 / 毕业 / 思念 / 治愈 | 60-80 BPM；钢琴+弦乐；1-5-6-4 卡农进行；副歌升调爆发；情绪曲线"克制→铺垫→释放→回味" |
| 摇滚 Rock | 摇滚 / 热血 / 呐喊 / 不认输 / 电吉他 | 120-160 BPM；失真双吉他+贝斯+鼓；强力和弦；riff 前奏 + solo 桥段；"压迫→蓄力→爆发→更大爆发" |
| 流行 Pop | 流行 / 主打歌 / 洗脑 | 95-120 BPM；合成器+鼓机；钩子前置；副歌第一句即记忆点 |
| 电子 EDM | 电音 / 舞曲 / 蹦迪 | 124-128 BPM；Build-up→Drop 结构；侧链压缩；短句 hook |
| 国风 | 国风 / 古风 / 戏腔 / 江湖 | 五声调式；古筝/琵琶/竹笛+现代节奏组；意象化歌词 |
| 民谣 Folk | 民谣 / 木吉他 / 叙事 / 远方 | 80-105 BPM；木吉他分解和弦；第一人称叙事 |
| 说唱 Hip-hop | 说唱 / 饶舌 / rap / flow | 80-95 BPM；808 贝斯；单押/双押方案；主歌 rap + 副歌演唱 hook |

未命中任何风格时，仅使用基础角色提示词，由制作人自由发挥并向用户追问。

**扩展新风格只需两步**：在 `GENRE_PROFILES` 加一个 `GenreProfile(name, keywords, prompt)`，重启后端即可。

## 5. 功能实现步骤（已完成的）

1. **搭骨架**：FastAPI + React 全栈模板，登录/注册（JWT）、会话表 `AgentSession`（Alembic 迁移）。
2. **聊天链路**：`POST /api/v1/agent/chat` → LangChain → DashScope；前端 DeepSeek 式界面（会话侧栏、Markdown 渲染、失败重试）。
3. **提示词工程**：`prompt_templates.py` 实现 `ROLE_PROMPTS`（四角色）+ `GENRE_PROFILES`（七风格）+ `detect_genre()` 关键词识别；`get_system_prompt(user_input)` 组装"基础角色 + 风格参数"。
4. **文件上传与多模态**：`/chat` 改 multipart；图片 → base64 data URL 进多模态消息；txt/md → RAG。
5. **RAG 知识库**：Chroma 持久化向量库（`langchain-chroma`），文档按会话隔离入库，检索"本会话 + 全局"片段注入 prompt；`build_knowledge_base.py` 批量导入全局知识库（已内置《零基础音乐创作速查》）。
6. **健壮性**：文件类型/数量/大小校验（415/413）；检索失败不阻塞对话；空回复与 token 截断显式报错；失效 token 自动登出。

## 6. 后续路线图（实现步骤）

| 阶段 | 内容 | 关键步骤 |
|---|---|---|
| P1 结构化输出 | 把五大板块输出为 JSON（Pydantic schema），前端分卡片渲染 | ① 定义 `MusicProposal` 模型 ② prompt 中给出 JSON Schema 示例 + `with_structured_output` ③ 前端卡片化 |
| P2 多角色编排 | 四角色拆成独立 Agent 流水线（LangGraph） | ① 制作人产出任务计划 ② 词/曲并行执行 ③ 评审角色打分回环 ④ 汇总输出 |
| P3 接入音乐生成 | 调 Suno / ACE-Step 等 API 直接出 demo 音频 | ① 风格提示词→API 参数映射 ② 异步任务+轮询 ③ 前端播放器 |
| P4 风格样本库 | 用经典曲目分析文档扩充 RAG 知识库 | ① 每风格整理 3-5 首代表作分析 ② 放入 `knowledge_base/` 跑导入脚本 |
| P5 迭代优化 | 用户反馈驱动改稿 | 会话内"按评审意见自动改第二稿"按钮 |

## 7. 快速开始

```bash
# 1. 依赖服务（PostgreSQL）
docker compose up -d db

# 2. 后端（务必用 uv 启动，conda 环境缺依赖）
cd backend
uv sync
cp .env.example .env   # 填入 DASHSCOPE_API_KEY
uv run python -m uvicorn app.main:app --reload

# 3. 前端（另开终端）
cd frontend
pnpm install
pnpm dev                # http://localhost:5173
```

默认管理员：`admin@example.com / changethis`（来自 `.env` 的 `FIRST_SUPERUSER*`，部署前务必修改）。

**环境变量**（均有默认值，按需覆盖）

| 变量 | 默认 | 说明 |
|---|---|---|
| `DASHSCOPE_API_KEY` | 无 | DashScope API Key（LLM + 向量化共用） |
| `AGENT_MODEL_NAME` | `deepseek-v4-pro-0813` | 对话模型 |
| `AGENT_MAX_TOKENS` | `4096` | 输出上限（歌词+编曲较长，建议 ≥2048） |
| `AGENT_MAX_UPLOAD_FILES` / `AGENT_MAX_UPLOAD_SIZE_MB` | 5 / 10 | 上传限制 |
| `RAG_EMBEDDING_MODEL` | `text-embedding-v4` | 向量化模型 |
| `RAG_TOP_K` / `RAG_CHUNK_SIZE` | 4 / 500 | 检索条数 / 切块大小 |

## 8. API 一览

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/v1/agent/chat` | 对话（multipart：`user_input` + `session_id?` + `files[]`） |
| GET | `/api/v1/agent/sessions` | 会话列表 |
| DELETE | `/api/v1/agent/sessions/{id}` | 删除会话 |
| POST | `/api/v1/users/signup` · `/api/v1/login/access-token` | 注册 / 登录 |

## 9. 使用示例

```
你   ：帮我写一首毕业季的抒情歌，送给同班同学
Agent：## 需求解读 … ## 风格提示词（中文描述 + 英文标签
       emotional graduation ballad, piano-led, warm strings…）
       ## 歌词 [Verse]教室后排的风扇… [Chorus]别来无恙…
       ## 编曲建议 72 BPM，C 大调，1-5-6-4…
       ## 评审意见 优点 2 条 / 改进建议 3 条

你   ：副歌再燃一点，改成摇滚版
Agent：（自动切换摇滚风格库 + 编曲师视角，重写风格标签与配器）
```

RAG 用法：把歌词草稿/需求文档拖进输入框上传，之后在本会话内的提问都会自动检索引用。

## 10. 已知注意事项

- **跑 pytest 会清空开发数据库**（测试 fixture 复用 `DATABASE_URL`）：跑完测试执行 `uv run python app/initial_data.py` 恢复管理员；生产环境务必给测试配独立数据库。
- 后端必须用 `uv run` 启动（依赖在 `.venv`）；conda base 环境缺 `langchain-chroma` 等新包。
- 带代理的开发环境中，LLM/向量化客户端已配置直连 DashScope（`trust_env=False`），无需全局代理。
