"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-09-21

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(length=8), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=True),
        sa.Column("generation", sa.String(length=16), nullable=True),
        sa.Column("storage_gb", sa.Integer(), nullable=True),
        sa.Column("color", sa.String(length=64), nullable=True),
        sa.Column("battery_health", sa.Integer(), nullable=True),
        sa.Column("grade", sa.String(length=1), nullable=True),
        sa.Column("kit_json", sa.Text(), nullable=True),
        sa.Column("price", sa.Integer(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sold_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("public_id", name="uq_items_public_id"),
    )
    op.create_index("ix_items_status_published_at", "items", ["status", "published_at"])

    op.create_table(
        "photos",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("item_id", sa.Integer(), sa.ForeignKey("items.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tg_file_id", sa.String(length=256), nullable=False),
        sa.Column("file_unique_id", sa.String(length=64), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cached_preview", sa.String(length=256), nullable=True),
        sa.Column("cached_full", sa.String(length=256), nullable=True),
    )
    op.create_index("ix_photos_item_id", "photos", ["item_id"])

    op.create_table(
        "channel_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("item_id", sa.Integer(), sa.ForeignKey("items.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chat_id", sa.Integer(), nullable=False),
        sa.Column("message_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
    )
    op.create_index("ix_channel_messages_item_id", "channel_messages", ["item_id"])

    op.create_table(
        "tags",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("label", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.UniqueConstraint("slug", name="uq_tags_slug"),
    )

    op.create_table(
        "item_tags",
        sa.Column("item_id", sa.Integer(), sa.ForeignKey("items.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tag_id", sa.Integer(), sa.ForeignKey("tags.id", ondelete="CASCADE"), nullable=False),
        sa.PrimaryKeyConstraint("item_id", "tag_id"),
    )
    op.create_index("ix_item_tags_tag_id", "item_tags", ["tag_id"])

    op.create_table(
        "events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("item_id", sa.Integer(), sa.ForeignKey("items.id", ondelete="CASCADE"), nullable=True),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_events_item_id", "events", ["item_id"])


def downgrade() -> None:
    op.drop_table("events")
    op.drop_table("item_tags")
    op.drop_table("tags")
    op.drop_table("channel_messages")
    op.drop_table("photos")
    op.drop_table("items")
