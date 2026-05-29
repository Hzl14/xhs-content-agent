# 架构说明

## 目标

本项目的目标不是做一个简单的“调用大模型写文案”接口，而是验证一个可解释、可续接、可回退的内容生产 Agent 系统。

核心问题：

- 用户语言很口语化，系统要判断是新任务还是继续旧任务
- 文案生成不是最终动作，发布前必须让用户确认
- 多 Agent 链路不能过度膨胀，否则状态会在长链路中失真
- 打分和质量审查应该集中在 Reviewer / Evaluation，而不是散落在每个节点里

## 主链路

```text
POST /agent/run
  -> load session state
  -> routing layer
  -> PlannerAgent
  -> defer publishing if needed
  -> AgentLoop
  -> export draft package
  -> save active_generation
  -> return response
```

如果用户确认发布：

```text
POST /agent/run
  -> routing layer detects confirm_publish
  -> select active draft candidate
  -> build publishing PipelineState
  -> PublisherAgent mock publish
  -> clear active_generation
  -> return publish_record
```

## Agent 职责

| Agent | 职责 |
| --- | --- |
| PlannerAgent | 识别任务意图，决定需要哪些 stage |
| CrawlerAgent | 获取候选小红书内容 |
| AnalysisAgent | 分析结构、关键词、标签、洞察 |
| TopicAgent | 生成可写的选题 |
| ContentAgent | 生成或修订文案 |
| ReviewerAgent | 打分、反写、判断是否通过 |
| PublisherAgent | 用户确认后执行发布动作，目前是 mock |

## AgentLoop 职责边界

AgentLoop 只负责：

- 按计划执行 stage
- 保存每一步状态
- 做必要的格式校验
- 对明显失败的 stage 做 retry
- 对 review 不通过的内容回到 ContentAgent

AgentLoop 不负责重度质量判断。原因是项目已经有专门的 ReviewerAgent 和 EvaluationEngine；如果每个节点都做复杂审查，会让链路变长，数据有效密度下降。

## 路由层

每次用户输入不会直接进入 PlannerAgent，而是先经过 routing layer。

判断顺序：

1. 读取 `session_id` 下的 `active_generation` 和 `pending_task`
2. 尝试高置信 hard rules
3. 无法确定时调用 LLM router
4. 通过 confidence gate 决定执行还是反问

典型动作：

- `new_task`
- `answer_pending_clarification`
- `revise_active_generation`
- `select_candidate`
- `confirm_active_generation`
- `confirm_publish`
- `abandon_task`
- `ask_clarification`

高风险动作使用更高阈值：

- 发布确认：高阈值
- 放弃任务：高阈值
- 修改草稿：中等阈值
- 选择候选：较低阈值

## 状态模型

| 字段 | 含义 |
| --- | --- |
| `session_id` | 一个前端窗口或对话会话 |
| `task_id` | 一个持续任务，例如某篇文案的生成/修改/发布 |
| `run_id` | 一次用户输入触发的一轮后端执行 |

额外状态：

- `active_generation`：当前已经生成、等待用户选择/修改/发布的草稿
- `pending_task`：系统上一轮提出反问，等待用户补充信息
- `PipelineState`：一次运行中的完整状态快照

这个设计让系统可以处理：

- 用户第二轮直接说“第2篇改得更真实一点”
- 用户说“可以发”
- 用户回答上一轮 clarification
- 用户另起一个新任务

## 发布安全门

PlannerAgent 可以识别用户“生成并发布”的意图，但系统不会在同一轮直接发布。

处理方式：

1. `needs_publish=True`
2. 后端将 `PUBLISHING` stage 延后
3. 执行抓取、分析、选题、写文案、review
4. 保存草稿为 `active_generation`
5. 返回 `WAITING_FOR_INPUT`
6. 用户明确确认后，才进入 PublisherAgent

这保证了用户在不可逆动作前能看到完整文案。

## 草稿包

`DraftService` 会把生成结果导出为：

- `draft.json`
- `draft.md`

前端拿到 `draft_package` 后可以提供下载链接。这样比直接把长文案塞在页面里更稳定，也适合包含配图建议、标签、多个候选等结构化内容。

## 当前 MVP 边界

真实生产中还需要补：

- 数据库持久化
- 真实小红书授权和发布接口
- 更完整的前端状态管理
- 更严格的权限和审计
- 更完整的失败重放和 trace 可视化

但对于实习项目 MVP，目前已经能展示完整的 Agent 产品闭环和关键工程取舍。
