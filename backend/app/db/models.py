from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class ItemStatus(str, enum.Enum):
    draft = "draft"
    active = "active"
    sold = "sold"


class Grade(str, enum.Enum):
    a = "A"
    b = "B"
    c = "C"


class MessageKind(str, enum.Enum):
    media = "media"
    cta = "cta"  # legacy: separate button message from before posts became a single photo


class MediaKind(str, enum.Enum):
    photo = "photo"
    video = "video"


class TagKind(str, enum.Enum):
    device = "device"
    brand = "brand"
    model = "model"
    storage = "storage"
    color = "color"
    grade = "grade"
    battery_band = "battery_band"
    price_band = "price_band"
    kit = "kit"


class EventType(str, enum.Enum):
    created = "created"
    published = "published"
    sold = "sold"
    delete_failed = "delete_failed"
    lead = "lead"
    notified = "notified"  # new-listing broadcast to subscribers finished


class LeadSource(str, enum.Enum):
    app = "app"
    site = "site"


class Item(Base):
    __tablename__ = "items"
    __table_args__ = (Index("ix_items_status_published_at", "status", "published_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(8), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=ItemStatus.draft.value)
    category: Mapped[str | None] = mapped_column(String(16))  # catalog.CATEGORIES code or "other"
    device_type: Mapped[str | None] = mapped_column(String(16))  # catalog.DEVICE_TYPES code
    brand: Mapped[str | None] = mapped_column(String(64))
    model: Mapped[str | None] = mapped_column(String(64))
    generation: Mapped[str | None] = mapped_column(String(16))
    year: Mapped[int | None] = mapped_column(Integer)
    storage_gb: Mapped[int | None] = mapped_column(Integer)
    variant: Mapped[str | None] = mapped_column(String(64))
    color: Mapped[str | None] = mapped_column(String(64))
    battery_health: Mapped[int | None] = mapped_column(Integer)
    grade: Mapped[str | None] = mapped_column(String(1))
    kit_json: Mapped[str | None] = mapped_column(Text)
    price: Mapped[int | None] = mapped_column(Integer)
    note: Mapped[str | None] = mapped_column(Text)
    # JSON list of wizard step names the admin explicitly skipped; NULL = none.
    skipped_json: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sold_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    photos: Mapped[list[Photo]] = relationship(
        back_populates="item", cascade="all, delete-orphan", order_by="Photo.position"
    )
    channel_messages: Mapped[list[ChannelMessage]] = relationship(
        back_populates="item", cascade="all, delete-orphan"
    )
    tags: Mapped[list[Tag]] = relationship(secondary="item_tags", back_populates="items")
    events: Mapped[list[Event]] = relationship(back_populates="item", cascade="all, delete-orphan")
    leads: Mapped[list[Lead]] = relationship(back_populates="item", cascade="all, delete-orphan")


class Photo(Base):
    """One media file of a lot (the table predates videos, hence the name)."""

    __tablename__ = "photos"
    __table_args__ = (Index("ix_photos_item_id", "item_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(
        String(8), nullable=False, default=MediaKind.photo.value, server_default=MediaKind.photo.value
    )
    tg_file_id: Mapped[str] = mapped_column(String(256), nullable=False)
    file_unique_id: Mapped[str] = mapped_column(String(64), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duration: Mapped[int | None] = mapped_column(Integer)  # seconds, videos only
    file_size: Mapped[int | None] = mapped_column(Integer)  # bytes
    cached_preview: Mapped[str | None] = mapped_column(String(256))
    cached_full: Mapped[str | None] = mapped_column(String(256))

    item: Mapped[Item] = relationship(back_populates="photos")


class ChannelMessage(Base):
    __tablename__ = "channel_messages"
    __table_args__ = (Index("ix_channel_messages_item_id", "item_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"), nullable=False)
    chat_id: Mapped[int] = mapped_column(Integer, nullable=False)
    message_id: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)

    item: Mapped[Item] = relationship(back_populates="channel_messages")


class Tag(Base):
    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    label: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)

    items: Mapped[list[Item]] = relationship(secondary="item_tags", back_populates="tags")


class ItemTag(Base):
    __tablename__ = "item_tags"
    __table_args__ = (Index("ix_item_tags_tag_id", "tag_id"),)

    item_id: Mapped[int] = mapped_column(
        ForeignKey("items.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[int] = mapped_column(ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True)


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (Index("ix_events_item_id", "item_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    item_id: Mapped[int | None] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"))
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    payload_json: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    item: Mapped[Item | None] = relationship(back_populates="events")


class Lead(Base):
    __tablename__ = "leads"
    __table_args__ = (Index("ix_leads_item_id", "item_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"), nullable=False)
    source: Mapped[str] = mapped_column(String(8), nullable=False)  # LeadSource
    buyer_name: Mapped[str] = mapped_column(String(128), nullable=False)
    buyer_contact: Mapped[str] = mapped_column(String(128), nullable=False)
    buyer_tg_id: Mapped[int | None] = mapped_column(BigInteger)
    buyer_username: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    item: Mapped[Item] = relationship(back_populates="leads")


class Subscriber(Base):
    """A customer who talked to the bot. `subscribed` = wants new-listing
    notifications; `blocked_at` is set when Telegram says the user blocked the
    bot (or deleted the account), cleared when they write to the bot again."""

    __tablename__ = "subscribers"

    tg_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    username: Mapped[str | None] = mapped_column(String(64))
    first_name: Mapped[str | None] = mapped_column(String(128))
    subscribed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    blocked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
