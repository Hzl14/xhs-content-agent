from __future__ import annotations

import contextvars
import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


TRACE_DIR = Path("data/traces")
MAX_SUMMARY_CHARS = 1200

_current_trace_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_trace_id",
    default=None,
)
_current_parent_span_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_parent_span_id",
    default=None,
)


@dataclass
class SpanRecord:
    trace_id: str
    span_id: str
    parent_span_id: str | None
    type: str
    name: str
    status: str = "success"
    input_summary: Any | None = None
    output_summary: Any | None = None
    latency_ms: float | None = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)


class SpanHandle:
    def __init__(
        self,
        *,
        span_type: str,
        name: str,
        input_summary: Any | None = None,
        metadata: dict[str, Any] | None = None,
        as_parent: bool = False,
    ) -> None:
        self.trace_id = _current_trace_id.get()
        self.span_id = f"{span_type}_{uuid.uuid4().hex[:10]}"
        self.parent_span_id = _current_parent_span_id.get()
        self.span_type = span_type
        self.name = name
        self.input_summary = _compact(input_summary)
        self.metadata = metadata or {}
        self.started_at = time.perf_counter()
        self.created_at = time.time()
        self._parent_token = (
            _current_parent_span_id.set(self.span_id) if as_parent and self.trace_id else None
        )
        self._ended = False

    def end(
        self,
        *,
        status: str = "success",
        output_summary: Any | None = None,
        error: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if self._ended:
            return
        self._ended = True
        if self._parent_token is not None:
            _current_parent_span_id.reset(self._parent_token)
        if not self.trace_id:
            return

        merged_metadata = dict(self.metadata)
        if metadata:
            merged_metadata.update(metadata)
        record = SpanRecord(
            trace_id=self.trace_id,
            span_id=self.span_id,
            parent_span_id=self.parent_span_id,
            type=self.span_type,
            name=self.name,
            status=status,
            input_summary=self.input_summary,
            output_summary=_compact(output_summary),
            latency_ms=round((time.perf_counter() - self.started_at) * 1000, 2),
            error=_truncate(str(error)) if error else None,
            metadata=_compact(merged_metadata) or {},
            created_at=self.created_at,
        )
        append_span(record)


def set_trace_id(trace_id: str):
    return _current_trace_id.set(trace_id)


def reset_trace_id(token) -> None:
    _current_trace_id.reset(token)


def begin_span(
    span_type: str,
    name: str,
    *,
    input_summary: Any | None = None,
    metadata: dict[str, Any] | None = None,
    as_parent: bool = False,
) -> SpanHandle:
    return SpanHandle(
        span_type=span_type,
        name=name,
        input_summary=input_summary,
        metadata=metadata,
        as_parent=as_parent,
    )


def append_span(record: SpanRecord) -> None:
    TRACE_DIR.mkdir(parents=True, exist_ok=True)
    path = TRACE_DIR / f"{_safe_name(record.trace_id)}.jsonl"
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")


def load_spans(trace_id: str) -> list[dict]:
    path = TRACE_DIR / f"{_safe_name(trace_id)}.jsonl"
    if not path.exists():
        return []
    spans: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            spans.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return spans


def _safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in value)


def _compact(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        return _truncate(value)
    if isinstance(value, (int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _compact(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_compact(v) for v in list(value)[:20]]
    return _truncate(str(value))


def _truncate(text: str) -> str:
    text = text.strip()
    if len(text) <= MAX_SUMMARY_CHARS:
        return text
    return text[:MAX_SUMMARY_CHARS] + "...[truncated]"
