"""Maintenance commands: `uv run python -m app.cli retag`."""

from __future__ import annotations

import asyncio
import sys

from sqlalchemy import select

from app.db.models import Item, ItemStatus
from app.db.session import dispose_engine, get_session_factory
from app.services import tags


async def retag() -> int:
    """Recompute tags of every active lot (after a tag-scheme change)."""
    async with get_session_factory()() as session:
        items = (await session.scalars(select(Item).where(Item.status == ItemStatus.active.value))).all()
        for item in items:
            await tags.sync_item_tags(session, item)
        await session.commit()
    await dispose_engine()
    return len(items)


def main(argv: list[str]) -> None:
    if argv[1:] == ["retag"]:
        print(f"retagged {asyncio.run(retag())} active items")
        return
    sys.exit("usage: python -m app.cli retag")


if __name__ == "__main__":
    main(sys.argv)
