"""مفاتيح الواجهة البرمجية — جدول كان ينقص سلسلة الترحيلات.

الجدول موجود في النماذج منذ بناء تكامل أنظمة إدارة الفنادق، لكنه لم يدخل أي
ترحيل. لم يظهر الخلل لأن الإقلاع يستدعي `create_all` فينشئه على أي حال —
وهذا بالضبط ما يخفي الانحراف: سجل الترحيلات يصف قاعدة بيانات غير التي تعمل.
من نشر بالترحيلات وحدها (أو راجع السجل ليفهم البنية) كان سيجد نفسه أمام
جدول لا وجود له في التاريخ.

Revision ID: b3f7a2c15d80
Revises: a1c4e8b7d902
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b3f7a2c15d80"
down_revision = "a1c4e8b7d902"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if sa.inspect(bind).has_table("api_keys"):
        # أنشأه `create_all` في بيئة قائمة — نسجّل الترحيل ولا نكرّر الإنشاء.
        return

    op.create_table(
        "api_keys",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(),
                  sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("name", sa.String(120), nullable=False, server_default=""),
        sa.Column("prefix", sa.String(16), nullable=False, unique=True, index=True),
        sa.Column("key_hash", sa.String(255), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.String(120), nullable=False, server_default=""),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("api_keys")
