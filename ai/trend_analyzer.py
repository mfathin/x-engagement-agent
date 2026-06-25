"""
Trend Analyzer Module

Periodically searches X for trending topics within the user's niche (scams, phishing)
and uses Gemini to extract the most effective hooks and current sub-topics.
This data is then fed into the auto-post generator to create highly contextual tweets.
"""

import logging
from browser.scraper import TweetScraper
from ai.generator import ReplyGenerator
from ai.skill_manager import SkillManager

logger = logging.getLogger(__name__)

class TrendAnalyzer:
    """Analyzes current X trends to provide context for auto-posting."""

    def __init__(self, scraper: TweetScraper, generator: ReplyGenerator, skill_manager: SkillManager):
        self.scraper = scraper
        self.generator = generator
        self.skill_manager = skill_manager

    async def learn_from_timeline(self) -> None:
        """
        Scrape the user's For You timeline, deduce their overarching niche,
        and save the learnings to SKILLS.md.
        """
        logger.info("🧠 Learning niche from timeline...")
        try:
            tweets = await self.scraper.discover_timeline_tweets()
            if not tweets:
                logger.warning("📉 No timeline tweets found to analyze.")
                return

            tweets_text = "\n\n".join(
                [f"Tweet {i+1} (@{t.username}): {t.tweet_text} (Likes: {t.likes})" 
                 for i, t in enumerate(tweets[:15])] 
            )
            
            niche_context = await self.generator.analyze_timeline(tweets_text)
            
            if niche_context:
                logger.info("✅ Timeline analyzed. Updating SKILLS.md...")
                self.skill_manager.overwrite_niche(niche_context)
            else:
                logger.warning("📉 AI failed to deduce niche from timeline.")
                
        except Exception as e:
            logger.error(f"❌ Error learning from timeline: {e}")

    async def learn_persona_from_past_replies(self) -> None:
        """
        Scrape the logged-in user's past replies to learn their writing style
        and update SKILLS.md. Focuses on older replies to match historical style.
        """
        logger.info("🗣️ Learning user persona from past replies (before 17 June 2026)...")
        try:
            # 1. Get logged-in username
            await self.scraper.page.goto("https://x.com/home", wait_until="domcontentloaded")
            import asyncio
            profile_link = self.scraper.page.locator('a[data-testid="AppTabBar_Profile_Link"]').first
            try:
                await profile_link.wait_for(state="visible", timeout=15000)
            except Exception:
                logger.error("Could not find profile link to determine username within timeout.")
                return
                
            href = await profile_link.get_attribute("href")
            if not href:
                logger.error("Profile link has no href.")
                return
            
            username = href.strip("/")
            logger.info(f"👤 Detected logged-in user: @{username}")

            # 2. Search for replies before June 17, 2026
            query = f"from:{username} until:2026-06-17"
            tweets = await self.scraper.discover_tweets(query, limit=15)
            
            if not tweets:
                logger.warning(f"📉 No past replies found before 17 June 2026 for @{username}.")
                return

            # Keep only tweets by the user (redundant since we used from:username, but safe)
            user_tweets = [t for t in tweets if t.username.lower() == username.lower()]
            if not user_tweets:
                return

            tweets_text = "\n\n".join(
                [f"Tweet: {t.tweet_text}" for t in user_tweets[:10]] 
            )
            
            prompt = f"""You are analyzing a user's historical Twitter replies to learn their writing style, tone, and persona.
            
REPLIES:
{tweets_text}

Task:
Describe this user's writing style, tone, typical vocabulary, and persona in Indonesian (under 100 words). Make it a direct instruction (e.g. "Gaya bahasamu santai, sering menggunakan emoji...").
This will be saved to SKILLS.md to ensure future AI-generated replies sound exactly like them.

Persona:"""
            
            if self.generator._gemini and self.generator._gemini.is_available():
                response = await asyncio.to_thread(
                    self.generator._gemini._get_client().models.generate_content,
                    model=self.generator._gemini.model,
                    contents=prompt,
                )
                if response and response.text:
                    persona_desc = response.text.strip()
                    logger.info("✅ Persona analyzed. Updating SKILLS.md...")
                    self.skill_manager.update_skills(f"[PERSONA] {persona_desc}")
                
        except Exception as e:
            logger.error(f"❌ Error learning persona: {e}")
