"""Settings loaded from .env (see .env.example)."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


class Settings:
    @property
    def database_url(self) -> str:
        url = os.environ.get("DATABASE_URL")
        if not url:
            raise RuntimeError("DATABASE_URL is not set. Copy .env.example to .env and edit it.")
        return url

    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8015"))
    migrations_dir = ROOT / "migrations"


settings = Settings()
