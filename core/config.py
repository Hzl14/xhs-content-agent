from pydantic import AliasChoices, Field
from pydantic import field_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "XHS Agent Refactored"
    app_version: str = "0.2.0"
    debug: bool = True
    host: str = "127.0.0.1"
    port: int = 8000

    llm_provider: str = Field(
        default="aliyun",
        validation_alias=AliasChoices("LLM_PROVIDER", "MODEL_PROVIDER", "DEEPSEEK_PROVIDER"),
    )
    llm_api_key: str = Field(
        default="",
        validation_alias=AliasChoices(
            "LLM_API_KEY",
            "ALIYUN_API_KEY",
            "DASHSCOPE_API_KEY",
            "OPENAI_API_KEY",
            "DEEPSEEK_API_KEY",
        ),
    )
    llm_model: str = Field(
        default="qwen-plus",
        validation_alias=AliasChoices("LLM_MODEL", "ALIYUN_MODEL", "OPENAI_MODEL", "DEEPSEEK_MODEL"),
    )
    llm_vision_model: str = Field(
        default="qwen-vl-plus",
        validation_alias=AliasChoices("LLM_VISION_MODEL", "ALIYUN_VISION_MODEL", "DEEPSEEK_VISION_MODEL"),
    )
    llm_base_url: str | None = Field(
        default="https://dashscope.aliyuncs.com/compatible-mode/v1",
        validation_alias=AliasChoices(
            "LLM_BASE_URL",
            "ALIYUN_BASE_URL",
            "OPENAI_BASE_URL",
            "DEEPSEEK_BASE_URL",
        ),
    )
    llm_temperature: float = Field(
        default=0.7,
        validation_alias=AliasChoices("LLM_TEMPERATURE", "OPENAI_TEMPERATURE"),
    )
    llm_max_retries: int = 2
    llm_request_timeout_seconds: float = 30.0
    llm_retry_deadline_seconds: float = 90.0

    review_threshold: float = 65.0
    max_reflections: int = 0

    image_output_dir: str = "data/output/images"
    sample_note_path: str = "data/raw/sample_notes.json"

    memory_base_dir: str = "data/memory"
    memory_short_term_turns: int = 7
    memory_summary_trigger_turns: int = 14
    memory_hard_token_limit: int = 18000
    memory_strong_threshold: float = 0.75
    memory_mid_threshold: float = 0.50
    pattern_feedback_max_active_rules: int = 3
    pattern_feedback_max_patterns: int = 5
    pattern_feedback_resolve_after_successes: int = 3
    pattern_feedback_compact_every_updates: int = 10
    pattern_feedback_success_threshold: float = 85.0

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return init_settings, dotenv_settings, env_settings, file_secret_settings

    @field_validator("debug", mode="before")
    @classmethod
    def parse_debug_flag(cls, value):
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"release", "prod", "production"}:
                return False
            if normalized in {"debug", "dev", "development"}:
                return True
        return value


settings = Settings()
