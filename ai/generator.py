"""
AI Reply Generator — orchestrates Gemini (primary) and Ollama (fallback).

Tries Gemini first, falls back to Ollama if Gemini fails. Returns empty
list if both providers fail, so the caller can handle it gracefully.
"""

import logging
from typing import Dict, Any, List, Optional

from ai.gemini_provider import GeminiProvider
from ai.token_router_provider import TokenRouterProvider
from config.settings import Settings

logger = logging.getLogger(__name__)


class ReplyGenerator:
    """
    Orchestrates AI reply generation with provider fallback.

    Usage:
        generator = ReplyGenerator(settings)
        drafts, provider = await generator.generate(tweet_data)
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self._gemini: Optional[GeminiProvider] = None
        self._token_router: Optional[TokenRouterProvider] = None

        # Initialize enabled providers
        if settings.gemini.enabled:
            self._gemini = GeminiProvider(
                api_key=settings.gemini.api_key,
                model=settings.gemini.model,
            )
            logger.info(f"Gemini provider enabled (model: {settings.gemini.model})")

        if settings.token_router.enabled:
            self._token_router = TokenRouterProvider(
                url=settings.token_router.url,
                api_key=settings.token_router.api_key,
                model=settings.token_router.model,
            )
            logger.info(f"Token Router provider enabled (model: {settings.token_router.model})")

    async def generate(
        self,
        tweet_data: Dict[str, Any],
        topic: str = "general",
        language: str = "en",
        skills_context: str = "",
    ) -> tuple[List[str], str]:
        """
        Generate reply drafts for a tweet.

        Tries Gemini first, then Token Router as fallback.

        Args:
            tweet_data: Dict with keys: tweet_text, username, author_name, has_media
            topic: Topic category for prompt context
            language: 'en' or 'id'
            skills_context: Learnings to guide persona

        Returns:
            Tuple of (list of reply strings, provider name used).
            Returns ([], '') if all providers fail.
        """
        tweet_text = tweet_data.get("tweet_text", "")
        username = tweet_data.get("username", "")
        display_name = tweet_data.get("author_name", username)
        has_media = tweet_data.get("has_media", False)

        if not tweet_text:
            logger.warning("Cannot generate replies for empty tweet text")
            return [], ""

        # --- Use Token Router Only ---
        if self._token_router:
            if await self._token_router.is_available():
                logger.info("Attempting reply generation with Token Router...")
                replies = await self._token_router.generate_replies(
                    tweet_text=tweet_text,
                    username=username,
                    display_name=display_name,
                    topic=topic,
                    language=language,
                )
                if replies:
                    logger.info(f"✅ Token Router generated {len(replies)} drafts")
                    return replies, "token_router"
                else:
                    logger.warning("Token Router also failed to generate replies")
            else:
                logger.warning("Token Router server not available for fallback")

        # --- Failed ---
        logger.error("❌ Token Router failed to generate replies")
        return [], ""

    async def generate_post(self, trend_context: str = "", skills_context: str = "") -> tuple[str, str]:
        """
        Generate a single auto-post draft.
        Returns (draft_text, provider_name). Returns ("", "") on failure.
        """
        if self._token_router and await self._token_router.is_available():
            if hasattr(self._token_router, "generate_post"):
                logger.info("Attempting post generation with Token Router...")
                draft = await self._token_router.generate_post(trend_context, skills_context)
                if draft:
                    return draft, "token_router"

        logger.error("❌ Token Router failed to generate post draft")
        return "", ""

    async def analyze_timeline(self, tweets_text: str) -> str:
        """Analyze timeline tweets to deduce niche."""
        if self._gemini and self._gemini.is_available():
            res = await self._gemini.analyze_timeline(tweets_text)
            if res: return res
        
        return ""

    async def analyze_post_mortem(self, post_text: str, likes: int, replies: int) -> str:
        """Analyze successful post."""
        if self._gemini and self._gemini.is_available():
            res = await self._gemini.analyze_post_mortem(post_text, likes, replies)
            if res: return res
            
        return ""

    async def health_check(self) -> Dict[str, bool]:
        """Check availability of all configured providers."""
        status = {}

        if self._gemini:
            status["gemini"] = self._gemini.is_available()
        else:
            status["gemini"] = False

        if self._token_router:
            status["token_router"] = await self._token_router.is_available()
        else:
            status["token_router"] = False

        return status
