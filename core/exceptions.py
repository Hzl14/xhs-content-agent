class XHSAgentError(Exception):
    """Base exception for refactored XHS agent."""


class PipelineExecutionError(XHSAgentError):
    """Raised when pipeline execution fails."""


class LLMOutputValidationError(XHSAgentError):
    """Raised when LLM output cannot be validated."""

