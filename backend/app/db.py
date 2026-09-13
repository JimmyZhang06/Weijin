import os
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .ai_config import settings


def uid():
    return str(uuid4())


def now():
    return datetime.now(timezone.utc)


if os.getenv("APP_ENV", "development") != "development":
    raise RuntimeError("Production authentication is not implemented; development mode only.")
if settings()["provider"] not in {"mock", "openai", "deepseek"}:
    raise RuntimeError("MODEL_PROVIDER must be mock, openai or deepseek.")

engine = create_engine(
    os.getenv("DATABASE_URL", "postgresql+psycopg://weijin:local-development-only@127.0.0.1:55438/weijin"),
    pool_pre_ping=True,
)
Session = sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass
