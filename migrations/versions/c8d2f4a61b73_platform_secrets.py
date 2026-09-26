"""أسرار المنصة المولَّدة المحفوظة.

Revision ID: c8d2f4a61b73
Revises: b3f7a2c15d80
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c8d2f4a61b73"
down_revision = "b3f7a2c15d80"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "platform_secrets",
        sa.Column("name", sa.String(64), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("platform_secrets")
