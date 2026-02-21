from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # ClickUp
    clickup_api_token: str
    clickup_webhook_secret: str = ""

    # GitHub
    github_token: str

    # Optional
    commit_message_template: str = "ClickUp task {task_id}: {task_name}"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
