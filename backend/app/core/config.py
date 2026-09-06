import os
import sys
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings


def _data_dir() -> Path:
    """账套数据根目录（SQLite 库 / 附件 / 备份 / 密钥）。

    - 环境变量 LEDGER_DATA_DIR 显式指定时优先（高级用户自定义数据盘）；
    - PyInstaller 打包环境（sys.frozen）默认 %LOCALAPPDATA%/有数LedgerAI/data
      —— 程序装在 Program Files 只读目录，数据必须落在用户可写处；
    - 开发环境默认 ./data（行为与历史一致）。
    """
    env = os.environ.get("LEDGER_DATA_DIR")
    if env:
        return Path(env)
    if getattr(sys, "frozen", False):
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "有数LedgerAI" / "data"
    return Path("./data")


def _secret_key() -> str:
    """登录令牌签名密钥。

    - 打包分发环境（sys.frozen）：首次运行随机生成并持久化到数据目录，
      避免所有安装共用默认密钥（否则 JWT 可跨安装伪造）；
    - 开发 / Docker：保持历史默认值（Docker 部署应通过 .env 显式提供
      SECRET_KEY；pydantic-settings 的 env 覆盖 default）。
    """
    if getattr(sys, "frozen", False):
        key_file = DATA_DIR / ".secret_key"
        if key_file.exists():
            value = key_file.read_text(encoding="utf-8").strip()
            if value:
                return value
        value = os.urandom(32).hex()
        key_file.write_text(value, encoding="utf-8")
        return value
    return "dev-secret-change-me"


DATA_DIR = _data_dir()
# 确保数据目录结构就绪（首次启动自动建库所需）
for _sub in ("", "attachments", "backups"):
    (DATA_DIR / _sub).mkdir(parents=True, exist_ok=True)


class Settings(BaseSettings):
    APP_NAME: str = "LedgerAI"
    SECRET_KEY: str = _secret_key()
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 720
    DATABASE_URL: str = f"sqlite:///{(DATA_DIR / 'ledger.db').as_posix()}"
    ATTACHMENTS_DIR: str = str(DATA_DIR / "attachments")
    BACKUP_DIR: str = str(DATA_DIR / "backups")
    LLM_API_KEY: str = ""
    LLM_BASE_URL: str = "https://api.deepseek.com"
    LLM_MODEL: str = "deepseek-chat"
    LLM_TIMEOUT_SECONDS: int = 180

    model_config = {
        # 打包版不读 .env（那是 Docker 部署用的相对路径，含 /data 容器路径，
        # 若被 cwd 下的 .env 误读会把库写到盘符根目录）。Docker/开发照旧读 .env。
        "env_file": None if getattr(sys, "frozen", False) else ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


def _frozen_settings_kwargs() -> dict:
    """打包环境的数据路径/密钥：一律锁定为本机数据目录，忽略 .env 与环境变量。"""
    return {
        "SECRET_KEY": _secret_key(),
        "DATABASE_URL": f"sqlite:///{(DATA_DIR / 'ledger.db').as_posix()}",
        "ATTACHMENTS_DIR": str(DATA_DIR / "attachments"),
        "BACKUP_DIR": str(DATA_DIR / "backups"),
    }


@lru_cache
def get_settings() -> Settings:
    if getattr(sys, "frozen", False):
        return Settings(**_frozen_settings_kwargs())
    return Settings()