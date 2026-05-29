# XHS Agent 重构流程文档（实习版）

## 0. 范围锁定

本项目仅做“实习可展示”的 Agent 核心能力，不追求全量业务。

本阶段包含：

1. 状态机编排框架
2. 主链路：`crawler -> analysis -> topic -> content -> reviewer`
3. Reflection 回退重写
4. Trace 可观测（`run_id`、耗时、重试次数、token）
5. 核心接口：`/agent/run`、`/trace/{run_id}`、`/health`

本阶段不做：

1. 飞书同步（全部排除）
2. 发布链路深度打磨（仅保留占位）
3. 重型平台化监控

## 1. 你必须亲手设计的部分

以下文件都已预留 `TODO(USER_DESIGN)`，你需要亲自实现：

1. `core/agent_loop.py`
2. `agents/reviewer_agent.py`
3. `core/agent_base.py`

你要完成的设计点：

1. 循环控制策略（阶段路由、失败重试、外层继续条件）
2. Reviewer 评分维度与 Reflection 重试策略
3. 每个节点的 Trace 指标增强逻辑

## 2. 阶段计划

### 阶段 A：骨架可跑（已完成）

1. 新架构目录已建立
2. 入口为 `main.py` + `run.py`
3. 前端 `static/` 保持不变

### 阶段 B：主链路稳定

1. `/agent/run` 跑通并稳定
2. 失败时返回清晰错误信息
3. 结果结构可复用、可校验

### 阶段 C：Reflection 强化

1. 实现你自己的评分体系
2. 实现回退重写与停止条件
3. 调整 `review_threshold` 与 `max_reflections`

### 阶段 D：Trace 可讲故事

1. 定义核心指标
2. 确保每次运行可追踪
3. 输出可用于面试的数据结论

## 3. 实习版验收标准

1. 可演示一次完整运行：`/agent/run`
2. 可演示一次低分文案回退重写
3. 可演示 `run_id` 轨迹查询：`/trace/{run_id}`
4. 能提供“有/无 Reflection”的对比结果

