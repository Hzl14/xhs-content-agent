from __future__ import annotations

try:
    import tiktoken
    _enc = tiktoken.get_encoding("cl100k_base")

    def estimate_tokens(text: str) -> int:
        if not text:
            return 0
        return len(_enc.encode(text))

except Exception:
    # tiktoken 未安装时的降级方案：中英混合按字符数估算
    def estimate_tokens(text: str) -> int:  # type: ignore[misc]
        if not text:
            return 0
        ascii_chars = sum(1 for c in text if ord(c) < 128)
        cjk_chars = len(text) - ascii_chars
        # 英文平均 4 字符 / token，中文约 1.5 字符 / token
        return max(1, int(ascii_chars / 4 + cjk_chars / 1.5))
