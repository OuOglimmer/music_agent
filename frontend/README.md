# 音乐创作 Agent 前端（Next.js）

基于 **Next.js 16（App Router）+ React 19 + Tailwind CSS v4 + shadcn/ui + TanStack Query** 重构的前端，
对接 FastAPI 后端（`/api/v1`）。

## 页面结构

| 路由 | 说明 |
| --- | --- |
| `/dashboard` | 音乐创作工作台（三栏式，Suno 风格） |
| `/chat` | 独立 AI 对话页（流式 SSE + 图片/文档附件） |
| `/items` `/admin` `/settings` | 项目数据 / 用户管理 / 账号设置 |
| `/login` `/signup` `/recover-password` `/reset-password` | 认证相关页面 |

## 音乐工作台（三栏布局）

- **左栏 · 项目与资源库**：项目管理（Agent 会话，选中即切换上下文）、素材上传
  （图片 / txt / md，随下一条消息发送）、作品库（生成的乐曲，MP3 试听 / MIDI 下载）。
- **中栏 · 创作中心**（三个标签页）：
  - **创作**：五种风格卡片（流行 / 抒情 / 摇滚 / 电音 / 民谣，选中自动联动 BPM / 调式 / 情绪）、
    情绪标签、调性与调式、速度 / 小节滑杆、拍号、创作备注，点击「开始创作」走多 Agent 流水线
    （和声 → 旋律 → 贝斯 → 鼓 → 评审 → 渲染，SSE 实时进度）。
  - **对话**：与 Agent 流式对话，支持从左栏附加素材。
  - **歌词**：结构化歌词编辑器（`[Verse]` `[Chorus]` 等段落标签、按项目自动保存）、AI 写歌词（流式）。
- **右栏 · 播放器**：真实音频波形（AudioContext 解码 + Canvas 渲染，可点击/拖拽跳转）、
  播放控制（上一首 / 播放暂停 / 下一首 / 循环 / 音量）、和弦进行展示、MP3 / MIDI 下载。
  仅 MIDI 的作品展示确定性示意波形。

## 开发

```bash
pnpm install
pnpm dev        # http://localhost:3000，/api/v1 通过 next.config.ts rewrites 代理到 localhost:8000
```

可选环境变量：`BACKEND_URL`（默认 http://localhost:8000）、`NEXT_PUBLIC_API_URL`（默认同源）。

## 其他

- 重新生成 API client：`pnpm generate-client`（需后端已启动）
- 生产构建：`pnpm build && pnpm start`（默认 <http://localhost:3000>）
- 代码检查：`pnpm lint`
