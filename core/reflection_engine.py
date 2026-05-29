class ReflectionEngine:
    def __init__(self, threshold: float = 65.0, max_reflections: int = 0):
        self.threshold = threshold
        self.max_reflections = max_reflections

    def should_retry(self, score: float, attempt: int) -> bool:
        return score < self.threshold and attempt < self.max_reflections
