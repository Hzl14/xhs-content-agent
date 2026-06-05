# XHS Content Agent

一个面向小红书内容生产的多 Agent MVP。项目不依赖 LangChain，而是用自定义 `AgentLoop`、状态模型和路由层，把“热帖抓取/分析/选题/写文案/审核/用户确认/模拟发布”串成一条可演示的闭环。

## 当前完成度

主功能已经进入可展示 MVP 阶段：

- 用户输入自然语言任务
- PlannerAgent 规划执行路线
- AgentLoop 按 stage 执行各 Agent
- ReviewerAgent / EvaluationEngine 对文案打分和反写
- 文案生成后暂停，等待用户预览、修改或确认
- 多轮对话通过 `session_id`、`task_id`、`run_id` 保持状态
- 草稿包导出为 JSON 和 Markdown
- 前端支持预览、修改、确认发布
- PublisherAgent 完成 mock 发布闭环

目前 Publisher 是模拟发布，不会真实调用小红书发布接口。

## 架构概览

```text
User
  -> POST /agent/run
  -> Routing Layer
       - hard rules
       - LLM router when needed
       - confidence gate
  -> PlannerAgent
  -> AgentLoop
       - CrawlerAgent
       - AnalysisAgent
       - TopicAgent
       - ContentAgent
       - ReviewerAgent
       - PublisherAgent only after user confirmation
  -> SessionManager / DraftService / Trace
  -> Frontend preview and next user action
```

核心目录：

```text
agents/      业务 Agent
api/         FastAPI 路由与 handler
core/        AgentLoop、状态机、评价引擎、基础 Agent
models/      Pydantic schema、stage、prompt、evaluation model
services/    LLM、session、draft、storage 等服务
memory/      会话记忆与 pattern feedback
static/      MVP 前端页面
tests/       核心链路测试
docs/        架构和演示说明
```

## 关键设计

### 1. AgentLoop

`core/agent_loop.py` 负责根据 PlannerAgent 给出的 stage 顺序执行节点。它只做流程控制和最低限度的格式校验，不承担复杂质量判断，避免链路过长导致状态失真。

质量判断主要交给：

- `ReviewerAgent`
- `EvaluationEngine`
- 可选的 LLM judge

### 2. 任务状态

项目把一次浏览器窗口、一个长期任务、一次用户输入分开建模：

- `session_id`：一个对话窗口或前端会话
- `task_id`：一个持续任务，例如“生成一篇并准备发布”
- `run_id`：每次 `/agent/run` 调用，也就是一次用户输入触发的一轮执行

这样用户第二轮说“改得更真实一点”时，系统可以继续使用上一轮的 PipelineState，而不是重新从主 Agent 完整规划。

### 3. 路由层

`run_agent_pipeline` 不是直接进 PlannerAgent，而是先判断当前输入属于：

- 新任务
- 回答上一轮反问
- 修改当前草稿
- 选择候选
- 确认发布
- 放弃任务

实现上采用：

- 高置信规则优先
- 必要时调用 LLM router
- 不同动作使用不同 confidence threshold
- 高风险动作如发布、放弃任务要求更高置信度

### 4. 发布前确认

如果用户一开始说“帮我生成并发布”，PlannerAgent 可以识别为 `needs_publish=True`，但后端会先把 `PUBLISHING` stage 延后：

```text
生成/审核完成
  -> 保存 active_generation
  -> 导出 draft package
  -> 返回 WAITING_FOR_INPUT
  -> 用户确认后才调用 PublisherAgent
```

这是产品安全逻辑：发布比生成更不可逆，必须让用户先看草稿。

## 快速启动

```bash
uv sync
copy .env.example .env
uv run uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

访问：

```text
http://127.0.0.1:8000/static/generate.html
```

健康检查：

```text
GET http://127.0.0.1:8000/health
```

## 常用接口

```text
POST /agent/run              主 Agent 入口
GET  /trace/{run_id}         查看一次运行 trace
GET  /drafts/...             下载导出的草稿包
POST /analysis/analyze       单独分析
POST /topics/generate        单独生成选题
POST /content/generate       单独生成文案
```

## 测试

推荐先跑核心 MVP 测试：

```bash
uv run pytest tests
```

当前重点覆盖：

- AgentLoop 回退和失败策略
- 任务路由层
- pending task / 多轮续接
- `session_id` / `task_id` / `run_id`
- 草稿包导出
- 发布前确认
- Publisher mock 发布
- 生成并发布的端到端 handler 流程

## 演示流程

1. 打开 `/static/generate.html`
2. 输入“帮我根据这些内容写一篇小红书文案并发布”
3. 系统生成草稿并进入确认页
4. 用户可以下载 Markdown/JSON 草稿包
5. 用户修改文案，系统回到 ContentAgent 生成新版
6. 用户确认发布
7. PublisherAgent 返回 mock `publish_id`

更详细的答辩说明见：

- [docs/architecture.md](docs/architecture.md)
- [docs/demo.md](docs/demo.md)

## MVP 边界

已经完成：

- Agent 编排
- 状态续接
- 文案审核和反写
- 用户确认发布
- 草稿导出
- 前端 MVP
- mock 发布闭环

暂未完成或刻意 mock：

- 真实小红书发布接口
- 真实账号授权
- 前端组件化重构
- 生产级持久化数据库
- 大规模并发调度
