from pydantic_settings import BaseSettings
from functools import lru_cache
import os


class Settings(BaseSettings):
    DB_DRIVER: str = "sqlite"
    DB_URL: str = "sqlite:///./data/erp.db"
    JWT_SECRET: str = "dev-secret-change-me"
    JWT_ALG: str = "HS256"
    JWT_TTL_MINUTES: int = 1440
    ADMIN_DEFAULT_PASSWORD: str = "admin123"

    FEISHU_WEBHOOK: str = ""
    WECOM_WEBHOOK: str = ""
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASS: str = ""
    SMTP_FROM: str = ""

    # DeepSeek LLM (AI分析模块专用)
    DEEPSEEK_API_KEY: str = ""
    DEEPSEEK_BASE_URL: str = "https://api.deepseek.com"
    DEEPSEEK_MODEL_FAST: str = "deepseek-v4-flash"  # 意图解析
    DEEPSEEK_MODEL_PRO: str = "deepseek-v4-pro"     # 报告生成

    class Config:
        env_file = ".env"
        case_sensitive = True


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
