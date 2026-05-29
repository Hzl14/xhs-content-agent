from pathlib import Path

from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import PydanticOutputParser
from langchain_openai import ChatOpenAI

from legacy_app.core.config import settings
from legacy_app.models.schemas import (
    ContentGenerateRequest,
    ContentGenerateResponse,
)


def load_prompt_template() -> str:
    prompt_path = Path("app/prompts/content_generation_prompt.txt")
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


def generate_contents(request: ContentGenerateRequest) -> ContentGenerateResponse:
    template = load_prompt_template()

    parser = PydanticOutputParser(pydantic_object=ContentGenerateResponse)

    prompt = ChatPromptTemplate.from_template(template)
    llm = build_llm()

    chain = prompt | llm | parser

    result = chain.invoke(
        {
            "count": request.count,
            "topic": request.topic,
            "reason": request.reason,
            "audience": request.audience,
            "tone": request.tone,
            "format_instructions": parser.get_format_instructions(),
        }
    )

    return result
