# 演示说明

## 启动

```bash
uv sync
copy .env.example .env
uv run uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

打开：

```text
http://127.0.0.1:8000/static/generate.html
```

## 推荐演示话术

输入示例：

```text
帮我根据这些小红书内容，写一篇适合大学生女生看的种草文案，并准备发布。
```

如果没有真实抓取数据，也可以在前端传入已有候选内容，或使用后端 mock/stub 测试说明链路。

## 演示路径

### 1. 生成草稿

用户提交任务后，系统会执行：

```text
PlannerAgent
  -> CrawlerAgent
  -> AnalysisAgent
  -> TopicAgent
  -> ContentAgent
  -> ReviewerAgent
```

如果用户说了“发布”，后端不会马上发布，而是把发布动作延后。

预期结果：

- 页面展示候选文案
- 返回 `WAITING_FOR_INPUT`
- 生成 `draft_package`
- 保存 `active_generation`

### 2. 预览草稿

在结果页可以查看：

- 标题
- 正文
- CTA
- 标签
- 配图建议
- 评分
- 草稿下载链接

可以说明：草稿包用 Markdown 和 JSON 双格式导出，方便用户下载，也方便后续接其他系统。

### 3. 修改草稿

用户输入：

```text
第1篇改得更真实一点，少一点营销感。
```

系统不会重新跑完整链路，而是通过 routing layer 识别为 `revise_active_generation`，直接进入修订流程。

预期结果：

- 复用上一轮 `PipelineState`
- 复用分析结果和当前草稿
- 生成新版文案
- 重新保存 active_generation

### 4. 确认发布

用户输入：

```text
发布第1篇
```

系统执行：

```text
routing layer
  -> confirm_publish
  -> PublisherAgent
  -> mock publish
```

预期结果：

- 返回 `stage=COMPLETED`
- 返回 `publish_record`
- `publish_id` 形如 `mock_xxxxxxxxxxxx`
- 清理 `active_generation`

### 5. 查看 trace

可以用返回的 `run_id` 请求：

```text
GET /trace/{run_id}
```

说明每个节点都有 trace，便于答辩时讲清楚系统为什么做出某个动作。

## 测试演示

运行：

```bash
uv run pytest tests
```

重点测试：

```text
tests/test_agent_run_publish_flow.py
tests/test_agent_loop.py
tests/test_task_routing_layer.py
tests/test_pending_task_helpers.py
tests/test_draft_service.py
tests/test_publisher_agent.py
tests/test_publish_confirmation.py
```

其中 `test_agent_run_publish_flow.py` 是最适合讲 MVP 闭环的测试：

1. 用户要求生成并发布
2. 系统先生成草稿并要求确认
3. 用户确认发布第 1 篇
4. 系统返回 mock 发布记录

## 答辩时可以强调的取舍

- 没有用 LangChain，而是自己写 AgentLoop，能证明对状态机和 Agent 编排的理解
- 质量审查集中在 ReviewerAgent，避免每个节点都变成小判官
- 发布动作必须二次确认，体现产品安全意识
- `session_id/task_id/run_id` 分离，解决多轮任务续接问题
- mock Publisher 让系统闭环先跑通，后续替换真实发布接口即可

## 当前边界说明

演示时建议主动说明：

- Publisher 是 mock，不会真实发布到小红书
- 本地 session manager 适合 MVP，生产环境应换数据库
- 前端是 MVP 页面，重点展示流程，不是最终产品 UI
- LLM 质量依赖模型和 prompt，项目重点在 Agent 架构和状态流转
