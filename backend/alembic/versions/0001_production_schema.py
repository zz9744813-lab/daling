"""Establish the durable production, quality, learning, and safety schema.

Revision ID: 0001_production_schema
Revises: None
"""

from __future__ import annotations

import app.db.models  # noqa: F401
import sqlalchemy as sa
from alembic import op
from app.core.database import Base

revision = "0001_production_schema"
down_revision = None
branch_labels = None
depends_on = None


def _names(inspector: sa.Inspector, table: str) -> set[str]:
    names = {item.get("name") for item in inspector.get_indexes(table)}
    names.update(item.get("name") for item in inspector.get_unique_constraints(table))
    return {name for name in names if name}


def _ensure_unique_index(
    inspector: sa.Inspector,
    table: str,
    name: str,
    columns: list[str],
) -> None:
    if table in inspector.get_table_names() and name not in _names(inspector, table):
        op.create_index(name, table, columns, unique=True)


def upgrade() -> None:
    bind = op.get_bind()
    # This repository predates Alembic. create_all safely establishes every
    # missing canonical table for both fresh and existing installations.
    Base.metadata.create_all(bind=bind)
    inspector = sa.inspect(bind)

    if "continuous_runs" in inspector.get_table_names():
        columns = {item["name"] for item in inspector.get_columns("continuous_runs")}
        if "generation" not in columns:
            op.add_column(
                "continuous_runs",
                sa.Column("generation", sa.Integer(), nullable=False, server_default="0"),
            )
        if "fencing_token" not in columns:
            op.add_column(
                "continuous_runs",
                sa.Column("fencing_token", sa.Integer(), nullable=False, server_default="0"),
            )

    # Existing installations need the same invariants as fresh metadata.
    inspector = sa.inspect(bind)
    for table, name, columns in (
        ("chapters", "uq_chapter_project_no", ["project_id", "chapter_no"]),
        ("chapter_versions", "uq_chapter_version_no", ["chapter_id", "version_no"]),
        ("manuscript_blocks", "uq_chapter_block_no", ["chapter_id", "block_no"]),
        ("storyline_volumes", "uq_storyline_volume_project_no", ["project_id", "volume_no"]),
        ("storyline_beats", "uq_storyline_beat_chapter", ["project_id", "chapter_no"]),
        ("chapter_summaries", "uq_chapter_summary_project_no", ["project_id", "chapter_no"]),
        ("current_story_states", "uq_story_state_project_no", ["project_id", "chapter_no"]),
        ("book_memory", "uq_book_memory_key", ["project_id", "memory_type", "key"]),
    ):
        _ensure_unique_index(inspector, table, name, columns)


def downgrade() -> None:
    # The migration adopts pre-existing user databases. A destructive downgrade
    # would delete novels and production history, so rollback is intentionally
    # data-preserving and handled by forward migrations.
    pass
