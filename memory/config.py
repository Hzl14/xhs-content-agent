from dataclasses import dataclass


@dataclass
class MemoryConfig:
    # 最近几轮保留完整原文进入 prompt
    short_term_active_turns: int = 7
    # 跨 session 最多引用几轮完整对话
    cross_session_full_turn_budget: int = 3
    # 超过多少轮触发正式摘要压缩
    formal_summary_trigger_turns: int = 14
    # prompt token 上限，超出触发压缩
    hard_token_limit: int = 18000
    # 前端展示最近 N 轮
    frontend_display_turns: int = 20
    # 跨 session 原文检索的强相关阈值（0~1）
    strong_related_threshold: float = 0.75
    # 跨 session 摘要检索的中等相关阈值（0~1）
    mid_related_threshold: float = 0.50
    # 记忆数据存储根目录
    memory_base_dir: str = "data/memory"
    # 模式反馈记忆：最多注入多少条活跃规则
    pattern_feedback_max_active_rules: int = 3
    # 模式反馈记忆：最多保留多少条近期失败/成功模式
    pattern_feedback_max_patterns: int = 5
    # 某维度连续达标多少次后，停用对应旧规则
    pattern_feedback_resolve_after_successes: int = 3
    # 每多少次写入触发一次压缩
    pattern_feedback_compact_every_updates: int = 10
    # 总分达到多少才沉淀成功模式
    pattern_feedback_success_threshold: float = 85.0
