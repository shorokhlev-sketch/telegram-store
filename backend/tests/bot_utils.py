"""Fake aiogram Bot that records API calls instead of hitting Telegram."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.methods import (
    AnswerCallbackQuery,
    DeleteMessage,
    EditMessageCaption,
    EditMessageReplyMarkup,
    EditMessageText,
    SendDocument,
    SendMediaGroup,
    SendMessage,
    SendPhoto,
    SetWebhook,
    TelegramMethod,
)
from aiogram.types import CallbackQuery, Chat, Message, PhotoSize, Update, User

ADMIN_ID = 111
STRANGER_ID = 999
CHAT_ID = 111


# Tags Telegram accepts with parse_mode=HTML. Anything else ("<public_id>")
# makes the real API reject the message: Bad Request: can't parse entities.
_TG_HTML_TAGS = {
    "b", "strong", "i", "em", "u", "ins", "s", "strike", "del", "a", "code", "pre",
    "tg-spoiler", "span", "blockquote", "tg-emoji",
}
_TAG_RE = re.compile(r"<\s*/?\s*([A-Za-z][\w-]*)")


def assert_telegram_html(text: str | None) -> None:
    """Fail like Telegram does on unsupported HTML tags (the bot's default parse mode is HTML)."""
    if not text:
        return
    for tag in _TAG_RE.findall(text):
        if tag.lower() not in _TG_HTML_TAGS:
            raise AssertionError(f"Telegram would reject this HTML: unsupported tag <{tag}> in {text!r}")


class FakeBot(Bot):
    def __init__(self) -> None:
        super().__init__(token="123456:TEST")
        self.calls: list[TelegramMethod] = []
        self._next_message_id = 1000
        # message_ids for which DeleteMessage/EditMessage* should raise, to
        # exercise sales.py's fallback path in tests.
        self.fail_delete_ids: set[int] = set()
        self.fail_edit_ids: set[int] = set()
        # subscriber broadcast simulation: chats that blocked the bot, and
        # chat -> retry_after seconds raised once on the next SendPhoto there.
        self.forbidden_chats: set[int] = set()
        self.retry_after_once: dict[int, int] = {}
        self.fail_photo_chats: set[int] = set()

    def _make_message(self, chat_id: int, text: str | None = None) -> Message:
        self._next_message_id += 1
        return Message(
            message_id=self._next_message_id,
            date=datetime.now(timezone.utc),
            chat=Chat(id=chat_id, type="private"),
            text=text,
        )

    async def __call__(self, method: TelegramMethod, request_timeout: int | None = None) -> Any:
        self.calls.append(method)
        # production bot uses DefaultBotProperties(parse_mode="HTML"): validate unless explicitly plain
        if getattr(method, "parse_mode", "HTML") not in (None, ""):
            pm = getattr(method, "parse_mode", "HTML")
            if not isinstance(pm, str) or pm.upper() == "HTML":
                assert_telegram_html(getattr(method, "text", None))
                assert_telegram_html(getattr(method, "caption", None))
        if isinstance(method, DeleteMessage) and method.message_id in self.fail_delete_ids:
            raise TelegramBadRequest(method=method, message="Bad Request: message can't be deleted")
        if isinstance(method, (EditMessageCaption, EditMessageText)) and method.message_id in self.fail_edit_ids:
            raise TelegramBadRequest(method=method, message="Bad Request: message is not modified")
        if isinstance(method, SendMessage):
            return self._make_message(method.chat_id, method.text)
        if isinstance(method, SendPhoto):
            if method.chat_id in self.forbidden_chats:
                raise TelegramForbiddenError(method=method, message="Forbidden: bot was blocked by the user")
            if method.chat_id in self.retry_after_once:
                seconds = self.retry_after_once.pop(method.chat_id)
                raise TelegramRetryAfter(method=method, message="Too Many Requests", retry_after=seconds)
            if method.chat_id in self.fail_photo_chats:
                raise TelegramBadRequest(method=method, message="Bad Request: chat not found")
            return self._make_message(method.chat_id)
        if isinstance(method, SendMediaGroup):
            return [self._make_message(method.chat_id) for _ in method.media]
        if isinstance(method, SendDocument):
            return self._make_message(method.chat_id)
        if isinstance(method, (EditMessageReplyMarkup, EditMessageCaption, EditMessageText)):
            return self._make_message(method.chat_id or CHAT_ID)
        if isinstance(method, (AnswerCallbackQuery, DeleteMessage, SetWebhook)):
            return True
        return True

    # --- assertions helpers ---
    def sent_texts(self) -> list[str]:
        return [m.text for m in self.calls if isinstance(m, SendMessage)]

    def last_text(self) -> str:
        texts = self.sent_texts()
        return texts[-1] if texts else ""

    def last_send(self) -> SendMessage | None:
        sends = [m for m in self.calls if isinstance(m, SendMessage)]
        return sends[-1] if sends else None

    def media_groups(self) -> list[SendMediaGroup]:
        return [m for m in self.calls if isinstance(m, SendMediaGroup)]

    def photos(self) -> list[SendPhoto]:
        return [m for m in self.calls if isinstance(m, SendPhoto)]

    def documents(self) -> list[SendDocument]:
        return [m for m in self.calls if isinstance(m, SendDocument)]

    def callback_answers(self) -> list[AnswerCallbackQuery]:
        return [m for m in self.calls if isinstance(m, AnswerCallbackQuery)]

    def deleted_message_ids(self) -> list[int]:
        return [m.message_id for m in self.calls if isinstance(m, DeleteMessage)]

    def edited_captions(self) -> list[EditMessageCaption]:
        return [m for m in self.calls if isinstance(m, EditMessageCaption)]

    def edited_texts(self) -> list[EditMessageText]:
        return [m for m in self.calls if isinstance(m, EditMessageText)]

    def reset(self) -> None:
        self.calls.clear()


_update_id = 0


def _next_update_id() -> int:
    global _update_id
    _update_id += 1
    return _update_id


def web_app_data_update(data: str, user_id: int = STRANGER_ID, username: str | None = "buyer") -> dict:
    """What Telegram sends when a Mini App opened from a reply-keyboard button
    calls Telegram.WebApp.sendData(data)."""
    sender = {"id": user_id, "is_bot": False, "first_name": "Ivan", "last_name": "Petrov"}
    if username:
        sender["username"] = username
    return {
        "update_id": _next_update_id(),
        "message": {
            "message_id": _next_update_id(),
            "date": int(datetime.now(timezone.utc).timestamp()),
            "chat": {"id": user_id, "type": "private"},
            "from": sender,
            "web_app_data": {"data": data, "button_text": "Магазин"},
        },
    }


def _user(user_id: int) -> User:
    return User(id=user_id, is_bot=False, first_name="Lev", username="lev")


def text_update(text: str, user_id: int = ADMIN_ID) -> dict:
    payload: dict = {
        "message_id": _next_update_id(),
        "date": int(datetime.now(timezone.utc).timestamp()),
        "chat": {"id": user_id, "type": "private"},
        "from": {"id": user_id, "is_bot": False, "first_name": "Lev", "username": "lev"},
        "text": text,
    }
    if text.startswith("/"):
        cmd = text.split()[0]
        payload["entities"] = [{"type": "bot_command", "offset": 0, "length": len(cmd)}]
    return {"update_id": _next_update_id(), "message": payload}


def _media_message(user_id: int, media_group_id: str | None, **fields) -> dict:
    message = {
        "message_id": _next_update_id(),
        "date": int(datetime.now(timezone.utc).timestamp()),
        "chat": {"id": user_id, "type": "private"},
        "from": {"id": user_id, "is_bot": False, "first_name": "Lev"},
        **fields,
    }
    if media_group_id:
        message["media_group_id"] = media_group_id
    return {"update_id": _next_update_id(), "message": message}


def photo_update(file_unique_id: str, user_id: int = ADMIN_ID, media_group_id: str | None = None) -> dict:
    return _media_message(
        user_id,
        media_group_id,
        photo=[
            {"file_id": f"small_{file_unique_id}", "file_unique_id": f"s{file_unique_id}", "width": 90, "height": 90},
            {"file_id": f"big_{file_unique_id}", "file_unique_id": file_unique_id, "width": 1280, "height": 1280},
        ],
    )


MB = 1024 * 1024


def video_update(
    file_unique_id: str,
    *,
    duration: int = 15,
    file_size: int | None = 5 * MB,
    user_id: int = ADMIN_ID,
    media_group_id: str | None = None,
) -> dict:
    video = {
        "file_id": f"vid_{file_unique_id}",
        "file_unique_id": file_unique_id,
        "width": 1080,
        "height": 1920,
        "duration": duration,
        "mime_type": "video/mp4",
    }
    if file_size is not None:
        video["file_size"] = file_size
    return _media_message(user_id, media_group_id, video=video)


def document_update(
    file_unique_id: str,
    *,
    mime_type: str = "video/mp4",
    file_size: int | None = 5 * MB,
    user_id: int = ADMIN_ID,
) -> dict:
    document = {
        "file_id": f"doc_{file_unique_id}",
        "file_unique_id": file_unique_id,
        "file_name": f"{file_unique_id}.mp4",
        "mime_type": mime_type,
    }
    if file_size is not None:
        document["file_size"] = file_size
    return _media_message(user_id, None, document=document)


def callback_update(data: str, user_id: int = ADMIN_ID) -> dict:
    return {
        "update_id": _next_update_id(),
        "callback_query": {
            "id": str(_next_update_id()),
            "from": {"id": user_id, "is_bot": False, "first_name": "Lev"},
            "chat_instance": "ci",
            "data": data,
            "message": {
                "message_id": _next_update_id(),
                "date": int(datetime.now(timezone.utc).timestamp()),
                "chat": {"id": user_id, "type": "private"},
                "text": "prompt",
            },
        },
    }


async def feed(dp, bot: FakeBot, payload: dict) -> None:
    update = Update.model_validate(payload, context={"bot": bot})
    await dp.feed_update(bot, update)


def button_datas(markup) -> list[str]:
    return [b.callback_data for row in markup.inline_keyboard for b in row]


def button_texts(markup) -> list[str]:
    return [b.text for row in markup.inline_keyboard for b in row]
