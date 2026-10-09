"""seed 20 items ZOO-1..ZOO-20 (idempotent)

Revision ID: 0002
Revises: 0001
"""
import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

ANIMALS = ["Aardvark", "Bison", "Capybara", "Dingo", "Emu", "Fennec", "Gecko", "Heron", "Ibis", "Jackal",
           "Kiwi", "Lemur", "Marmot", "Narwhal", "Ocelot", "Pangolin", "Quokka", "Raven", "Serval", "Tapir"]


def upgrade() -> None:
    rows = [{"sku": f"ZOO-{n}", "name": f"{animal} plush", "price_cents": 900 + 150 * n, "stock": 5 * n}
            for n, animal in enumerate(ANIMALS, start=1)]
    op.get_bind().execute(sa.text(
        "INSERT INTO items (sku, name, price_cents, stock) VALUES (:sku, :name, :price_cents, :stock) "
        "ON CONFLICT (sku) DO NOTHING"), rows)


def downgrade() -> None:
    op.execute("DELETE FROM items WHERE sku LIKE 'ZOO-%'")
