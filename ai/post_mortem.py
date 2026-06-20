"""
Post Mortem Module

Periodically evaluates the engagement of past auto-posts.
If a post was successful, it feeds it to the AI to extract learnings and update SKILLS.md.
"""

import logging
import asyncio
from browser.scraper import TweetScraper
from ai.generator import ReplyGenerator
from ai.skill_manager import SkillManager
from db import queries as db

logger = logging.getLogger(__name__)

class PostMortemAnalyzer:
    """Analyzes the performance of past auto-posts to learn what works."""

    def __init__(self, scraper: TweetScraper, generator: ReplyGenerator, skill_manager: SkillManager):
        self.scraper = scraper
        self.generator = generator
        self.skill_manager = skill_manager

    async def run_post_mortem(self) -> None:
        """Find old unanalyzed posts, fetch their stats, and learn from successful ones."""
        logger.info("🔬 Running Post-Mortem Analysis on past auto-posts...")
        
        # Get posts older than 12 hours that haven't been analyzed
        posts = await db.get_unanalyzed_auto_posts(older_than_hours=12)
        if not posts:
            logger.info("🤷 No unanalyzed past auto-posts found.")
            return
            
        logger.info(f"📊 Found {len(posts)} posts ready for analysis.")
        
        for post in posts:
            post_id = post["id"]
            post_url = post["post_url"]
            post_text = post["post_text"]
            
            try:
                # Go to the tweet URL to scrape its current engagement
                logger.info(f"➜ Checking stats for post ID {post_id}: {post_url}")
                await self.scraper.page.goto(post_url, wait_until="domcontentloaded")
                await asyncio.sleep(3)
                
                # We can reuse _parse_all_tweets to parse the main article
                raw_tweets = await self.scraper._parse_all_tweets()
                if not raw_tweets:
                    logger.warning(f"Could not parse tweet {post_url}")
                    continue
                    
                # First parsed tweet should be the main one
                main_tweet = raw_tweets[0]
                likes = main_tweet.likes
                replies = main_tweet.replies
                
                logger.info(f"📈 Post ID {post_id} stats: {likes} Likes, {replies} Replies")
                
                # Update DB
                await db.update_auto_post_analyzed(post_id, likes, replies)
                
                # Check if successful (Thresholds could be in settings, hardcoded for now)
                # Let's say > 5 likes or > 2 replies is "successful" for a small account
                if likes >= 5 or replies >= 2:
                    logger.info("🌟 Post was successful! Extracting learnings...")
                    learning = await self.generator.analyze_post_mortem(post_text, likes, replies)
                    if learning:
                        logger.info(f"🧠 Learned: {learning[:50]}...")
                        self.skill_manager.update_skills(f"[POST MORTEM LEARNING] {learning}")
                else:
                    logger.info("📉 Post had low engagement. No specific learnings extracted.")
                    
            except Exception as e:
                logger.error(f"❌ Error analyzing post {post_id}: {e}")
            
            await asyncio.sleep(5) # Delay between checking posts
