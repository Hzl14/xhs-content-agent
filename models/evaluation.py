from pydantic import BaseModel, Field, computed_field


class PatternFeedback(BaseModel):
    failed_patterns: list[str] = Field(default_factory=list)
    successful_patterns: list[str] = Field(default_factory=list)
    dimension_rules: dict[str, str] = Field(default_factory=dict)
    top_priority: str = ""


class LLMJudgeResult(BaseModel):
    authenticity_score: float = 0.0
    tone_fit_score: float = 0.0
    ai_trace_score: float = 0.0
    main_issue: str = ""
    suggestion: str = ""


class ReviewCritique(BaseModel):
    hard_gate_passed: bool = True
    gate_failures: list[str] = Field(default_factory=list)
    hook_score: float = 0.0
    keyword_score: float = 0.0
    format_score: float = 0.0
    cta_score: float = 0.0
    authenticity_score: float = 0.0
    trend_alignment_score: float = 0.0
    audience_fit_score: float = 0.0
    llm_authenticity_score: float = 0.0
    llm_tone_fit_score: float = 0.0
    llm_ai_trace_score: float = 0.0
    llm_main_issue: str = ""
    total_score: float = 0.0
    weak_dimensions: list[str] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
    pattern_feedback: PatternFeedback = Field(default_factory=PatternFeedback)

    @computed_field
    @property
    def passed(self) -> bool:
        return self.hard_gate_passed and self.total_score >= 75.0
