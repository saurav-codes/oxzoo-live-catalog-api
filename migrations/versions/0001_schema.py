"""items, hops, and the probe table

Revision ID: 0001
Revises:
"""
import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "items",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("sku", sa.String(16), nullable=False, unique=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("price_cents", sa.Integer, nullable=False),
        sa.Column("stock", sa.Integer, nullable=False),
    )
    op.create_table(
        "hops",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("trace", sa.String(36), nullable=False, index=True),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("step", sa.String(40), nullable=False),
        sa.Column("detail", sa.Text, nullable=False, server_default=""),
    )
    op.create_table(
        "zoo_probe",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("token", sa.String(32), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("zoo_probe")
    op.drop_table("hops")
    op.drop_table("items")
