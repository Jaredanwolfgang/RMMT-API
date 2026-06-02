import os
from datetime import timedelta


def _load_local_env():
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path, "r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip("\"'")
            if key and key not in os.environ:
                os.environ[key] = value


_load_local_env()


class GeneralConfig:
    # 从环境变量读取数据库配置
    DB_HOST = os.getenv('DB_HOST', '127.0.0.1')
    DB_PORT = os.getenv('DB_PORT', '3306')
    DB_NAME = os.getenv('DB_NAME', 'roommate')
    DB_USER = os.getenv('DB_USER', 'root')
    DB_PASSWORD = os.getenv('DB_PASSWORD', '')
    
    # 构建数据库URL
    DATABASE_URL = f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
    JWT_SECRET_KEY = os.getenv('JWT_SECRET', "R2xpmzp1F9QcpHn9")
    DATABASE_LOG = False
    ASYNC_JOB_SCAN_INTERVAL = 10  # in seconds
    AI_API_KEY = os.getenv('AI_API_KEY', '')
    AI_BASE_URL = os.getenv('AI_BASE_URL', 'https://api.deepseek.com')
    AI_MODEL = os.getenv('AI_MODEL', 'deepseek-chat')
    AI_TIMEOUT = int(os.getenv('AI_TIMEOUT', '20'))
    AI_SEARCH_CANDIDATE_LIMIT = int(os.getenv('AI_SEARCH_CANDIDATE_LIMIT', '30'))
    AI_EXPLANATION_CACHE_TTL = int(os.getenv('AI_EXPLANATION_CACHE_TTL', '86400'))
