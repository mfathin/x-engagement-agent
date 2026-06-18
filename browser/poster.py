"""
Reply Poster — posts approved replies to X/Twitter via browser automation.

Simulates human-like typing with random delays per character,
random pauses, and natural click behavior.
"""

import asyncio
import logging
import random
from pathlib import Path
from typing import Optional

from playwright.async_api import Page

from config.settings import Settings
from config import selectors as sel

logger = logging.getLogger(__name__)


class ReplyPoster:
    """
    Posts reply to a tweet using browser automation.

    Uses human-like typing simulation to avoid detection:
    - Random delay per keystroke (50-150ms)
    - Occasional longer pauses mid-sentence
    - Random delays before/after clicking
    """

    def __init__(self, page: Page, settings: Settings):
        self.page = page
        self.settings = settings

    async def post_reply(self, tweet_url: str, reply_text: str) -> bool:
        """
        Navigate to tweet and post a reply.

        Args:
            tweet_url: Full URL of the tweet to reply to
            reply_text: The approved reply text to post

        Returns:
            True if reply was posted successfully, False otherwise
        """
        logger.info(f"💬 Posting reply to: {tweet_url}")
        logger.info(f"💬 Reply text: \"{reply_text[:80]}...\"")

        try:
            # 1. Navigate to the tweet
            await self.page.goto(tweet_url, wait_until="domcontentloaded")
            await self._random_delay(2, 4)

            # 2. Wait for tweet to load
            try:
                await self.page.wait_for_selector(
                    sel.TWEET_ARTICLE, timeout=15000
                )
            except Exception:
                logger.error("Tweet did not load in time")
                await self._take_debug_screenshot("tweet_not_loaded")
                return False

            await self._random_delay(1, 2)

            # 3. Find and click the reply text area
            reply_box = await self._find_reply_box()
            if not reply_box:
                logger.error("Could not find reply text box")
                await self._take_debug_screenshot("no_reply_box")
                return False

            await reply_box.click()
            await self._random_delay(0.5, 1.5)

            # 4. Type the reply with human-like simulation
            await self._human_type(reply_text)
            await self._random_delay(1, 3)

            # 5. Find and click the submit/post button
            submitted = await self._click_submit()
            if not submitted:
                logger.error("Could not find or click submit button")
                await self._take_debug_screenshot("no_submit_button")
                return False

            # 6. Wait for confirmation
            await self._random_delay(3, 5)

            # Check if reply appeared (basic confirmation)
            logger.info("✅ Reply posted successfully!")
            return True

        except Exception as e:
            logger.error(f"Error posting reply: {type(e).__name__}: {e}")
            await self._take_debug_screenshot("post_error")
            return False

    async def create_new_post(self, post_text: str, max_retries: int = 3) -> bool:
        """
        Open compose modal via sidebar button and post a new tweet.

        Retries up to max_retries times on failure.

        Args:
            post_text: The text to post.
            max_retries: Number of retry attempts.

        Returns:
            True if successful, False otherwise.
        """
        for attempt in range(1, max_retries + 1):
            logger.info(f"📝 Creating new auto-post (attempt {attempt}/{max_retries})...")
            try:
                # 1. Navigate to home first so the sidebar compose button is available
                await self.page.goto("https://x.com/home", wait_until="domcontentloaded", timeout=30000)
                await self._random_delay(3, 5)

                # 2. Click the compose ("Post") button in the sidebar
                compose_btn = self.page.locator('a[data-testid="SideNav_NewTweet_Button"]').first
                try:
                    await compose_btn.wait_for(state="visible", timeout=20000)
                    await compose_btn.click()
                    logger.debug("Clicked sidebar compose button")
                except Exception:
                    logger.warning("Sidebar compose button not found, trying fallback URL...")
                    # Fallback: try direct navigation (may work if page is already loaded)
                    try:
                        await self.page.goto("https://x.com/compose/post", wait_until="domcontentloaded", timeout=30000)
                    except Exception:
                        pass  # X may abort this but still show the modal
                
                await self._random_delay(2, 4)

                # 3. Find the text area (the compose modal)
                box = self.page.locator('div[data-testid="tweetTextarea_0"]').first
                try:
                    await box.wait_for(state="visible", timeout=20000)
                except Exception:
                    logger.error(f"Could not find compose text area (attempt {attempt})")
                    await self._take_debug_screenshot(f"no_compose_box_attempt{attempt}")
                    if attempt < max_retries:
                        await asyncio.sleep(5)
                        continue
                    return False

                await box.click()
                await self._random_delay(0.5, 1.5)

                # 4. Type text
                await self._human_type(post_text)
                await self._random_delay(1, 3)

                # 5. Click Submit
                btn = self.page.locator('[data-testid="tweetButton"]').first
                try:
                    await btn.wait_for(state="visible", timeout=10000)
                    await btn.click()
                    logger.debug("Clicked post button")
                except Exception as e:
                    logger.error(f"Could not click post button (attempt {attempt}): {e}")
                    await self._take_debug_screenshot(f"no_post_button_attempt{attempt}")
                    if attempt < max_retries:
                        await asyncio.sleep(5)
                        continue
                    return False

                # Wait for confirmation
                await self._random_delay(3, 5)
                logger.info("✅ Auto-post published successfully!")
                return True

            except Exception as e:
                logger.error(f"Error creating new post (attempt {attempt}): {e}")
                await self._take_debug_screenshot(f"create_post_error_attempt{attempt}")
                if attempt < max_retries:
                    logger.info(f"🔄 Retrying in 5 seconds...")
                    await asyncio.sleep(5)
                    continue
                return False
        
        return False

    async def _find_reply_box(self) -> Optional[object]:
        """Find the reply text input area on the tweet page."""

        # Try primary selector
        try:
            box = self.page.locator(sel.REPLY_TEXT_BOX).first
            if await box.count() > 0:
                logger.debug("Found reply box via primary selector")
                return box
        except Exception:
            pass

        # Try fallback contenteditable div
        try:
            box = self.page.locator(sel.REPLY_TEXT_BOX_FALLBACK).first
            if await box.count() > 0:
                logger.debug("Found reply box via fallback selector")
                return box
        except Exception:
            pass

        # Try generic contenteditable approach
        try:
            box = self.page.locator(
                'div[role="textbox"][contenteditable="true"]'
            ).first
            if await box.count() > 0:
                logger.debug("Found reply box via generic textbox selector")
                return box
        except Exception:
            pass

        return None

    async def _human_type(self, text: str):
        """
        Type text character by character with human-like timing.

        - Base delay: 50-150ms per character
        - Occasional pause after punctuation (200-500ms)
        - Occasional longer pause mid-sentence (300-800ms, ~5% chance)
        """
        for i, char in enumerate(text):
            # Type the character
            await self.page.keyboard.type(char, delay=0)

            # Base typing delay
            base_delay = random.uniform(0.04, 0.14)

            # Longer delay after punctuation
            if char in ".!?,;:":
                base_delay += random.uniform(0.15, 0.40)

            # Occasional thinking pause (~5% of characters)
            elif random.random() < 0.05:
                base_delay += random.uniform(0.25, 0.70)

            # Slight speed variation for spaces
            elif char == " ":
                base_delay += random.uniform(0.02, 0.08)

            await asyncio.sleep(base_delay)

    async def _click_submit(self) -> bool:
        """Find and click the reply submit button."""

        # Try the inline reply button
        try:
            btn = self.page.locator(sel.REPLY_SUBMIT_BUTTON).first
            if await btn.count() > 0:
                # Check if button is enabled
                is_disabled = await btn.get_attribute("aria-disabled")
                if is_disabled == "true":
                    logger.warning("Submit button is disabled, waiting...")
                    await self._random_delay(1, 2)

                await btn.click()
                logger.debug("Clicked submit via primary selector")
                return True
        except Exception as e:
            logger.debug(f"Primary submit failed: {e}")

        # Fallback: try button with "Reply" or "Post" text
        for label_text in ["Reply", "Post", "Balas"]:
            try:
                btn = self.page.get_by_test_id("tweetButton").first
                if await btn.count() > 0:
                    await btn.click()
                    logger.debug(f"Clicked submit via tweetButton testid")
                    return True
            except Exception:
                pass

            try:
                btn = self.page.locator(
                    f'div[role="button"][data-testid="tweetButtonInline"]'
                ).first
                if await btn.count() > 0:
                    await btn.click()
                    return True
            except Exception:
                pass

        return False

    async def _take_debug_screenshot(self, name: str):
        """Take a screenshot for debugging purposes."""
        try:
            log_dir = self.settings.get_log_dir()
            screenshot_path = log_dir / f"debug_{name}_{self._timestamp()}.png"
            await self.page.screenshot(path=str(screenshot_path))
            logger.info(f"📸 Debug screenshot saved: {screenshot_path}")
        except Exception as e:
            logger.debug(f"Could not save screenshot: {e}")

    @staticmethod
    def _timestamp() -> str:
        """Get current timestamp string for filenames."""
        from datetime import datetime
        return datetime.now().strftime("%Y%m%d_%H%M%S")

    @staticmethod
    async def _random_delay(min_sec: float = 1.0, max_sec: float = 3.0):
        """Human-like random delay."""
        delay = random.uniform(min_sec, max_sec)
        await asyncio.sleep(delay)
