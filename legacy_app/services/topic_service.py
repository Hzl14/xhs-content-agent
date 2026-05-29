import json
from pathlib import Path

from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_openai import ChatOpenAI

from legacy_app.core.config import settings
from legacy_app.models.schemas import (
    TopicGenerateRequest,
    TopicGenerateResponse,
    TopicItem,
)


def load_prompt_template() -> str:
    prompt_path = Path("app/prompts/topic_generation_prompt.txt")
    with open(prompt_path, "r", encoding="utf-8") as f:
        return f.read()


def build_llm() -> ChatOpenAI:
    llm_kwargs = {
        "model": settings.llm_model,
        "temperature": settings.llm_temperature,
        "api_key": settings.llm_api_key,
    }

    if settings.llm_base_url:
        llm_kwargs["base_url"] = settings.llm_base_url

    return ChatOpenAI(**llm_kwargs)


def generate_topics(request: TopicGenerateRequest) -> TopicGenerateResponse:
    template = load_prompt_template()

    prompt = ChatPromptTemplate.from_template(template)
    llm = build_llm()
    parser = StrOutputParser()

    chain = prompt | llm | parser

    result_text = chain.invoke(
        {
            "count": request.count,
            "audience": request.audience,
            "summary": request.summary,
            "top_keywords": ", ".join(request.top_keywords),
            "top_tags": ", ".join(request.top_tags),
            "title_patterns": ", ".join(request.title_patterns),
            "insight_points": "\n".join(f"- {point}" for point in request.insight_points),
        }
    )

    try:
        parsed = json.loads(result_text)
        topics_data = parsed.get("topics", [])
        topics = [TopicItem(**item) for item in topics_data]
        return TopicGenerateResponse(topics=topics)
    except Exception as e:
        raise ValueError(f"Failed to parse LLM output as JSON: {e}\nRaw output:\n{result_text}")
