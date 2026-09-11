"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-09-10
"""

from __future__ import annotations

from alembic import op

from aegis.infrastructure.postgres.models import Base

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)
    # Audit events are immutable: refuse UPDATE and DELETE at the database level.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION aegis_audit_immutable() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'audit_events is append-only';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_events_immutable
        BEFORE UPDATE OR DELETE ON audit_events
        FOR EACH ROW EXECUTE FUNCTION aegis_audit_immutable();
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_incident_memories_embedding ON incident_memories "
        "USING hnsw (embedding vector_cosine_ops)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_incident_memories_text ON incident_memories "
        "USING gin (to_tsvector('english', embedding_text))"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS audit_events_immutable ON audit_events")
    op.execute("DROP FUNCTION IF EXISTS aegis_audit_immutable")
    Base.metadata.drop_all(bind=op.get_bind())
