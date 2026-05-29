from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from models.evaluation import ReviewCritique


@dataclass
class PatternRule:
    dimension: str
    rule: str
    active: bool = True
    low_count: int = 0
    success_count: int = 0
    last_score: float = 0.0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


@dataclass
class PatternFeedbackState:
    user_id: str
    version: int = 1
    update_count: int = 0
    rules: dict[str, PatternRule] = field(default_factory=dict)
    failed_patterns: list[str] = field(default_factory=list)
    successful_patterns: list[str] = field(default_factory=list)
    user_revision_preferences: list[str] = field(default_factory=list)
    updated_at: float = field(default_factory=time.time)


class PatternFeedbackStore:
    """
    Stores cross-topic writing rules extracted from review feedback.
    Content-level issues stay in the current review loop; only abstract rules live here.
    """

    score_fields = [
        "hook_score",
        "keyword_score",
        "format_score",
        "cta_score",
        "authenticity_score",
        "trend_alignment_score",
        "audience_fit_score",
        "llm_tone_fit_score",
        "llm_ai_trace_score",
    ]

    def __init__(
        self,
        base_dir: str = "data/memory",
        max_active_rules: int = 7,
        max_patterns: int = 10,
        resolve_after_successes: int = 3,
        compact_every_updates: int = 10,
        weak_threshold: float = 80.0,
        success_threshold: float = 85.0,
    ) -> None:
        self.base_dir = Path(base_dir)
        self.max_active_rules = max_active_rules
        self.max_patterns = max_patterns
        self.resolve_after_successes = resolve_after_successes
        self.compact_every_updates = compact_every_updates
        self.weak_threshold = weak_threshold
        self.success_threshold = success_threshold

    def update_from_critiques(self, user_id: str, critiques: list[ReviewCritique]) -> None:
        if not critiques:
            return

        state = self.load(user_id)
        changed = False
        for critique in critiques:
            changed = self._merge_critique(state, critique) or changed

        if not changed:
            return

        state.update_count += 1
        state.updated_at = time.time()
        if state.update_count % self.compact_every_updates == 0:
            self._compact(state)
        else:
            self._trim_patterns(state)
        self.save(state)

    def update_from_user_feedback(self, user_id: str, feedback: str) -> None:
        rules = self._extract_user_feedback_rules(feedback)
        if not rules:
            return

        state = self.load(user_id)
        changed = self._append_unique(state.user_revision_preferences, rules)
        if not changed:
            return

        state.update_count += 1
        state.updated_at = time.time()
        self._trim_patterns(state)
        self.save(state)

    def build_prompt_context(self, user_id: str) -> str:
        state = self.load(user_id)
        active_rules = [
            rule for rule in state.rules.values()
            if rule.active and rule.low_count > 0
        ]
        active_rules.sort(key=lambda item: (item.low_count, item.updated_at), reverse=True)
        active_rules = active_rules[: self.max_active_rules]

        parts: list[str] = []
        if active_rules:
            parts.append("[历史写作模式规则]")
            for rule in active_rules:
                parts.append(f"- {rule.dimension}: {rule.rule}")
        if state.failed_patterns:
            parts.append("\n[近期需避免的写作模式]")
            for pattern in state.failed_patterns[-self.max_patterns:]:
                parts.append(f"- {pattern}")
        if state.successful_patterns:
            parts.append("\n[历史验证有效的写法]")
            for pattern in state.successful_patterns[-self.max_patterns:]:
                parts.append(f"- {pattern}")

        if state.user_revision_preferences:
            parts.append("\n[用户修订偏好]")
            for preference in state.user_revision_preferences[-self.max_patterns:]:
                parts.append(f"- {preference}")

        return "\n".join(parts)

    def load(self, user_id: str) -> PatternFeedbackState:
        path = self._path(user_id)
        if not path.exists():
            return PatternFeedbackState(user_id=user_id)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            rules = {
                key: PatternRule(**value)
                for key, value in data.get("rules", {}).items()
            }
            return PatternFeedbackState(
                user_id=data.get("user_id", user_id),
                version=data.get("version", 1),
                update_count=data.get("update_count", 0),
                rules=rules,
                failed_patterns=data.get("failed_patterns", []),
                successful_patterns=data.get("successful_patterns", []),
                user_revision_preferences=data.get("user_revision_preferences", []),
                updated_at=data.get("updated_at", time.time()),
            )
        except Exception:  # noqa: BLE001
            return PatternFeedbackState(user_id=user_id)

    def save(self, state: PatternFeedbackState) -> None:
        path = self._path(state.user_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(asdict(state), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _merge_critique(self, state: PatternFeedbackState, critique: ReviewCritique) -> bool:
        if not critique.hard_gate_passed:
            return False

        weak = set(critique.weak_dimensions)
        weak.discard("hard_gate")
        is_success = critique.total_score >= self.success_threshold
        if not weak and not is_success:
            return False

        changed = False
        if weak:
            for dimension, rule_text in critique.pattern_feedback.dimension_rules.items():
                rule = state.rules.get(dimension)
                if rule is None:
                    rule = PatternRule(dimension=dimension, rule=rule_text)
                    state.rules[dimension] = rule
                rule.rule = rule_text
                rule.active = True
                rule.low_count += 1
                rule.success_count = 0
                rule.last_score = float(getattr(critique, dimension, 0.0))
                rule.updated_at = time.time()
                changed = True
            changed = self._append_unique(
                state.failed_patterns,
                critique.pattern_feedback.failed_patterns,
            ) or changed

        if is_success:
            for dimension in self.score_fields:
                if dimension in weak:
                    continue
                score = float(getattr(critique, dimension, 0.0))
                rule = state.rules.get(dimension)
                if rule is None or score < self.weak_threshold:
                    continue
                rule.success_count += 1
                rule.last_score = score
                rule.updated_at = time.time()
                if rule.success_count >= self.resolve_after_successes:
                    rule.active = False
                changed = True
            changed = self._append_unique(
                state.successful_patterns,
                critique.pattern_feedback.successful_patterns,
            ) or changed

        return changed

    def _compact(self, state: PatternFeedbackState) -> None:
        active = {
            key: rule
            for key, rule in state.rules.items()
            if rule.active or rule.success_count < self.resolve_after_successes
        }
        sorted_rules = sorted(
            active.items(),
            key=lambda item: (item[1].active, item[1].low_count, item[1].updated_at),
            reverse=True,
        )
        state.rules = dict(sorted_rules[: self.max_active_rules * 2])
        self._trim_patterns(state)

    def _trim_patterns(self, state: PatternFeedbackState) -> None:
        state.failed_patterns = state.failed_patterns[-self.max_patterns:]
        state.successful_patterns = state.successful_patterns[-self.max_patterns:]
        state.user_revision_preferences = state.user_revision_preferences[-self.max_patterns:]

    def _path(self, user_id: str) -> Path:
        return self.base_dir / user_id / "pattern_feedback.json"

    @staticmethod
    def _append_unique(target: list[str], values: list[str]) -> bool:
        changed = False
        for value in values:
            if value and value not in target:
                target.append(value)
                changed = True
        return changed

    @staticmethod
    def _extract_user_feedback_rules(feedback: str) -> list[str]:
        text = feedback.strip()
        if not text:
            return []

        rules: list[str] = []
        if "标题" in text and any(word in text for word in ["普通", "平", "抓人", "吸引", "爆", "钩子", "亮眼"]):
            rules.append("用户偏好标题更有钩子和明确收益，避免平淡标题。")
        if any(word in text for word in ["真实", "细节", "亲测", "经历", "案例", "场景"]):
            rules.append("用户偏好正文更真实具体，增加亲测细节、场景和判断依据。")
        if any(word in text for word in ["口语", "自然", "像人", "不像AI", "别太AI", "模板", "机器"]):
            rules.append("用户偏好口语化、自然表达，降低 AI 模板感。")
        if any(word in text for word in ["精简", "短一点", "太长", "啰嗦", "废话"]):
            rules.append("用户偏好表达更精简，减少重复铺垫。")
        if any(word in text for word in ["详细", "展开", "说清楚", "多写", "补充"]):
            rules.append("用户偏好内容更充分，增加解释、例子和执行细节。")
        if any(word in text for word in ["互动", "评论", "引导", "CTA", "结尾"]):
            rules.append("用户重视结尾互动引导，CTA 要自然且具体。")
        return rules
