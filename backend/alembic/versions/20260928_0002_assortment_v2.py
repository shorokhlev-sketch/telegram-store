"""assortment v2: categories, manual-entry fields, media kind, leads

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-28

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("items") as batch:
        batch.add_column(sa.Column("category", sa.String(length=16), nullable=True))
        batch.add_column(sa.Column("device_type", sa.String(length=16), nullable=True))
        batch.add_column(sa.Column("brand", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("year", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("variant", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("skipped_json", sa.Text(), nullable=True))

    with op.batch_alter_table("photos") as batch:
        batch.add_column(sa.Column("kind", sa.String(length=8), nullable=False, server_default="photo"))
        batch.add_column(sa.Column("duration", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("file_size", sa.Integer(), nullable=True))

    op.create_table(
        "leads",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("item_id", sa.Integer(), sa.ForeignKey("items.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source", sa.String(length=8), nullable=False),
        sa.Column("buyer_name", sa.String(length=128), nullable=False),
        sa.Column("buyer_contact", sa.String(length=128), nullable=False),
        sa.Column("buyer_tg_id", sa.BigInteger(), nullable=True),
        sa.Column("buyer_username", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("notified_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_leads_item_id", "leads", ["item_id"])

    # Every lot before v2 is an iPhone picked from the catalog.
    op.execute(
        "UPDATE items SET category = 'iphone', device_type = 'phone', brand = 'Apple' "
        "WHERE model IS NOT NULL"
    )


def downgrade() -> None:
    # Dropping columns recreates the tables (SQLite batch mode). The migration
    # connection does not enable PRAGMA foreign_keys, so dropping the old
    # `items` copy does not cascade into photos/channel_messages/etc.
    op.drop_index("ix_leads_item_id", table_name="leads")
    op.drop_table("leads")

    # v1 treats every media row as a photo and would send_photo() a video file_id.
    op.execute("DELETE FROM photos WHERE kind <> 'photo'")
    with op.batch_alter_table("photos") as batch:
        batch.drop_column("file_size")
        batch.drop_column("duration")
        batch.drop_column("kind")

    with op.batch_alter_table("items") as batch:
        batch.drop_column("skipped_json")
        batch.drop_column("variant")
        batch.drop_column("year")
        batch.drop_column("brand")
        batch.drop_column("device_type")
        batch.drop_column("category")
