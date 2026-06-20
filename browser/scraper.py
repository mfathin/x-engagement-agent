"""
Tweet Discovery — scrapes X/Twitter for trending tweets via browser.

Navigates to X search, scrolls to load tweets, parses DOM to extract
tweet data, and filters by age and engagement thresholds.
"""

import asyncio
import logging
import random
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Any

from playwright.async_api import Page, Locator

from config.settings import Settings
from config import selectors as sel
from db import queries as db

logger = logging.getLogger(__name__)


@dataclass
class TweetData:
    """Parsed tweet data from the DOM."""
    tweet_id: str = ""
    author_name: str = ""
    username: str = ""
    tweet_text: str = ""
    tweet_url: str = ""
    likes: int = 0
    replies: int = 0
    retweets: int = 0
    tweet_timestamp: str = ""
    parsed_time: Optional[datetime] = None
    has_media: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tweet_id": self.tweet_id,
            "author_name": self.author_name,
            "username": self.username,
            "tweet_text": self.tweet_text,
            "tweet_url": self.tweet_url,
            "likes": self.likes,
            "replies": self.replies,
            "retweets": self.retweets,
            "tweet_timestamp": self.tweet_timestamp,
            "has_media": self.has_media,
        }


class TweetScraper:
    """
    Scrapes tweets from X/Twitter search results via browser automation.

    Uses DOM parsing with data-testid selectors. Falls back to
    heuristic parsing if selectors change.
    """

    def __init__(self, page: Page, settings: Settings):
        self.page = page
        self.settings = settings

    async def discover_tweets(self, query: str) -> List[TweetData]:
        """
        Search for tweets matching query and return filtered results.

        Steps:
        1. Navigate to X search with "Latest" tab
        2. Scroll to load tweets
        3. Parse tweet articles from DOM
        4. Filter by age and engagement
        5. Deduplicate against database

        Args:
            query: Search query string

        Returns:
            List of TweetData objects passing all filters
        """
        logger.info(f"🔍 Searching tweets for: '{query}'")

        # Navigate to search
        search_url = (
            f"https://x.com/search?q={query}&src=typed_query&f=live"
        )
        try:
            await self.page.goto(search_url, wait_until="domcontentloaded")
        except Exception as e:
            logger.error(f"Failed to navigate to search: {e}")
            return []

        return await self._process_scraped_tweets(raw_url_context=search_url)

    async def discover_user_replies(self, username: str) -> List[TweetData]:
        """Fetch tweets from /{username}/with_replies."""
        logger.info(f"🔍 Scraping replies for: @{username}")
        replies_url = f"https://x.com/{username}/with_replies"
        try:
            await self.page.goto(replies_url, wait_until="domcontentloaded")
            return await self._process_scraped_tweets(raw_url_context=replies_url)
        except Exception as e:
            logger.error(f"Failed to navigate to user replies: {e}")
            return []

    async def discover_timeline_tweets(self) -> List[TweetData]:
        """Scrape tweets from the For You / Following timeline."""
        logger.info("🏠 Searching tweets on Homepage/Timeline")

        try:
            await self.page.goto("https://x.com/home", wait_until="domcontentloaded")
        except Exception as e:
            logger.error(f"Failed to navigate to home: {e}")
            return []

        return await self._process_scraped_tweets(raw_url_context="https://x.com/home")

    async def discover_notification_tweets(self) -> List[TweetData]:
        """
        Scrape tweets from the notifications page.
        
        Only processes "New post" notifications and ensures the tweet is
        under 15 minutes old.
        """
        logger.info("🔔 Checking notifications page for recent new posts")

        try:
            await self.page.goto("https://x.com/notifications", wait_until="domcontentloaded")
            try:
                await self.page.wait_for_selector('div[data-testid="cellInnerDiv"]', timeout=15000)
            except Exception:
                pass
            await self._random_delay(4, 7)
        except Exception as e:
            logger.error(f"Failed to navigate to notifications: {e}")
            return []

        tweet_urls_to_visit = set()
        grouped_cells_to_click = []

        try:
            cells = self.page.locator('div[data-testid="cellInnerDiv"]')
            count = await cells.count()
            
            # Limit to top 15 notifications to avoid scrolling forever
            for i in range(min(count, 15)):
                cell = cells.nth(i)
                text = (await cell.inner_text()).lower()
                
                # Check if it's a new post notification (ignore replies, likes, etc.)
                is_new_post = any(kw in text for kw in ["new post", "postingan baru", "tweeted"])
                if not is_new_post:
                    continue
                    
                # Look for direct status links in this cell
                status_links = cell.locator('a[href*="/status/"]')
                link_count = await status_links.count()
                
                if link_count > 0:
                    for j in range(link_count):
                        href = await status_links.nth(j).get_attribute("href")
                        if href and "/status/" in href:
                            url = f"https://x.com{href}" if href.startswith("/") else href
                            tweet_urls_to_visit.add(url)
                else:
                    # Grouped notification with no direct link, mark index to click later
                    grouped_cells_to_click.append(i)
                    
        except Exception as e:
            logger.error(f"Error parsing notification cells: {e}")
            
        discovered = []

        # 1. Process direct URLs
        for url in tweet_urls_to_visit:
            try:
                import re
                id_match = re.search(r"/status/(\d+)", url)
                if id_match:
                    tweet_id = id_match.group(1)
                    if await db.is_tweet_processed(tweet_id):
                        continue

                logger.info(f"  ➜ Fetching notification tweet: {url}")
                await self.page.goto(url, wait_until="domcontentloaded")
                await self._random_delay(2, 4)
                
                raw_tweets = await self._parse_all_tweets()
                if raw_tweets:
                    # First article is the main tweet
                    main_tweet = raw_tweets[0]
                    if main_tweet.parsed_time:
                        age = datetime.now(timezone.utc) - main_tweet.parsed_time
                        if age.total_seconds() <= 15 * 60:
                            discovered.append(main_tweet)
                        else:
                            logger.info(f"  ⏭️ Tweet too old ({age.total_seconds()/60:.1f} mins)")
            except Exception as e:
                logger.error(f"Error fetching tweet from notification URL: {e}")

        # 2. Process grouped notifications (if any)
        # We process these last because navigating back and forth resets DOM
        for index in grouped_cells_to_click:
            try:
                logger.info(f"  ➜ Clicking grouped notification at index {index}")
                await self.page.goto("https://x.com/notifications", wait_until="domcontentloaded")
                await self._random_delay(2, 4)
                
                cells = self.page.locator('div[data-testid="cellInnerDiv"]')
                if await cells.count() > index:
                    # Click near the top-left corner to avoid accidentally clicking 
                    # user profile <a> links that sit in the center of the text
                    await cells.nth(index).click(position={"x": 10, "y": 10})
                    try:
                        await self.page.wait_for_selector('article[data-testid="tweet"]', timeout=15000)
                    except Exception:
                        pass
                    await self._random_delay(4, 6)
                    
                    raw_tweets = await self._parse_all_tweets()
                    for tweet in raw_tweets:
                        if tweet.tweet_id and not await db.is_tweet_processed(tweet.tweet_id):
                            if tweet.parsed_time:
                                age = datetime.now(timezone.utc) - tweet.parsed_time
                                if age.total_seconds() <= 15 * 60:
                                    discovered.append(tweet)
                                else:
                                    logger.info(f"  ⏭️ Grouped tweet too old ({age.total_seconds()/60:.1f} mins)")
            except Exception as e:
                logger.error(f"Error processing grouped notification: {e}")

        logger.info(f"✅ Processed {len(discovered)} new notification tweets (< 15 mins)")
        return discovered

    async def _process_scraped_tweets(self, raw_url_context: str) -> List[TweetData]:
        """Internal helper to scroll, parse, and filter tweets after navigation."""

        # Wait for content to load, ensuring articles are visible if possible
        try:
            await self.page.wait_for_selector('article[data-testid="tweet"]', timeout=15000)
        except Exception:
            logger.warning("Timeout waiting for tweets to load. Will try to proceed anyway.")
        
        await self._random_delay(4, 7)

        # Scroll to load more tweets
        scroll_count = random.randint(6, 12)
        for i in range(scroll_count):
            await self._scroll_down()
            await self._random_delay(1.5, 3.5)

        # Parse all visible tweets
        raw_tweets = await self._parse_all_tweets()
        logger.info(f"📋 Parsed {len(raw_tweets)} tweets from DOM")

        # Filter by age and engagement
        filtered = []
        max_age_hours = self.settings.engagement.tweet_max_age_hours
        min_likes = self.settings.engagement.min_likes
        min_replies = self.settings.engagement.min_replies

        for tweet in raw_tweets:
            # Skip tweets without IDs
            if not tweet.tweet_id:
                continue

            # Check if already processed
            if await db.is_tweet_processed(tweet.tweet_id):
                logger.debug(f"⏭️ Already processed: {tweet.tweet_id}")
                continue

            # Check age
            if tweet.parsed_time:
                age = datetime.now(timezone.utc) - tweet.parsed_time
                if age > timedelta(hours=max_age_hours):
                    logger.debug(
                        f"⏭️ Too old ({age.total_seconds()/3600:.1f}h): {tweet.tweet_id}"
                    )
                    continue

            # Check engagement
            if tweet.likes < min_likes:
                logger.debug(
                    f"⏭️ Low likes ({tweet.likes}<{min_likes}): {tweet.tweet_id}"
                )
                continue

            if tweet.replies < min_replies:
                logger.debug(
                    f"⏭️ Low replies ({tweet.replies}<{min_replies}): {tweet.tweet_id}"
                )
                continue

            filtered.append(tweet)

        logger.info(
            f"✅ {len(filtered)} tweets passed filters "
            f"(from {len(raw_tweets)} total)"
        )

        return filtered

    async def _parse_all_tweets(self) -> List[TweetData]:
        """Parse all tweet articles currently visible in the DOM."""
        tweets = []

        try:
            articles = self.page.locator(sel.TWEET_ARTICLE)
            count = await articles.count()

            for i in range(count):
                try:
                    article = articles.nth(i)
                    tweet = await self._parse_tweet_element(article)
                    if tweet and tweet.tweet_text:
                        tweets.append(tweet)
                except Exception as e:
                    logger.debug(f"Error parsing tweet #{i}: {e}")
                    continue

        except Exception as e:
            logger.error(f"Error finding tweet articles: {e}")

        return tweets

    async def _parse_tweet_element(self, article: Locator) -> Optional[TweetData]:
        """
        Parse a single tweet article element into TweetData.

        Extracts text, author, timestamp, URL, and engagement counts.
        """
        tweet = TweetData()

        try:
            # ── Tweet Text ──────────────────────────────────────────
            try:
                text_el = article.locator(sel.TWEET_TEXT).first
                if await text_el.count() > 0:
                    tweet.tweet_text = (await text_el.inner_text()).strip()
            except Exception:
                pass

            if not tweet.tweet_text:
                return None

            # ── Author & Username ───────────────────────────────────
            try:
                user_el = article.locator(sel.USER_NAME).first
                if await user_el.count() > 0:
                    user_text = await user_el.inner_text()
                    lines = [l.strip() for l in user_text.split("\n") if l.strip()]

                    # First line is display name, line starting with @ is username
                    for line in lines:
                        if line.startswith("@"):
                            tweet.username = line.lstrip("@")
                        elif not tweet.author_name and not line.startswith("·"):
                            tweet.author_name = line

                    if not tweet.username and len(lines) >= 2:
                        # Fallback: second line often has @username
                        for line in lines:
                            at_match = re.search(r"@(\w+)", line)
                            if at_match:
                                tweet.username = at_match.group(1)
                                break
            except Exception:
                pass

            # ── Timestamp & URL ─────────────────────────────────────
            try:
                time_el = article.locator(sel.TWEET_TIMESTAMP).first
                if await time_el.count() > 0:
                    datetime_attr = await time_el.get_attribute("datetime")
                    if datetime_attr:
                        tweet.tweet_timestamp = datetime_attr
                        tweet.parsed_time = datetime.fromisoformat(
                            datetime_attr.replace("Z", "+00:00")
                        )
            except Exception:
                pass

            # Extract tweet URL from status link
            try:
                link_els = article.locator(sel.TWEET_LINK)
                link_count = await link_els.count()
                for li in range(link_count):
                    href = await link_els.nth(li).get_attribute("href")
                    if href and "/status/" in href:
                        tweet.tweet_url = f"https://x.com{href}" if href.startswith("/") else href
                        # Extract tweet ID from URL
                        id_match = re.search(r"/status/(\d+)", href)
                        if id_match:
                            tweet.tweet_id = id_match.group(1)
                        break
            except Exception:
                pass

            # ── Engagement Counts ───────────────────────────────────
            tweet.replies = await self._extract_count(article, sel.REPLY_COUNT_BUTTON)
            tweet.retweets = await self._extract_count(article, sel.RETWEET_COUNT_BUTTON)
            tweet.likes = await self._extract_count(article, sel.LIKE_COUNT_BUTTON)

            # ── Media Check ─────────────────────────────────────────
            try:
                media_count = await article.locator('[data-testid="tweetPhoto"], [data-testid="videoPlayer"]').count()
                if media_count > 0:
                    tweet.has_media = True
            except Exception:
                pass

            return tweet

        except Exception as e:
            logger.debug(f"Error parsing tweet element: {e}")
            return None

    async def _extract_count(self, article: Locator, selector: str) -> int:
        """
        Extract engagement count from a button element.

        The count is usually in the aria-label or as inner text.
        Handles K (thousands) and M (millions) suffixes.
        """
        try:
            btn = article.locator(selector).first
            if await btn.count() == 0:
                return 0

            # Try aria-label first (e.g., "1,234 Likes")
            aria = await btn.get_attribute("aria-label")
            if aria:
                num_match = re.search(r"([\d,]+)", aria)
                if num_match:
                    return int(num_match.group(1).replace(",", ""))

            # Fallback: inner text
            text = (await btn.inner_text()).strip()
            return self._parse_count_text(text)

        except Exception:
            return 0

    @staticmethod
    def _parse_count_text(text: str) -> int:
        """Parse count text like '1.2K', '45', '2.3M' into integer."""
        if not text:
            return 0

        text = text.strip().upper()

        try:
            if "M" in text:
                return int(float(text.replace("M", "").replace(",", "")) * 1_000_000)
            elif "K" in text:
                return int(float(text.replace("K", "").replace(",", "")) * 1_000)
            else:
                return int(text.replace(",", ""))
        except (ValueError, TypeError):
            return 0

    async def _scroll_down(self):
        """Scroll the page down to load more tweets."""
        try:
            scroll_distance = random.randint(400, 900)
            await self.page.mouse.wheel(0, scroll_distance)
        except Exception as e:
            logger.debug(f"Scroll error: {e}")

    @staticmethod
    async def _random_delay(min_sec: float = 1.0, max_sec: float = 3.0):
        """Human-like random delay."""
        delay = random.uniform(min_sec, max_sec)
        await asyncio.sleep(delay)
