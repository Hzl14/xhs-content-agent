from typing import Optional

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "XHS Content Agent (Legacy)"
    app_version: str = "0.1.0"
    debug: bool = True
    host: str = "127.0.0.1"
    port: int = 8000

    llm_provider: str = Field(
        default="aliyun",
        validation_alias=AliasChoices("LLM_PROVIDER", "MODEL_PROVIDER"),
    )
    llm_api_key: str = Field(
        default="",
        validation_alias=AliasChoices(
            "LLM_API_KEY",
            "ALIYUN_API_KEY",
            "DASHSCOPE_API_KEY",
            "OPENAI_API_KEY",
        ),
    )
    llm_model: str = Field(
        default="qwen-plus",
        validation_alias=AliasChoices("LLM_MODEL", "ALIYUN_MODEL", "OPENAI_MODEL"),
    )
    llm_temperature: float = Field(
        default=0.7,
        validation_alias=AliasChoices("LLM_TEMPERATURE", "OPENAI_TEMPERATURE"),
    )
    llm_base_url: Optional[str] = Field(
        default="https://dashscope.aliyuncs.com/compatible-mode/v1",
        validation_alias=AliasChoices("LLM_BASE_URL", "ALIYUN_BASE_URL", "OPENAI_BASE_URL"),
    )
    llm_max_retries: int = 3
    llm_request_timeout_seconds: float = 30.0
    llm_retry_deadline_seconds: float = 90.0

    image_model: str = "gpt-image-1"
    image_size: str = "1024x1024"
    image_output_dir: str = "data/output/images"

    feishu_app_id: str = ""
    feishu_app_secret: str = ""
    feishu_app_token: str = ""
    feishu_table_id: str = ""
    feishu_publish_table_id: str = ""

    xhs_mcp_url: str = "http://localhost:18060"
    xhs_mcp_endpoint: str = "http://localhost:18060/mcp"
    xhs_mcp_binary: str = ""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
