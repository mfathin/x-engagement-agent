"""
Token Router provider for reply generation.

Used as a fallback when Gemini API is unavailable or quota is exceeded.
Uses the OpenAI-compatible API from Token Router.
"""

import logging
from typing import List

from ai.prompts import get_reply_prompt, parse_reply_options

logger = logging.getLogger(__name__)


class TokenRouterProvider:
    """Generate tweet replies using Token Router (OpenAI compatible API)."""

    def __init__(self, url: str, api_key: str, model: str):
        self.url = url
        self.api_key = api_key
        self.model = model

        # Import inside init to avoid requiring openai if not enabled
        try:
            from openai import AsyncOpenAI
            self.client = AsyncOpenAI(
                base_url=self.url,
                api_key=self.api_key,
            )
        except ImportError:
            self.client = None
            logger.error("openai package not installed. Run: pip install openai")

    async def generate_replies(
        self,
        tweet_text: str,
        username: str,
        display_name: str,
        topic: str = "general",
        language: str = "en",
        has_media: bool = False,
        skills_context: str = "",
    ) -> List[str]:
        """
        Generate reply drafts using Token Router.

        Args:
            tweet_text: The tweet content to reply to
            username: Tweet author's @handle
            display_name: Tweet author's display name
            topic: Topic category for context
            language: 'en' or 'id' for prompt language
            has_media: Whether the tweet contains media
            skills_context: Context about user skills

        Returns:
            List of 1-3 reply strings, or empty list on failure
        """
        if not self.client:
            return []

        prompt = get_reply_prompt(
            username=username,
            display_name=display_name,
            tweet_text=tweet_text,
            topic=topic,
            language=language,
            has_media=has_media,
            skills_context=skills_context,
        )

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.8,
            )

            raw_text = response.choices[0].message.content
            if not raw_text:
                logger.warning("Token Router returned empty response")
                return []

            logger.debug(f"Token Router raw response:\n{raw_text}")

            replies = parse_reply_options(raw_text)

            if not replies:
                logger.warning("Failed to parse reply options from Token Router response")
                return []

            logger.info(f"Token Router generated {len(replies)} reply drafts")
            return replies

        except Exception as e:
            logger.error(f"Token Router error: {type(e).__name__}: {e}")
            return []

    async def generate_post(self, trend_context: str = "", skills_context: str = "") -> str:
        """Generate a single engaging auto-post draft using Token Router."""
        if not self.client:
            return ""

        from ai.prompts import get_auto_post_prompt

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": get_auto_post_prompt(trend_context, skills_context)}],
                temperature=0.8,
            )

            raw_text = response.choices[0].message.content
            if not raw_text:
                logger.warning("Token Router returned empty response for auto-post")
                return ""

            return raw_text.strip().strip('"').strip("'")
        except Exception as e:
            logger.error(f"Token Router error generating post: {e}")
            return ""

    async def analyze_trends(self, tweets_text: str) -> str:
        """Analyze trending tweets using Token Router."""
        if not self.client:
            return ""

        from ai.prompts import TREND_ANALYSIS_PROMPT
        try:
            prompt = TREND_ANALYSIS_PROMPT.format(tweets=tweets_text)
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
            )
            raw_text = response.choices[0].message.content
            return raw_text.strip() if raw_text else ""
        except Exception as e:
            logger.error(f"Token Router error analyzing trends: {e}")
            return ""

    async def analyze_timeline(self, tweets_text: str) -> str:
        """Analyze timeline tweets using Token Router."""
        if not self.client:
            return ""

        from ai.prompts import TIMELINE_ANALYSIS_PROMPT
        try:
            prompt = TIMELINE_ANALYSIS_PROMPT.format(tweets=tweets_text)
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
            )
            raw_text = response.choices[0].message.content
            return raw_text.strip() if raw_text else ""
        except Exception as e:
            logger.error(f"Token Router error analyzing timeline: {e}")
            return ""

    async def analyze_post_mortem(self, post_text: str, likes: int, replies: int) -> str:
        """Analyze post performance using Token Router."""
        if not self.client:
            return ""

        from ai.prompts import POST_MORTEM_PROMPT
        try:
            prompt = POST_MORTEM_PROMPT.format(post_text=post_text, likes=likes, replies=replies)
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
            )
            raw_text = response.choices[0].message.content
            return raw_text.strip() if raw_text else ""
        except Exception as e:
            logger.error(f"Token Router error analyzing post-mortem: {e}")
            return ""

    async def is_available(self) -> bool:
        """Check if Token Router API is available."""
        if not self.client:
            return False

        try:
            # Token Router usually supports listing models, let's just make a very basic request or check models list
            models = await self.client.models.list()
            model_names = [m.id for m in models.data]
            
            if self.model not in model_names:
                logger.warning(
                    f"Token Router model '{self.model}' not found in available models: {model_names}"
                )
            
            return True
        except Exception as e:
            logger.warning(f"Token Router API not available: {e}")
            return False
