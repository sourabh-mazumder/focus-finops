"""Database connection configuration.

Reads a DATABASE_URL (or discrete PG* variables) from the environment /
a local .env file. Values fall back to the defaults used when this project
was scaffolded (a local `focus_finops` database owned by role `focus_app`).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")


@dataclass(frozen=True)
class DbConfig:
    host: str = os.getenv("PGHOST", "localhost")
    port: str = os.getenv("PGPORT", "5432")
    dbname: str = os.getenv("PGDATABASE", "focus_finops")
    user: str = os.getenv("PGUSER", "focus_app")
    password: str = os.getenv("PGPASSWORD", "focus_app_pw")

    @property
    def conninfo_uri(self) -> str:
        """A plain libpq connection URI (e.g. for `psql` or other tools)."""
        explicit = os.getenv("DATABASE_URL")
        if explicit:
            return explicit
        return (
            f"postgresql://{self.user}:{self.password}"
            f"@{self.host}:{self.port}/{self.dbname}"
        )

    @property
    def sqlalchemy_url(self) -> str:
        """The connection URL for SQLAlchemy's psycopg2 dialect -- same
        target as `conninfo_uri`, with the driver named explicitly rather
        than relying on `postgresql://` resolving to whichever DBAPI
        happens to be installed.
        """
        uri = self.conninfo_uri
        if uri.startswith("postgresql+"):
            return uri
        if uri.startswith("postgresql://"):
            return "postgresql+psycopg2://" + uri[len("postgresql://"):]
        if uri.startswith("postgres://"):
            return "postgresql+psycopg2://" + uri[len("postgres://"):]
        return uri


def get_config() -> DbConfig:
    return DbConfig()
