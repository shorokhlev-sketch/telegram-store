from functools import lru_cache
from urllib.parse import quote
from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# backend/app/config.py -> repo root -> web/dist (same layout on lab: $REMOTE/web/dist).
_DEFAULT_WEB_DIST = Path(__file__).resolve().parents[2] / "web" / "dist"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    bot_token: str = ""
    bot_username: str = ""
    miniapp_short_name: str = ""
    channel_id: int = 0
    admin_ids: Annotated[list[int], NoDecode] = Field(default_factory=list)
    seller_username: str = ""
    leads_chat_id: int = 0
    public_base_url: str = "http://localhost:8000"
    webhook_secret: str = ""
    db_path: Path = Path("/data/app.db")
    media_dir: Path = Path("/data/media")
    backup_chat_id: int = 0
    backup_hour_utc: int = 3
    set_webhook_on_start: bool = True
    web_dist_dir: Path = _DEFAULT_WEB_DIST
    shop_name: str = ""

    @field_validator("channel_id", "backup_chat_id", "leads_chat_id", mode="before")
    @classmethod
    def _empty_int_is_zero(cls, value: object) -> object:
        if value is None or (isinstance(value, str) and not value.strip()):
            return 0
        return value

    @field_validator("seller_username", mode="before")
    @classmethod
    def _strip_at(cls, value: object) -> object:
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip().lstrip("@").strip()
        return value

    @field_validator("admin_ids", mode="before")
    @classmethod
    def _parse_admin_ids(cls, value: object) -> list[int]:
        if value is None or value == "":
            return []
        if isinstance(value, str):
            return [int(part) for part in value.split(",") if part.strip()]
        if isinstance(value, (list, tuple)):
            return [int(v) for v in value]
        raise ValueError("ADMIN_IDS must be a comma-separated list of integers")

    @property
    def database_url(self) -> str:
        return f"sqlite+aiosqlite:///{self.db_path}"

    @property
    def webhook_path(self) -> str:
        return "/tg/webhook"

    @property
    def webhook_url(self) -> str:
        return f"{self.public_base_url.rstrip('/')}{self.webhook_path}"

    @property
    def miniapp_url(self) -> str:
        if self.miniapp_short_name:
            return f"https://t.me/{self.bot_username}/{self.miniapp_short_name}"
        return f"https://t.me/{self.bot_username}"

    def miniapp_item_url(self, public_id: str) -> str:
        return f"{self.miniapp_url}?startapp=item_{public_id}"

    @property
    def app_url(self) -> str:
        """Storefront URL for `web_app` buttons inside the bot (menu button,
        reply keyboard). Opened this way the app runs as a Mini App."""
        return f"{self.public_base_url.rstrip('/')}/"

    def app_item_url(self, public_id: str) -> str:
        """Item page for inline `web_app` buttons in private chats."""
        return f"{self.public_base_url.rstrip('/')}/item/{public_id}"

    @property
    def leads_chat_id_effective(self) -> int:
        """Where buyer leads go: LEADS_CHAT_ID, else the first admin, else 0 (nowhere)."""
        if self.leads_chat_id:
            return self.leads_chat_id
        return self.admin_ids[0] if self.admin_ids else 0

    def seller_chat_url(self, text: str) -> str | None:
        """Private chat with the seller with `text` prefilled; None when no seller is set."""
        if not self.seller_username:
            return None
        return f"https://t.me/{self.seller_username}?text={quote(text)}"

    def is_admin(self, telegram_id: int) -> bool:
        return telegram_id in self.admin_ids


@lru_cache
def get_settings() -> Settings:
    return Settings()
