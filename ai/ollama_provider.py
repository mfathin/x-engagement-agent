"""
Ollama (local LLM) provider for reply generation.

Used as a fallback when Gemini API is unavailable or quota is exceeded.
Requires Ollama to be running locally.
"""

import logging
from typing import List

from ai.prompts import get_reply_prompt, parse_reply_options

logger = logging.getLogger(__name__)


class OllamaProvider:
    """Generate tweet replies using a local Ollama model."""

    def __init__(self, url: str = "http://localhost:11434", model: str = "llama3"):
        self.url = url
        self.model = model

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
        Generate reply drafts using local Ollama model.

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
            import asyncio
            import ollama

            # ollama.generate is synchronous, wrap it
            response = await asyncio.to_thread(
                ollama.generate,
                model=self.model,
                prompt=prompt,
                options={
                    "temperature": 0.8,
                    "num_ctx": 4096,
                },
            )

            raw_text = response.get("response", "")
            if not raw_text:
                logger.warning("Ollama returned empty response")
                return []

            logger.debug(f"Ollama raw response:\n{raw_text}")

            replies = parse_reply_options(raw_text)

            if not replies:
                logger.warning("Failed to parse reply options from Ollama response")
                return []

            logger.info(f"Ollama generated {len(replies)} reply drafts")
            return replies

        except ImportError:
            logger.error(
                "ollama package not installed. Run: pip install ollama"
            )
            return []
        except Exception as e:
            logger.error(f"Ollama error: {type(e).__name__}: {e}")
            return []

    async def generate_post(self, trend_context: str = "", skills_context: str = "") -> str:
        """Generate a single engaging auto-post draft using Ollama."""
        from ai.prompts import get_auto_post_prompt

        try:
            import asyncio
            import ollama

            response = await asyncio.to_thread(
                ollama.generate,
                model=self.model,
                prompt=get_auto_post_prompt(trend_context, skills_context),
                options={
                    "temperature": 0.8,
                    "num_ctx": 2048,
                },
            )

            raw_text = response.get("response", "")
            if not raw_text:
                logger.warning("Ollama returned empty response for auto-post")
                return ""

            return raw_text.strip().strip('"').strip("'")
        except Exception as e:
            logger.error(f"Ollama error generating post: {e}")
            return ""

    async def analyze_trends(self, tweets_text: str) -> str:
        """Analyze trending tweets using Ollama."""
        from ai.prompts import TREND_ANALYSIS_PROMPT
        try:
            import asyncio
            import ollama
            
            prompt = TREND_ANALYSIS_PROMPT.format(tweets=tweets_text)
            response = await asyncio.to_thread(
                ollama.generate,
                model=self.model,
                prompt=prompt,
                options={"temperature": 0.3, "num_ctx": 4096},
            )
            raw_text = response.get("response", "")
            return raw_text.strip() if raw_text else ""
        except Exception as e:
            logger.error(f"Ollama error analyzing trends: {e}")
            return ""

    async def analyze_timeline(self, tweets_text: str) -> str:
        """Analyze timeline tweets using Ollama."""
        from ai.prompts import TIMELINE_ANALYSIS_PROMPT
        try:
            import asyncio
            import ollama
            
            prompt = TIMELINE_ANALYSIS_PROMPT.format(tweets=tweets_text)
            response = await asyncio.to_thread(
                ollama.generate,
                model=self.model,
                prompt=prompt,
                options={"temperature": 0.3, "num_ctx": 4096},
            )
            raw_text = response.get("response", "")
            return raw_text.strip() if raw_text else ""
        except Exception as e:
            logger.error(f"Ollama error analyzing timeline: {e}")
            return ""

    async def analyze_post_mortem(self, post_text: str, likes: int, replies: int) -> str:
        """Analyze post performance using Ollama."""
        from ai.prompts import POST_MORTEM_PROMPT
        try:
            import asyncio
            import ollama
            
            prompt = POST_MORTEM_PROMPT.format(post_text=post_text, likes=likes, replies=replies)
            response = await asyncio.to_thread(
                ollama.generate,
                model=self.model,
                prompt=prompt,
                options={"temperature": 0.3, "num_ctx": 2048},
            )
            raw_text = response.get("response", "")
            return raw_text.strip() if raw_text else ""
        except Exception as e:
            logger.error(f"Ollama error analyzing post-mortem: {e}")
            return ""

    async def is_available(self) -> bool:
        """Check if Ollama server is running and the model is available."""
        try:
            import asyncio
            import ollama

            # Try to list models — if Ollama isn't running, this will fail
            models_response = await asyncio.to_thread(ollama.list)
            
            # Extract models using object attributes, supporting both old dicts and new Pydantic models
            models_list = getattr(models_response, "models", None) or models_response.get("models", [])
            model_names = []
            for m in models_list:
                # Handle both dicts and objects, and 'name' vs 'model' keys
                if isinstance(m, dict):
                    name = m.get("name", m.get("model", ""))
                else:
                    name = getattr(m, "name", getattr(m, "model", ""))
                model_names.append(name.split(":")[0])
                
            available = self.model in model_names or f"{self.model}:latest" in [
                name for name in model_names
            ]

            if not available:
                logger.warning(
                    f"Ollama model '{self.model}' not found. "
                    f"Available: {model_names}. "
                    f"Run: ollama pull {self.model}"
                )
            return available

        except Exception as e:
            logger.warning(f"Ollama server not available: {e}")
            return False
