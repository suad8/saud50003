"""تذكير المغادرة: إعداد للفندق، وعلامة إرسال لكل إقامة.

Revision ID: e7b1f4a29c83
Revises: d4e9a3c72f15
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e7b1f4a29c83"
down_revision = "d4e9a3c72f15"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tenants", sa.Column("checkout_reminder_hours", sa.Integer(),
                                       nullable=False, server_default="3"))
    op.add_column("stays", sa.Column("reminder_sent_at", sa.DateTime(timezone=True),
                                     nullable=True))


def downgrade() -> None:
    op.drop_column("stays", "reminder_sent_at")
    op.drop_column("tenants", "checkout_reminder_hours")
