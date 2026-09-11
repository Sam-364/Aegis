"""audit_events: refuse TRUNCATE as well as UPDATE and DELETE

A row-level trigger never fires for TRUNCATE, so a single statement could have erased the whole
audit trail that the immutability guarantee rests on. Statement-level triggers do fire.

Revision ID: 0002_audit_truncate_guard
Revises: 0001_initial
Create Date: 2026-09-11
"""

from __future__ import annotations

from alembic import op

revision = "0002_audit_truncate_guard"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS audit_events_no_truncate ON audit_events")
    op.execute(
        """
        CREATE TRIGGER audit_events_no_truncate
        BEFORE TRUNCATE ON audit_events
        FOR EACH STATEMENT EXECUTE FUNCTION aegis_audit_immutable();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS audit_events_no_truncate ON audit_events")
