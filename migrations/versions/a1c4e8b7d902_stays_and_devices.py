"""إقامات النزلاء وأجهزتها — أساس إغلاق الدخول عند المغادرة.

Revision ID: a1c4e8b7d902
Revises: edae89c718f2
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a1c4e8b7d902"
down_revision = "edae89c718f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "stays",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(),
                  sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("room", sa.String(16), nullable=False, index=True),
        sa.Column("guest_name", sa.String(120), nullable=False, server_default=""),
        sa.Column("phone_last4", sa.String(4), nullable=False, server_default=""),
        sa.Column("stay_code", sa.String(16), nullable=False, server_default="", index=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="open"),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_by", sa.String(64), nullable=False, server_default=""),
        sa.Column("armed_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_stay_tenant_room_status", "stays", ["tenant_id", "room", "status"])

    op.create_table(
        "stay_devices",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("stay_id", sa.Integer(),
                  sa.ForeignKey("stays.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("token_hash", sa.String(64), nullable=False, index=True),
        sa.Column("label", sa.String(120), nullable=False, server_default=""),
        sa.Column("language", sa.String(8), nullable=False, server_default=""),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("stay_id", "token_hash", name="uq_device_stay_token"),
    )


def downgrade() -> None:
    op.drop_table("stay_devices")
    op.drop_index("ix_stay_tenant_room_status", table_name="stays")
    op.drop_table("stays")
