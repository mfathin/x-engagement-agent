"""
Application settings loaded from environment variables.

All configuration is centralized here. Values are read from a .env file
at startup via python-dotenv.
"""

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

from dotenv import load_dotenv

# Load .env from project root
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env")


def _get(key: str, default: str = "") -> str:
    """Get env variable with fallback."""
    return os.getenv(key, default)


def _get_bool(key: str, default: bool = False) -> bool:
    return _get(key, str(default)).lower() in ("true", "1", "yes")


def _get_int(key: str, default: int = 0) -> int:
    try:
        return int(_get(key, str(default)))
    except ValueError:
        return default


def _get_float(key: str, default: float = 0.0) -> float:
    try:
        return float(_get(key, str(default)))
    except ValueError:
        return default


def _get_list(key: str, default: str = "") -> List[str]:
    raw = _get(key, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


@dataclass
class TelegramConfig:
    bot_token: str = ""
    chat_id: str = ""
    webhook_url: str = ""


@dataclass
class GeminiConfig:
    enabled: bool = False
    api_key: str = ""
    model: str = "gemini-2.5-flash"


@dataclass
class TokenRouterConfig:
    enabled: bool = False
    url: str = "https://api.tokenrouter.com/v1"
    api_key: str = ""
    model: str = "MiniMax-M3"


@dataclass
class OmnirouteConfig:
    enabled: bool = False
    url: str = "http://localhost:20128/v1"
    api_key: str = ""
    model: str = "openai/codex"


@dataclass
class TwitterConfig:
    auth_token: str = ""


@dataclass
class EngagementConfig:
    queries: List[str] = field(default_factory=list)
    min_likes: int = 300
    min_replies: int = 20
    tweet_max_age_hours: int = 4
    notifications_enabled: bool = False
    notifications_check_interval: int = 10
    auto_post_enabled: bool = False
    auto_post_interval_minutes: int = 290


@dataclass
class RateLimitConfig:
    max_replies_per_hour: int = 3
    active_hours_start: int = 7   # 07:00
    active_hours_end: int = 23    # 23:00


@dataclass
class BudgetConfig:
    daily_budget: float = 5.00
    monthly_budget: float = 100.00


@dataclass
class BrowserConfig:
    data_dir: str = "browser_data"
    viewport_width: int = 1280
    viewport_height: int = 900


@dataclass
class LoggingConfig:
    log_dir: str = "logs"
    log_level: str = "INFO"


@dataclass
class Settings:
    """Central application configuration loaded from environment variables."""

    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    gemini: GeminiConfig = field(default_factory=GeminiConfig)
    token_router: TokenRouterConfig = field(default_factory=TokenRouterConfig)
    omniroute: OmnirouteConfig = field(default_factory=OmnirouteConfig)
    twitter: TwitterConfig = field(default_factory=TwitterConfig)
    engagement: EngagementConfig = field(default_factory=EngagementConfig)
    rate_limit: RateLimitConfig = field(default_factory=RateLimitConfig)
    budget: BudgetConfig = field(default_factory=BudgetConfig)
    browser: BrowserConfig = field(default_factory=BrowserConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    project_root: Path = field(default_factory=lambda: _PROJECT_ROOT)

    def __post_init__(self):
        """Load all values from environment after dataclass init."""
        # Telegram
        self.telegram = TelegramConfig(
            bot_token=_get("TELEGRAM_BOT_TOKEN"),
            chat_id=_get("TELEGRAM_CHAT_ID"),
            webhook_url=_get("TELEGRAM_WEBHOOK_URL"),
        )

        # Gemini
        self.gemini = GeminiConfig(
            enabled=_get_bool("GEMINI_ENABLED"),
            api_key=_get("GEMINI_API_KEY"),
            model=_get("GEMINI_MODEL", "gemini-2.5-flash"),
        )

        # Token Router
        self.token_router = TokenRouterConfig(
            enabled=_get_bool("TOKEN_ROUTER_ENABLED"),
            url=_get("TOKEN_ROUTER_URL", "https://api.tokenrouter.com/v1"),
            api_key=_get("TOKEN_ROUTER_API_KEY"),
            model=_get("TOKEN_ROUTER_MODEL", "MiniMax-M3"),
        )

        # Omniroute
        self.omniroute = OmnirouteConfig(
            enabled=_get_bool("OMNIROUTE_ENABLED"),
            url=_get("OMNIROUTE_URL", "http://localhost:20128/v1"),
            api_key=_get("OMNIROUTE_API_KEY"),
            model=_get("OMNIROUTE_MODEL", "openai/codex"),
        )

        # Twitter
        self.twitter = TwitterConfig(
            auth_token=_get("TWITTER_AUTH_TOKEN"),
        )

        # Engagement
        self.engagement = EngagementConfig(
            queries=_get_list("ENGAGEMENT_QUERIES", "AI,tech,startups"),
            min_likes=_get_int("ENGAGEMENT_MIN_LIKES", 20),
            min_replies=_get_int("ENGAGEMENT_MIN_REPLIES", 20),
            tweet_max_age_hours=_get_int("TWEET_MAX_AGE_HOURS", 4),
            notifications_enabled=_get_bool("NOTIFICATIONS_ENABLED", False),
            notifications_check_interval=_get_int("NOTIFICATIONS_CHECK_INTERVAL", 10),
            auto_post_enabled=_get_bool("AUTO_POST_ENABLED", False),
            auto_post_interval_minutes=_get_int("AUTO_POST_INTERVAL_MINUTES", 290),
        )

        # Rate Limit
        self.rate_limit = RateLimitConfig(
            max_replies_per_hour=_get_int("MAX_REPLIES_PER_HOUR", 3),
            active_hours_start=_get_int("ACTIVE_HOURS_START", 7),
            active_hours_end=_get_int("ACTIVE_HOURS_END", 23),
        )

        # Budget
        self.budget = BudgetConfig(
            daily_budget=_get_float("AI_DAILY_BUDGET", 5.00),
            monthly_budget=_get_float("AI_MONTHLY_BUDGET", 100.00),
        )

        # Browser
        self.browser = BrowserConfig(
            data_dir=_get("BROWSER_DATA_DIR", "browser_data"),
            viewport_width=_get_int("BROWSER_VIEWPORT_WIDTH", 1280),
            viewport_height=_get_int("BROWSER_VIEWPORT_HEIGHT", 900),
        )

        # Logging
        self.logging = LoggingConfig(
            log_dir=_get("LOG_DIR", "logs"),
            log_level=_get("LOG_LEVEL", "INFO"),
        )

    def validate(self) -> List[str]:
        """Validate required settings. Returns list of error messages."""
        errors = []

        if not self.telegram.bot_token:
            errors.append("TELEGRAM_BOT_TOKEN is required")
        if not self.telegram.chat_id:
            errors.append("TELEGRAM_CHAT_ID is required")

        if self.gemini.enabled and not self.gemini.api_key:
            errors.append("GEMINI_API_KEY is required when GEMINI_ENABLED=true")

        if not self.gemini.enabled and not self.token_router.enabled and not self.omniroute.enabled:
            errors.append(
                "At least one AI provider must be enabled "
                "(GEMINI_ENABLED, TOKEN_ROUTER_ENABLED, or OMNIROUTE_ENABLED)"
            )

        if not self.engagement.queries:
            errors.append("ENGAGEMENT_QUERIES must have at least one query")

        return errors

    def check_and_exit_on_errors(self):
        """Validate and exit if there are critical config errors."""
        errors = self.validate()
        if errors:
            print("❌ Configuration errors:")
            for err in errors:
                print(f"   • {err}")
            print("\nPlease check your .env file. See .env.example for reference.")
            sys.exit(1)

    def get_browser_data_path(self) -> Path:
        """Get absolute path to browser data directory."""
        p = Path(self.browser.data_dir)
        if not p.is_absolute():
            p = self.project_root / p
        p.mkdir(parents=True, exist_ok=True)
        return p

    def get_log_dir(self) -> Path:
        """Get absolute path to log directory."""
        p = Path(self.logging.log_dir)
        if not p.is_absolute():
            p = self.project_root / p
        p.mkdir(parents=True, exist_ok=True)
        return p
