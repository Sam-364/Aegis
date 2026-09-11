"""Programmatic Alembic helpers: run migrations and verify the schema is current."""

from __future__ import annotations

import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy.ext.asyncio import AsyncEngine

ROOT = Path(__file__).resolve().parents[4]


def alembic_config(sync_url: str) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.set_main_option(
        "sqlalchemy.url", sync_url.replace("postgresql://", "postgresql+psycopg://")
    )
    return cfg


def upgrade_head(sync_url: str) -> None:
    command.upgrade(alembic_config(sync_url), "head")


async def upgrade_head_async(sync_url: str) -> None:
    await asyncio.to_thread(upgrade_head, sync_url)


async def schema_is_current(
    engine: AsyncEngine, sync_url: str
) -> tuple[bool, str | None, str | None]:
    """Compare the database revision with the newest script revision."""
    script = ScriptDirectory.from_config(alembic_config(sync_url))
    head = script.get_current_head()

    def _current(sync_conn: object) -> str | None:
        return MigrationContext.configure(sync_conn).get_current_revision()  # type: ignore[arg-type]

    async with engine.connect() as conn:
        current = await conn.run_sync(_current)
    return current == head, current, head
