# 音乐创作 Agent · 后端

基于 FastAPI 的后端服务:账号体系 + 音乐创作 Agent(对话 Agent 与多 Agent 创作流水线)、
语音、RAG、MCP、可观测性等模块均在 `app/` 下。

## 环境要求

* [Docker](https://www.docker.com/)(用于 PostgreSQL、Mailpit 等支撑服务)。
* [uv](https://docs.astral.sh/uv/):Python 依赖与虚拟环境管理。

## 本地开发

后端本地运行,数据库可连接 Docker Compose 里的 PostgreSQL,或连接 Windows 上安装、
暴露给 WSL2 的 PostgreSQL。

### PostgreSQL on Windows 搭配 WSL2

后端从仓库根目录的 `.env` 读取 `DATABASE_URL`。PostgreSQL 安装在 Windows 上时,
将该 URL 配置为 WSL2 内可达的 Windows 主机地址:

```dotenv
POSTGRES_USER=postgres
POSTGRES_PASSWORD=your-password
POSTGRES_DB=app
DATABASE_URL=postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@localhost:5432/${POSTGRES_DB}
```

WSL2 的 localhost 转发启用时,`localhost` 即可;否则使用 `/etc/resolv.conf`
中 `nameserver` 条目显示的 Windows 主机地址,例如:

```dotenv
DATABASE_URL=postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@172.25.224.1:5432/${POSTGRES_DB}
```

Windows 上的 PostgreSQL 需要监听该地址,且 `pg_hba.conf` 允许 WSL2 网段。典型配置:

```conf
# postgresql.conf
listen_addresses = '*'

# pg_hba.conf - 网段替换为 WSL2 内 `ip route` 显示的值
# 应用库不存在时,还需要维护库 "postgres" 的访问权限
host    all    postgres    172.16.0.0/12    scram-sha-256
```

同时在 Windows 防火墙放行 TCP `5432` 端口并重启 PostgreSQL 服务。
在 PowerShell 中查找并启动服务:

```powershell
Get-Service | Where-Object {$_.Name -match "postgres"}
Start-Service <service-name>
```

然后在仓库根目录,创建配置的数据库(如不存在)、执行全部迁移并创建初始超级用户:

```console
$ bash scripts/setup-db-wsl.sh
```

该脚本会连接 PostgreSQL 的 `postgres` 维护库来创建 `POSTGRES_DB`,
因此所配置的用户需要有建库权限。脚本可重复执行,安全幂等。

改用 Docker Compose 数据库时,启动 PostgreSQL 与 Mailpit:

```console
$ docker compose up -d db mailpit
```

随后在 `./backend/` 目录安装依赖、准备数据库并启动开发服务器:

```console
$ uv sync
$ uv run bash scripts/prestart.sh
$ uv run fastapi dev
```

API 位于 `http://localhost:8000`,自动交互式文档在 `http://localhost:8000/docs`。

## 通用开发流程

后端命令都在 `./backend/` 目录下通过 `uv run` 执行;确保编辑器使用项目内
`.venv/bin/python` 解释器。

- 数据模型与 SQL 表:`./backend/app/models.py`(SQLModel)
- API 路由:`./backend/app/api/` 与 `./backend/app/agent/routes*.py`
- CRUD 工具:`./backend/app/crud.py`
- Agent 相关:`./backend/app/agent/`(详见下文「Music Agent」一节)

## VS Code

项目已内置 VS Code 调试配置,可直接打断点、暂停、查看变量;
Python 测试面板也已配置,可直接运行测试。

## Docker Compose 整栈运行

用 Docker Compose 运行后端与构建好的前端:

```console
$ docker compose run --rm backend bash scripts/prestart.sh
$ docker compose watch
```

应用位于 `http://localhost:8000`。

### Docker Compose Override

`compose.override.yml` 包含本地开发的端口发布、源码同步、自动镜像重建与后端热重载配置。
直接运行 `docker compose` 而不显式指定文件列表时,会自动应用该文件。

进入后端容器 shell:

```console
$ docker compose exec backend bash
```

## 后端测试

在 `backend` 目录运行:

```console
$ uv run bash scripts/test.sh
```

测试基于 Pytest,位于 `./backend/tests/`,可修改或新增。
若使用 GitHub Actions,测试会在 CI 中自动运行。

测试自动使用独立的 `app_test` 数据库(不存在时自动创建、跑迁移):
conftest 在会话结束时会清空 `User` / `Item` 表,独立数据库保证开发库数据
(超级用户、Agent 会话等)不受任何 pytest 入口影响。

### 对运行中的整栈跑测试

整栈已启动时,可以直接:

```bash
docker compose exec backend bash scripts/tests-start.sh
```

`/app/backend/scripts/tests-start.sh` 会确认整栈就绪后调用 `pytest`;
需要给 pytest 传参时,直接附加在命令后即可,例如首个错误即停:

```bash
docker compose exec backend bash scripts/tests-start.sh -x
```

### 测试覆盖率

测试运行后会生成 `htmlcov/index.html`,浏览器打开即可查看覆盖率报告。

## 数据库迁移

每次修改模型后,都要创建一个新的迁移版本并升级数据库。在 `backend` 目录用 `uv`
运行 Alembic(数据库使用 Compose 中的 PostgreSQL):

* Alembic 已配置为自动从 `./backend/app/models.py` 导入你的 SQLModel 模型。

* 修改模型(例如新增列)后,创建迁移版本:

```console
$ uv run alembic revision --autogenerate -m "Add column last_name to User model"
```

* 把 alembic 目录下生成的文件提交到 git 仓库。

* 然后执行迁移(真正修改数据库的一步):

```console
$ uv run alembic upgrade head
```

如果完全不想用迁移,可以取消 `./backend/app/core/db.py` 中以下行的注释:

```python
SQLModel.metadata.create_all(engine)
```

并注释掉 `scripts/prestart.sh` 中包含 `alembic upgrade head` 的行。

如果想从默认模型起步并彻底重构(没有任何历史版本),可以删除
`./backend/app/alembic/versions/` 下的版本文件(`.py`),再按上述方式创建首个迁移。

## 邮件模板

邮件模板用 [React Email](https://react.email) 编写,位于 `./packages/react-email/`:
`emails` 目录每封邮件一个组件,`ui` 目录是共享组件(layout、heading、button、link、callout)。

`./backend/app/email-templates/` 中的 HTML 由这些组件生成,是应用实际发送的内容,
请勿手工编辑。

编辑时预览邮件,在项目根目录启动开发服务器:

```console
$ bun run email:dev
```

来自后端的取值以 Jinja 占位符的形式写在组件 props 中,例如 `username = "{{ username }}"`;
每封邮件的上下文在 `./backend/app/utils.py` 的 `generate_*_email()` 中组装,
新增占位符时需要同步修改。

完成后,重新生成应用使用的模板:

```console
$ bun run email:export
```

## Music Agent：多 Agent 创作流水线

`/api/v1/agent` 下的音乐创作能力分两层：

- **对话 Agent**（`POST /chat`）：单 Agent + 工具调用循环，含乐理查询与创作类工具。
- **多 Agent 创作流水线**（`POST /compose`）：LangGraph 编排，按"分角色创作"方法论推进。

### 创作流水线（POST /api/v1/agent/compose）

请求体为 multipart 表单：`request_json` 字段携带特征清单 JSON，`audio` 为可选音频：

```json
{
  "key": "C", "mode": "major", "bpm": 96,
  "mood": "温暖的毕业纪念", "style": "ballad",
  "bars": 8, "beats_per_bar": 4,
  "user_notes": "送给即将毕业的朋友", "session_id": null
}
```

上传音频时 key/mode/bpm 可省略，由听觉解析自动检测。

响应为 SSE 事件流：`session` → `stage`×N → `done` / `error`。每个节点完成时发一条
`stage` 事件（`node` / `stage` 角色标签 / `detail` 进展说明），前端据此渲染各角色进展。

节点编排与通信约定（`app/agent/composer.py`）：

```
START → 和声 Agent → 乐理校正（mcp42） → 旋律 Agent → [贝斯 ∥ 鼓]（并行） → 评审 Agent →（不通过则返工旋律）→ 渲染 → END
```

- 通信采用共享状态板：`MusicBlueprint` 是唯一媒介，各节点只读写自己负责的字段；
  大产物（MIDI）落盘到 `AGENT_SCORES_DIR`（默认 `backend/generated_scores/`），状态里只传路径。
- LLM 不可用或输出无效时，每个节点回退到确定性模板，流水线始终可跑通。
- 评审不通过会回到旋律节点返工，最多 `AGENT_MAX_REVISIONS`（默认 2）轮后仍渲染交付。
- **乐理校正节点**：调用 mcp42 公共 MCP 服务（`analyze-progression`）校验和弦进行的
  调性、罗马数字与终止式，结果注入评审与 SSE stage 事件；服务不可达或超时
  （`AGENT_MCP42_TIMEOUT`，默认 20s）时静默跳过，不阻塞流水线。
- **听觉解析（第 1 阶段）**：`POST /compose` 为 multipart 请求，可选上传音频文件
  （wav/mp3/flac/ogg/aac/m4a，字段名 `audio`）。上传后调用本地 audio-analyzer MCP
  服务（`AGENT_AUDIO_ANALYZER_COMMAND`，stdio）提取节奏骨架（BPM/稳定性）与和声
  骨架（调性/置信度），自动填入特征清单；请求里显式给出的 key/mode/bpm 优先于解析
  结果。解析失败不阻塞创作（stage 事件会说明"不可用"）。
- **音频渲染**：MIDI 落盘后自动用 fluidsynth + FluidR3_GM 音色库渲染、lame 压缩为
  MP3（`AGENT_ENABLE_MP3`，默认开启；缺 lame 时输出 WAV，缺 fluidsynth 时跳过）。
- **产物下载**：`GET /api/v1/agent/scores/{filename}`（需登录）返回 MP3/MIDI 文件，
  前端 `<audio>` 可直接播放；文件名做了目录穿越防护。

### 创作工具（`app/agent/tools/composition.py`）

`generate_chord_progression`（按调式情绪生成进行）、`generate_bass_line`（根音节奏型）、
`generate_drum_pattern`（GM 鼓谱节奏型）、`validate_melody`（调内校验）、`render_midi`
（四轨总谱导出，旋律/和声/贝斯/鼓，鼓组走 MIDI channel 10）。全部同时注册为
对话 Agent 的 LangChain 工具。

环境变量：`AGENT_SCORES_DIR`（MIDI 输出目录）、`AGENT_MAX_REVISIONS`（评审返工轮数上限）。

## 架构增强模块

### 全链路可观测性矩阵日志（`app/core/observability.py`）

零第三方依赖（纯标准库）的三层可观测性：

1. **Trace/Span 追踪**：`trace_id` 用 ContextVar 贯穿请求全程，`traced_span()` /
   `@traced` 在任意层埋点（HTTP → Agent 节点 → LLM → 工具 → RAG → MCP → 音频），
   span 树存环形缓冲（512 条），可按 trace_id 回放整条链路。中间件自动生成
   trace_id（或接续 `X-Request-ID`），响应头回传 `X-Trace-ID`。
2. **矩阵日志**：root logger 输出单行 JSON（`ts/level/logger/msg/trace_id/span` +
   extra 矩阵字段），机器可聚合、人可读。
3. **指标**：进程内计数器、延迟直方图（p50/p95/max）、路由×阶段耗时矩阵。

查询接口（仅超级管理员）：

- `GET /api/v1/observability/metrics` — 指标快照
- `GET /api/v1/observability/traces?limit=50` — 最近链路摘要
- `GET /api/v1/observability/traces/{trace_id}` — 单条链路完整 span 树

埋点覆盖：`http.request`、`rag.hybrid_search`、`mcp.discover`、`mcp.call`、
`mcp42.analyze_progression`、`stt.transcribe`、`tts.synthesize` 等。

### MCP 工具集成框架（`app/mcp/` + `app/agent/mcp_registry.py`）

**服务端**（`AGENT_ENABLE_MCP_SERVER`，默认开启）：`app/mcp/server.py` 用 FastMCP
把乐理/创作/RAG 工具发布为标准 MCP Server，以 Streamable HTTP 挂载在 `/mcp`，
任何 MCP 客户端都可发现并调用（共 8 个工具）。门面层只做协议适配，底层实现与
LangChain 工具同源，两套暴露零漂移。

**客户端注册中心**（`mcp_registry`）：配置驱动的多 Server 接入：

```dotenv
# 远程 HTTP 与本地 stdio 命令可混配
AGENT_MCP_SERVERS=[
  {"name": "music-wiki", "url": "https://wiki.example/mcp", "timeout": 10},
  {"name": "audio", "transport": "stdio", "command": "/opt/audio-analyzer"}
]
```

- **发现**：启动时（`AGENT_ENABLE_MCP_TOOLS=true` 时）或 `POST /api/v1/agent/mcp/refresh`
  触发 `list_tools`，工具自动桥接为 LangChain 工具并入对话 Agent 的工具循环；
- **调用**：超时降级（单 Server 失败不影响全局），结构化内容优先返回；
- **状态**：`GET /api/v1/agent/mcp/servers` 查看注册表与已发现工具。

内置 mcp42 乐理校正服务（`AGENT_MCP42_URL`）即为注册中心的一个普通条目；
`mcp_clients.py` 门面与 `mcp_audio.py` 听觉解析都复用同一传输层语义。

### 语音捕捉处理与语音输出（`app/agent/speech.py` + `routes_voice.py`）

- **捕捉处理（STT）**：`POST /api/v1/agent/voice/transcriptions`（multipart，字段
  `audio`，可选 `language`）。浏览器麦克风录音（webm/ogg）与常见音频
  （wav/mp3/m4a/flac/aac）统一走 OpenAI 兼容 `/audio/transcriptions`
  （DashScope ASR，模型 `AUDIO_STT_MODEL`，默认 `qwen3-asr-flash`）。
- **语音输出（TTS）**：`POST /api/v1/agent/voice/speech`（JSON `{text, voice?,
  response_format?}`）返回音频流；相同文本+音色命中内存 LRU 缓存（32 条/8MB）
  免重复合成。
- **能力协商**：`GET /api/v1/agent/voice/config` 返回允许的录音格式、默认音色
  （`AUDIO_TTS_VOICE`，默认 Cherry）、长度与大小上限，前端据此渲染录音按钮。

三个接口均需登录；未配置 API Key 返回 503，上游失败返回 502。

### 向量库自动优化与 RAG 召回率（`app/rag/hybrid|evaluation|optimizer.py`）

**混合检索**（`retriever.hybrid_search`）：向量召回 + BM25 词法召回（自实现 Okapi
BM25，中文"ASCII 词 + CJK 单字/二元组"分词）→ RRF 按排名融合。两路互补：向量管
语义，BM25 管精确术语（如"4536 进行"）；任一路失败自动降级为另一路。

**评估器**（`evaluation.py`）：从语料自动构建 golden 集（按块术语生成查询），计算
recall@k 与 MRR；`HashEmbeddings` 提供确定性离线嵌入，测试全程不触网。

**自动调参器**（`python -m app.rag.optimizer`）：网格搜索 `top_k / fetch_k /
vector_weight / bm25_weight`，目标 recall ≥ 0.90；达标组合中取 MRR 最高者
（打平时优先保留混合冗余，防嵌入模型漂移），最优参数持久化到
`app/rag/rag_tuning.json`，retriever 启动时热加载。当前知识库语料上：

```
recall@4 = 1.00，mrr = 1.00（top_k=4, fetch_k=12, vector=1.0, bm25=0.5）
```

注意：chunk_size/chunk_overlap 属于入库参数（变更需重建索引），保持在
`RAG_CHUNK_SIZE` 等环境变量中，不进自动调参；golden 集是回归基线，上线后应
用真实问答对持续补充。

### Agent 治理体系（`app/agent/intent|memory|review|evaluation|governance.py`）

对话 Agent 的五项治理能力：意图识别、提示词工程、上下文工程、人工干预、
评估与反馈闭环。数据落在 5 张新表（`agent_session_memory` /
`agent_feedback` / `review_case` / `agent_improvement_note` /
`agent_evaluation_run`），`agent_message` 新增 `intent` /
`intent_confidence` / `prompt_version` 标记列。

**意图识别**（`intent.py`，每轮对话先于生成执行）：

- 规则快通道：7 类意图（歌词创作/编曲乐理/完整创作/乐理问答/转人工/反馈投诉/闲聊）
  关键词计分，命中越多置信度越高，未命中返回低置信 chat；
- LLM 精分类：规则置信度低于 `AGENT_INTENT_CONFIDENCE_THRESHOLD`（默认 0.55）时
  用一次低成本 LLM 调用复核，超时/失败/解析失败一律回退规则结果——意图识别
  永不阻塞或中断对话；
- 敏感检测独立于意图：命中敏感词直接标记 `sensitive`，触发人工升级。

**提示词工程**（`prompt_templates.py`）：`build_system_prompt()` 四层组装——
角色设定 + 风格参数库（原有一层）→ 意图指南（按意图识别结果切换行为策略）
→ 运营改进项（人工坐席沉淀，激活后自动注入）。`PROMPT_VERSION`（当前
`2.1.0`）随助手消息落库，评估与反馈按版本分组，支撑"改提示词 → 对比指标"。

**上下文工程**（`memory.py`）：模型可见上下文按"人工坐席指导 > 会话滚动摘要 >
用户偏好 > RAG 检索资料"优先级、在 `AGENT_CONTEXT_TOKEN_BUDGET`（默认 3000
token）预算内装配。长对话把窗口外旧消息压成摘要（LLM 后台任务，失败只影响
摘要新鲜度）；规则抽取用户"喜欢/不喜欢"等偏好跨轮注入。

**人工干预岗位**（`review.py` + `governance.py`）：

- 升级触发：敏感内容、明确转人工、非闲聊意图置信度过低（chat 流程自动建单）；
  用户点踩（`auto_feedback`）；用户主动申请（界面"转人工"按钮或
  `POST /interventions`）。每会话同时最多一张未结工单；
- 坐席处理（superuser）：`GET /interventions` 工单队列 → 工单详情与会话消息 →
  `POST /interventions/{id}/handle`。`action=in_progress` 为坐席接管，
  `guidance`（接管指令）自动注入该会话后续每轮对话的系统提示；结单后停止注入。

**评估与反馈闭环**（`evaluation.py`）：

- 用户反馈：`POST /feedback` 对单条助手消息点赞/点踩（可附评语），按消息的
  意图/提示词版本降维聚合存储；点踩自动升级人工；
- 离线评估：`POST /evaluations/run`（superuser）抽样最近回复，LLM-as-judge 按
  相关性/专业性/可执行性/清晰度四维打分（0-10），聚合报告（总体均分、按意图、
  按提示词版本分组）持久化到 `agent_evaluation_run`；
- 闭环：`GET /evaluations/summary` 总览报表定位薄弱意图 → 坐席沉淀改进项
  （`POST /improvement-notes`，激活后自动注入系统提示）→ 再次评估对比。

配置项：`AGENT_ENABLE_LLM_INTENT`（默认开）、`AGENT_INTENT_LLM_TIMEOUT`（8s）、
`AGENT_INTENT_CONFIDENCE_THRESHOLD`（0.55）、`AGENT_ENABLE_AUTO_ESCALATION`（默认开）、
`AGENT_CONTEXT_TOKEN_BUDGET`（3000）。测试见 `tests/agent/test_intent|memory|evaluation.py`
与 `tests/api/routes/test_governance.py`（覆盖"点踩 → 工单 → 坐席接管 → 指导注入
下一轮对话"的完整闭环）。
