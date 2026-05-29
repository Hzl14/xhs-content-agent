"""
image_service.py
璋冪敤 gpt-image-1 鏍规嵁璇濋鏁版嵁鐢熸垚鍥剧墖锛屼繚瀛樺埌鏈湴锛岃繑鍥炴枃浠惰矾寰勫垪琛ㄣ€?"""

import base64
import asyncio
import time
import os
from pathlib import Path

from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI, RateLimitError

from legacy_app.core.config import settings
from legacy_app.models.schemas import ContentItem, TopicItem


def _build_image_prompt(topic: TopicItem, content: ContentItem) -> str:
    """
    灏嗚瘽棰樹俊鎭拰鍐呭涓殑 image_suggestion 鏁村悎鎴愬浘鐗囩敓鎴?prompt銆?    鐢ㄤ腑鏂囨弿杩拌浆鑻辨枃椋庢牸鎸囦护锛屽洜涓?gpt-image-1 瀵硅嫳鏂?prompt 鏁堟灉鏇村ソ銆?    """
    # 鎶婁腑鏂?image_suggestion 鐩存帴鎷艰繘 prompt锛屾ā鍨嬭兘鐞嗚В涓枃
    return (
        f"Create a high-quality, vibrant social media image for Xiaohongshu (Little Red Book). "
        f"Topic: {topic.title}. "
        f"Visual concept: {content.image_suggestion}. "
        f"Style: warm, lifestyle, authentic, bright colors, suitable for a Chinese female audience aged 18-28. "
        f"No text overlay. Square composition 1:1."
    )


async def generate_images(
    topic: TopicItem,
    content: ContentItem,
    image_count: int = 1,
) -> list[str]:
    """
    璋冪敤 gpt-image-1 鐢熸垚鍥剧墖锛屼繚瀛樺埌鏈湴锛岃繑鍥炵粷瀵硅矾寰勫垪琛ㄣ€?
    Args:
        topic: 璇濋淇℃伅锛屾彁渚涗富棰樿儗鏅?        content: 鐢熸垚鐨勫唴瀹癸紝鍏朵腑 image_suggestion 浣滀负瑙嗚鍙傝€?        image_count: 鐢熸垚鍥剧墖鏁伴噺锛?-4 寮?
    Returns:
        鏈湴鍥剧墖鏂囦欢鐨勭粷瀵硅矾寰勫垪琛?    """
    client = AsyncOpenAI(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url or None,
    )

    prompt = _build_image_prompt(topic, content)

    # 纭繚杈撳嚭鐩綍瀛樺湪
    output_dir = Path(settings.image_output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    response = await _generate_image_with_retry(
        client=client,
        prompt=prompt,
        image_count=image_count,
    )

    saved_paths: list[str] = []
    ts = int(time.time())

    for idx, image_data in enumerate(response.data):
        # gpt-image-1 榛樿杩斿洖 b64_json
        if image_data.b64_json:
            img_bytes = base64.b64decode(image_data.b64_json)
            file_path = output_dir / f"{ts}_{idx}.png"
            file_path.write_bytes(img_bytes)
            saved_paths.append(str(file_path.resolve()))
        elif image_data.url:
            # 濡傛灉杩斿洖鐨勬槸 url锛堝鐢級锛岀敤 httpx 涓嬭浇
            import httpx
            async with httpx.AsyncClient() as http:
                resp = await http.get(image_data.url, timeout=60)
                resp.raise_for_status()
            file_path = output_dir / f"{ts}_{idx}.png"
            file_path.write_bytes(resp.content)
            saved_paths.append(str(file_path.resolve()))
        else:
            raise ValueError(f"鍥剧墖鏁版嵁涓虹┖锛宨ndex={idx}")

    return saved_paths


async def _generate_image_with_retry(client: AsyncOpenAI, prompt: str, image_count: int):
    retries = max(settings.llm_max_retries, 1)
    request_timeout = max(settings.llm_request_timeout_seconds, 0.1)
    retry_deadline = max(settings.llm_retry_deadline_seconds, request_timeout)
    deadline_at = time.monotonic() + retry_deadline
    last_exc: Exception | None = None

    for attempt in range(retries):
        try:
            remaining = deadline_at - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"Image generation retry deadline exceeded after {retry_deadline:.1f}s.")
            timeout = min(request_timeout, remaining)
            return await asyncio.wait_for(
                client.images.generate(
                    model=settings.image_model,
                    prompt=prompt,
                    size=settings.image_size,  # type: ignore[arg-type]
                    n=image_count,
                ),
                timeout=timeout,
            )
        except TimeoutError as exc:
            last_exc = exc
        except asyncio.TimeoutError as exc:
            last_exc = TimeoutError(f"Image generation request timed out after {request_timeout:.1f}s.")
        except (APIConnectionError, APITimeoutError, RateLimitError) as exc:
            last_exc = exc
        except APIStatusError as exc:
            last_exc = exc
            if exc.status_code < 500 and exc.status_code not in {408, 409, 429}:
                raise

        if attempt >= retries - 1:
            break
        remaining = deadline_at - time.monotonic()
        if remaining <= 0:
            break
        await asyncio.sleep(min(0.5 * (2**attempt), 4.0, remaining))

    raise last_exc or RuntimeError("Image generation request failed.")
