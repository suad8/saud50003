"""الجوال مفتاح الدخول — صورة قانونية مفهرسة، وردم بيانات ما سبق.

Revision ID: d4e9a3c72f15
Revises: c8d2f4a61b73
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d4e9a3c72f15"
down_revision = "c8d2f4a61b73"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("stays", sa.Column("phone_norm", sa.String(length=20),
                                     nullable=False, server_default=""))
    op.create_index("ix_stays_phone_norm", "stays", ["phone_norm"])
    op.create_index("ix_stay_tenant_phone_status", "stays",
                    ["tenant_id", "phone_norm", "status"])


def downgrade() -> None:
    op.drop_index("ix_stay_tenant_phone_status", table_name="stays")
    op.drop_index("ix_stays_phone_norm", table_name="stays")
    op.drop_column("stays", "phone_norm")
