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

    # 安全加固（云端生产必配）
    CORS_ORIGINS: str = ""          # 逗号分隔的允许跨域域名，空=仅同源(前后端同域部署够用)
    ENABLE_DOCS: bool = False       # 生产关闭 /docs /openapi.json，本地调试才开
    # 深层加固
    ENABLE_HSTS: bool = True        # 强制 HTTPS(HSTS)，走 Zeabur 内置 TLS，安全无副作用
    HIDE_SERVER_HEADER: bool = True # 隐藏 uvicorn Server 版本指纹，降低针对性扫描
    MAX_BODY_MB: int = 20           # 全局请求体上限(默认20MB)，防超大负载打爆内存

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
