"""
Token Router provider for reply generation.

Used as a fallback when Gemini API is unavailable or quota is exceeded.
Uses the OpenAI-compatible API from Token Router via httpx.
"""

import logging
from typing import List

from ai.prompts import get_reply_prompt, parse_reply_options

logger = logging.getLogger(__name__)


class TokenRouterProvider:
    """Generate tweet replies using Token Router (OpenAI compatible API)."""

    def __init__(self, url: str, api_key: str, model: str):
        self.url = url.rstrip('/')
        self.api_key = api_key
        self.model = model

    async def _post_chat_completions(self, messages: list, temperature: float = 0.8) -> str:
        """Helper to make POST request to OpenAI-compatible completions endpoint."""
        import httpx
        
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature
        }
        
        endpoint = f"{self.url}/chat/completions"
        if not endpoint.endswith("/chat/completions") and "/v1" not in endpoint:
            # fallback if url was provided strangely
            pass
            
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(endpoint, headers=headers, json=payload, timeout=30.0)
                response.raise_for_status()
                data = response.json()
                return data.get("choices", [{}])[0].get("message", {}).get("content", "")
            except Exception as e:
                logger.error(f"Token Router HTTP request failed: {e}")
                return ""

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
        prompt = get_reply_prompt(
            username=username,
            display_name=display_name,
            tweet_text=tweet_text,
            topic=topic,
            language=language,
            has_media=has_media,
            skills_context=skills_context,
        )

        raw_text = await self._post_chat_completions(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.8
        )
        
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

    async def generate_post(self, trend_context: str = "", skills_context: str = "") -> str:
        from ai.prompts import get_auto_post_prompt
        
        raw_text = await self._post_chat_completions(
            messages=[{"role": "user", "content": get_auto_post_prompt(trend_context, skills_context)}],
            temperature=0.8
        )
        
        if not raw_text:
            logger.warning("Token Router returned empty response for auto-post")
            return ""

        return raw_text.strip().strip('"').strip("'")

    async def analyze_timeline(self, tweets_text: str) -> str:
        from ai.prompts import TIMELINE_ANALYSIS_PROMPT
        prompt = TIMELINE_ANALYSIS_PROMPT.format(tweets=tweets_text)
        
        raw_text = await self._post_chat_completions(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3
        )
        return raw_text.strip() if raw_text else ""

    async def analyze_post_mortem(self, post_text: str, likes: int, replies: int) -> str:
        from ai.prompts import POST_MORTEM_PROMPT
        prompt = POST_MORTEM_PROMPT.format(post_text=post_text, likes=likes, replies=replies)
        
        raw_text = await self._post_chat_completions(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3
        )
        return raw_text.strip() if raw_text else ""

    async def is_available(self) -> bool:
        """Check if Token Router API is available."""
        import httpx
        
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        endpoint = f"{self.url}/models"
        
        async with httpx.AsyncClient() as client:
            try:
                # We do a basic GET to models to ensure auth and server are alive
                response = await client.get(endpoint, headers=headers, timeout=10.0)
                if response.status_code == 200:
                    data = response.json()
                    models = [m.get("id") for m in data.get("data", [])]
                    if self.model not in models:
                        logger.warning(f"Token Router model '{self.model}' not found. Available: {models}")
                    return True
                else:
                    logger.warning(f"Token Router API not available. Status Code: {response.status_code}")
                    return False
            except Exception as e:
                logger.warning(f"Token Router API not available: {e}")
                return False
