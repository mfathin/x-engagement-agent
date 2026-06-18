"""
Google Gemini API provider for reply generation.

Uses the new google-genai SDK (not the deprecated google-generativeai).
"""

import logging
from typing import List

from ai.prompts import get_reply_prompt, parse_reply_options

logger = logging.getLogger(__name__)


class GeminiProvider:
    """Generate tweet replies using Google Gemini API."""

    def __init__(self, api_key: str, model: str = "gemini-2.5-flash"):
        self.api_key = api_key
        self.model = model
        self._client = None

    def _get_client(self):
        """Lazy-initialize the Gemini client."""
        if self._client is None:
            try:
                from google import genai

                self._client = genai.Client(api_key=self.api_key)
            except ImportError:
                logger.error(
                    "google-genai package not installed. "
                    "Run: pip install google-genai"
                )
                raise
        return self._client

    async def generate_replies(
        self,
        tweet_text: str,
        username: str,
        display_name: str,
        topic: str = "general",
        language: str = "en",
    ) -> List[str]:
        """
        Generate reply drafts using Gemini.

        Args:
            tweet_text: The tweet content to reply to
            username: Tweet author's @handle
            display_name: Tweet author's display name
            topic: Topic category for context
            language: 'en' or 'id' for prompt language

        Returns:
            List of 1-3 reply strings, or empty list on failure
        """
        prompt = get_reply_prompt(
            username=username,
            display_name=display_name,
            tweet_text=tweet_text,
            topic=topic,
            language=language,
        )

        try:
            client = self._get_client()

            # Use synchronous call wrapped for compatibility
            # (google-genai doesn't have native async yet for all methods)
            import asyncio

            response = await asyncio.to_thread(
                client.models.generate_content,
                model=self.model,
                contents=prompt,
            )

            if not response or not response.text:
                logger.warning("Gemini returned empty response")
                return []

            raw_text = response.text
            logger.debug(f"Gemini raw response:\n{raw_text}")

            replies = parse_reply_options(raw_text)

            if not replies:
                logger.warning("Failed to parse reply options from Gemini response")
                return []

            logger.info(f"Gemini generated {len(replies)} reply drafts")
            return replies

        except Exception as e:
            logger.error(f"Gemini API error: {type(e).__name__}: {e}")
            return []

    async def generate_post(self) -> str:
        """Generate a single engaging auto-post draft."""
        from ai.prompts import AUTO_POST_PROMPT_ID
        try:
            client = self._get_client()
            import asyncio
            response = await asyncio.to_thread(
                client.models.generate_content,
                model=self.model,
                contents=AUTO_POST_PROMPT_ID,
            )
            if not response or not response.text:
                return ""
            # Clean up the output in case the AI wraps it in quotes
            return response.text.strip().strip('"').strip("'")
        except Exception as e:
            logger.error(f"Gemini API error generating post: {e}")
            return ""

    def is_available(self) -> bool:
        """Check if Gemini is configured and ready."""
        return bool(self.api_key) and self.api_key != "your_gemini_key"
