from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
import uuid

from pydantic import BaseModel, Field

from models.evaluation import ReviewCritique
from models.states import PipelineStage, PlannedStageStatus


class NoteItem(BaseModel):
    title: str
    content: str
    likes: int = 0
    favorites: int = 0
    comments: int = 0
    tags: list[str] = Field(default_factory=list)
    author: str | None = None
    publish_time: str | None = None
    url: str | None = None
    content_type: str | None = None
    keyword_used: str | None = None
    keyword_type: str | None = None
    style_tag: str | None = None
    quality_signals: list[str] = Field(default_factory=list)
    image_urls: list[str] = Field(default_factory=list)
    video_urls: list[str] = Field(default_factory=list)
    media_summary: str | None = None


class StructuralPatterns(BaseModel):
    title_patterns: list[str] = Field(default_factory=list)
    top_keywords: list[str] = Field(default_factory=list)
    top_tags: list[str] = Field(default_factory=list)
    hook_words: list[str] = Field(default_factory=list)
    avg_title_length: int = 0
    avg_paragraph_count: int = 0
    uses_numbering: bool = False
    uses_emoji: bool = False
    ends_with_question: bool = False


class ContentInsights(BaseModel):
    dominant_narrative: str = ""
    core_user_pain: str = ""
    credibility_signals: list[str] = Field(default_factory=list)
    emotional_arc: str = ""
    reusable_expressions: list[str] = Field(default_factory=list)
    insight_points: list[str] = Field(default_factory=list)


class EngagementSignals(BaseModel):
    content_value_type: str = ""
    avg_collect_ratio: float = 0.0
    avg_comment_ratio: float = 0.0
    best_post_title: str = ""
    best_post_collect_ratio: float = 0.0
    best_post_key_features: list[str] = Field(default_factory=list)


class WritingStrategy(BaseModel):
    recommended_title_formula: str = ""
    opening_strategy: str = ""
    body_structure: list[str] = Field(default_factory=list)
    credibility_tactics: str = ""
    emotional_design: dict[str, str] = Field(default_factory=dict)
    closing_cta: str = ""
    tag_strategy: dict[str, Any] = Field(default_factory=dict)
    avoid_patterns: list[str] = Field(default_factory=list)
    must_include_elements: list[str] = Field(default_factory=list)


class AnalysisResult(BaseModel):
    summary: str = ""
    sample_size: int = 0
    analyzed_at: datetime = Field(default_factory=datetime.utcnow)
    structural_patterns: StructuralPatterns = Field(default_factory=StructuralPatterns)
    content_insights: ContentInsights = Field(default_factory=ContentInsights)
    engagement_signals: EngagementSignals = Field(default_factory=EngagementSignals)
    writing_strategy: WritingStrategy = Field(default_factory=WritingStrategy)

    # Compatibility fields used by existing topic/review/evaluation flows.
    top_keywords: list[str] = Field(default_factory=list)
    top_tags: list[str] = Field(default_factory=list)
    title_patterns: list[str] = Field(default_factory=list)
    insight_points: list[str] = Field(default_factory=list)


class TopicItem(BaseModel):
    title: str
    reason: str


class ContentItem(BaseModel):
    title: str
    body: str
    hashtags: list[str] = Field(default_factory=list)
    cta: str
    image_suggestion: str = ""
    content_type: str = "分享"


class GeneratedTopicWithContents(BaseModel):
    topic: TopicItem
    contents: list[ContentItem] = Field(default_factory=list)
    critique: ReviewCritique | None = None


class DraftPackage(BaseModel):
    draft_id: str
    json_path: str
    markdown_path: str
    json_url: str
    markdown_url: str
    content_count: int = 0


class PublishRecord(BaseModel):
    publish_id: str
    status: str = "simulated_success"
    platform: str = "xhs"
    mode: str = "mock"
    title: str = ""
    content_preview: str = ""
    hashtags: list[str] = Field(default_factory=list)
    selected_index: int | None = None
    task_id: str = ""
    run_id: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)


class PlannedStageItem(BaseModel):
    stage: PipelineStage
    status: PlannedStageStatus = PlannedStageStatus.PENDING


class PipelinePlan(BaseModel):
    intent: str = "generate_content_from_trends"
    needs_clarification: bool = False
    clarification_question: str = ""
    clarification_fields: list[str] = Field(default_factory=list)
    clarification_tips: str = ""
    topic_seed: str = ""
    planned_stages: list[PlannedStageItem] = Field(default_factory=list)
    needs_crawl: bool = True
    needs_analysis: bool = True
    needs_topic_generation: bool = True
    needs_content_generation: bool = True
    needs_review: bool = True
    needs_publish: bool = False
    search_query: str = ""
    search_keywords: list[str] = Field(default_factory=list)
    audience: str = ""
    tone: str = ""
    topic_count: int = 3
    content_count_per_topic: int = 1


class TaskRoutingDecision(BaseModel):
    action: str = "new_task"
    confidence: float = 1.0
    reason: str = ""
    clarification_question: str = ""
    should_start_new_task: bool = True
    source: str = ""
    selected_index: int | None = None
    requires_replan: bool = True


class NodeTrace(BaseModel):
    node_name: str
    status: str = "success"
    latency_ms: float | None = None
    retry_count: int = 0
    review_score: float | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    error: str | None = None


class TraceSpan(BaseModel):
    trace_id: str
    span_id: str
    parent_span_id: str | None = None
    type: str
    name: str
    status: str = "success"
    input_summary: Any | None = None
    output_summary: Any | None = None
    latency_ms: float | None = None
    error: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: float | None = None


class PipelineState(BaseModel):
    run_id: str
    session_id: str = "default"
    task_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    mode: Literal["fast", "deep"] = "fast"
    stage: PipelineStage = PipelineStage.IDLE
    # 用于记忆系统：标识发起请求的用户，默认 anonymous 兼容无用户场景
    user_id: str = "anonymous"
    user_message: str = ""
    ai_message: str = ""
    search_query: str = ""
    search_keywords: list[str] = Field(default_factory=list)
    raw_crawl_limit: int = 12
    final_note_limit: int = 3
    min_final_note_count: int = 1
    audience: str = "大学生女性"
    tone: str = "真实分享"
    topic_count: int = 1
    content_count_per_topic: int = 1
    review_threshold: float = 65.0
    max_reflections: int = 0

    candidate_notes: list[NoteItem] = Field(default_factory=list)  # 前端检索回传的候选帖子，通常约 50 条
    input_notes: list[NoteItem] = Field(default_factory=list)  # 后端筛选后的分析样本，通常约 20 条
    analysis: AnalysisResult | None = None
    plan: PipelinePlan = Field(default_factory=PipelinePlan)
    task_routing: TaskRoutingDecision = Field(default_factory=TaskRoutingDecision)
    topics: list[TopicItem] = Field(default_factory=list)
    results: list[GeneratedTopicWithContents] = Field(default_factory=list)  # AI生成的话题和内容

    failed: bool = False
    error_message: str | None = None
    current_node: str | None = None
    traces: list[NodeTrace] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    # 记忆上下文字符串，由 ConversationManager 在 pipeline 开始前填充，
    # 供 TopicAgent / ContentAgent 拼接进 prompt
    memory_context: str = ""
    # 统一组装后的非 system 公共上下文，供所有调用 LLM 的 Agent 复用。
    llm_context: str = ""


class AnalyzeRequest(BaseModel):
    items: list[NoteItem]


class AnalyzeResponse(BaseModel):
    total_count: int
    top_keywords: list[str]
    top_tags: list[str]
    title_patterns: list[str]
    insight_points: list[str]
    summary: str


class TopicGenerateRequest(BaseModel):
    summary: str
    top_keywords: list[str]
    top_tags: list[str]
    title_patterns: list[str]
    insight_points: list[str]
    audience: str = "大学生女性"
    count: int = 10


class TopicGenerateResponse(BaseModel):
    topics: list[TopicItem]


class ContentGenerateRequest(BaseModel):
    topic: str
    reason: str
    audience: str = "大学生女性"
    tone: str = "真实分享"
    count: int = 3


class ContentGenerateResponse(BaseModel):
    contents: list[ContentItem]


class AgentRunRequest(BaseModel):
    # 传入 user_id 以启用跨 session 记忆；不传则使用 "anonymous"
    user_id: str = "anonymous"
    session_id: str = "default"
    task_id: str | None = None
    mode: Literal["fast", "deep"] = "fast"
    user_message: str = ""
    search_query: str = ""
    search_keywords: list[str] = Field(default_factory=list)
    raw_crawl_limit: int = 12
    final_note_limit: int = 3
    min_final_note_count: int = 1
    audience: str = "大学生女性"
    tone: str = "真实分享"
    topic_count: int = 1
    content_count_per_topic: int = 1
    review_threshold: float = 65.0
    max_reflections: int = 0
    items: list[NoteItem] | None = None
    candidate_notes: list[NoteItem] | None = None


class AgentRunResponse(BaseModel):
    run_id: str
    session_id: str = "default"
    task_id: str = ""
    stage: PipelineStage
    failed: bool
    error_message: str | None = None
    needs_clarification: bool = False
    clarification_question: str = ""
    clarification_fields: list[str] = Field(default_factory=list)
    clarification_tips: str = ""
    search_query: str = ""
    search_keywords: list[str] = Field(default_factory=list)
    input_note_count: int = 0
    analysis_summary: str = ""
    top_keywords: list[str] = Field(default_factory=list)
    top_tags: list[str] = Field(default_factory=list)
    title_patterns: list[str] = Field(default_factory=list)
    insight_points: list[str] = Field(default_factory=list)
    draft_package: DraftPackage | None = None
    publish_record: PublishRecord | None = None
    source_notes: list[NoteItem] = Field(default_factory=list)
    results: list[GeneratedTopicWithContents] = Field(default_factory=list)


class TraceResponse(BaseModel):
    run_id: str
    stage: PipelineStage
    success: bool
    total_tokens: int
    total_latency_ms: float
    nodes: list[NodeTrace]
    spans: list[TraceSpan] = Field(default_factory=list)
