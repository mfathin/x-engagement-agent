"""
AI Reply Generator — orchestrates Gemini (primary) and Ollama (fallback).

Tries Gemini first, falls back to Ollama if Gemini fails. Returns empty
list if both providers fail, so the caller can handle it gracefully.
"""

import logging
from typing import Dict, Any, List, Optional

from ai.gemini_provider import GeminiProvider
from ai.ollama_provider import OllamaProvider
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
        self._ollama: Optional[OllamaProvider] = None

        # Initialize enabled providers
        if settings.gemini.enabled:
            self._gemini = GeminiProvider(
                api_key=settings.gemini.api_key,
                model=settings.gemini.model,
            )
            logger.info(f"Gemini provider enabled (model: {settings.gemini.model})")

        if settings.ollama.enabled:
            self._ollama = OllamaProvider(
                url=settings.ollama.url,
                model=settings.ollama.model,
            )
            logger.info(f"Ollama provider enabled (model: {settings.ollama.model})")

    async def generate(
        self,
        tweet_data: Dict[str, Any],
        topic: str = "general",
        language: str = "en",
        skills_context: str = "",
    ) -> tuple[List[str], str]:
        """
        Generate reply drafts for a tweet.

        Tries Gemini first, then Ollama as fallback.

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

        # --- Use Ollama Only ---
        if self._ollama:
            if await self._ollama.is_available():
                logger.info("Attempting reply generation with Ollama...")
                replies = await self._ollama.generate_replies(
                    tweet_text=tweet_text,
                    username=username,
                    display_name=display_name,
                    topic=topic,
                    language=language,
                )
                if replies:
                    replies = [f"{r} - A" for r in replies]
                    logger.info(f"✅ Ollama generated {len(replies)} drafts")
                    return replies, "ollama"
                else:
                    logger.warning("Ollama also failed to generate replies")
            else:
                logger.warning("Ollama server not available for fallback")

        # --- Failed ---
        logger.error("❌ Ollama failed to generate replies")
        return [], ""

    async def generate_post(self, trend_context: str = "", skills_context: str = "") -> tuple[str, str]:
        """
        Generate a single auto-post draft.
        Returns (draft_text, provider_name). Returns ("", "") on failure.
        """
        if self._ollama and await self._ollama.is_available():
            if hasattr(self._ollama, "generate_post"):
                logger.info("Attempting post generation with Ollama...")
                draft = await self._ollama.generate_post(trend_context, skills_context)
                if draft:
                    return draft, "ollama"

        logger.error("❌ Ollama failed to generate post draft")
        return "", ""

    async def analyze_trends(self, tweets_text: str) -> str:
        """Analyze trending tweets."""
        if self._gemini and self._gemini.is_available():
            res = await self._gemini.analyze_trends(tweets_text)
            if res: return res
        
        return ""

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

        if self._ollama:
            status["ollama"] = await self._ollama.is_available()
        else:
            status["ollama"] = False

        return status
