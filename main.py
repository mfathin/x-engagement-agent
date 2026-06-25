"""
X Engagement Agent — Main Orchestrator

Ties together all components:
- Browser session management (Playwright)
- Tweet discovery (scraping)
- AI reply generation (Gemini + Ollama)
- Telegram approval bot
- Reply posting
- Rate limiting & safety

Scraping is controlled manually via Telegram commands:
- /scrape_on   — Start continuous scraping
- /scrape_off  — Pause scraping
- /scrape_once — Run one scrape cycle

No replies are ever posted without explicit manual approval.
"""

import asyncio
import logging
import random
import signal
import sys

# Force UTF-8 encoding for stdout/stderr to fix Windows console emoji issues
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

from datetime import datetime
from pathlib import Path

from config.settings import Settings
from db.models import init_db, set_db_path
from db import queries as db
from browser.session import BrowserSessionManager
from browser.scraper import TweetScraper
from browser.poster import ReplyPoster
from ai.generator import ReplyGenerator
from ai.trend_analyzer import TrendAnalyzer
from ai.skill_manager import SkillManager
from ai.post_mortem import PostMortemAnalyzer
from telegram_bot.bot import ApprovalBot

# ── Logging Setup ───────────────────────────────────────────────────


def setup_logging(settings: Settings):
    """Configure logging to both file and console."""
    log_dir = settings.get_log_dir()
    log_file = log_dir / f"agent_{datetime.now().strftime('%Y%m%d')}.log"

    # Create formatters
    file_formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)-25s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    console_formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%H:%M:%S",
    )

    # File handler
    file_handler = logging.FileHandler(str(log_file), encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(file_formatter)

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(
        getattr(logging, settings.logging.log_level.upper(), logging.INFO)
    )
    console_handler.setFormatter(console_formatter)

    # Root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)

    # Quiet noisy libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("telegram.ext").setLevel(logging.WARNING)
    logging.getLogger("playwright").setLevel(logging.WARNING)


logger = logging.getLogger(__name__)


# ── Main Agent ──────────────────────────────────────────────────────


class EngagementAgent:
    """
    Main orchestrator that coordinates all components.

    Lifecycle:
    1. Load config & init DB
    2. Launch browser, validate session
    3. Start Telegram bot
    4. Wait for /scrape_on or /scrape_once command
    5. Run discovery → AI → approval → posting cycle
    6. Graceful shutdown on Ctrl+C
    """

    def __init__(self):
        self.settings = Settings()
        self.browser: BrowserSessionManager | None = None
        self.scraper: TweetScraper | None = None
        self.poster: ReplyPoster | None = None
        self.ai: ReplyGenerator | None = None
        self.telegram_bot: ApprovalBot | None = None
        self.trend_analyzer: TrendAnalyzer | None = None
        self.skill_manager: SkillManager | None = None
        self.post_mortem: PostMortemAnalyzer | None = None
        self._shutdown_event = asyncio.Event()
        self._browser_lock = asyncio.Lock()
        # When set, other loops must pause (auto-post is using the browser)
        self._auto_post_busy = asyncio.Event()
        self.notif_browser_page = None
        self.notif_scraper: TweetScraper | None = None

    async def run(self):
        """Main entry point — run the agent."""

        # ── 1. Validate config ──────────────────────────────────────
        self.settings.check_and_exit_on_errors()
        setup_logging(self.settings)

        logger.info("=" * 60)
        logger.info("🚀 X Engagement Agent starting...")
        logger.info("=" * 60)

        # ── 2. Initialize database ──────────────────────────────────
        db_path = self.settings.project_root / "engagement.db"
        set_db_path(db_path)
        await init_db()
        logger.info(f"📦 Database initialized: {db_path}")
        await db.log_activity("agent_started", "Agent initialization complete")

        # ── 3. Launch browser ───────────────────────────────────────
        self.browser = BrowserSessionManager(self.settings)
        page = await self.browser.launch()

        # ── 4. Check/establish session ──────────────────────────────
        session_valid = await self.browser.is_session_valid()
        if not session_valid:
            logger.warning("Session not valid — manual login required")
            try:
                await self.browser.wait_for_manual_login(timeout_minutes=10)
            except TimeoutError as e:
                logger.error(str(e))
                await self.browser.close()
                sys.exit(1)
        else:
            logger.info("✅ Existing session is valid")

        # ── 5. Initialize components ────────────────────────────────
        self.scraper = TweetScraper(page, self.settings)
        self.poster = ReplyPoster(page, self.settings)
        self.ai = ReplyGenerator(self.settings)
        self.skill_manager = SkillManager()
        self.trend_analyzer = TrendAnalyzer(self.scraper, self.ai, self.skill_manager)
        self.post_mortem = PostMortemAnalyzer(self.scraper, self.ai, self.skill_manager)

        # AI health check
        ai_status = await self.ai.health_check()
        logger.info(f"🤖 AI providers: {ai_status}")

        # ── 6. Start Telegram bot ───────────────────────────────────
        self.telegram_bot = ApprovalBot(self.settings)
        self.telegram_bot.set_post_reply_callback(self._post_reply_locked)
        self.telegram_bot.set_scrape_once_callback(self._run_homepage_scrape_cycle)
        self.telegram_bot.set_auto_post_callbacks(
            post_callback=self._create_new_post_locked,
            regen_callback=self._generate_new_post_draft
        )
        await self.telegram_bot.start()
        await self.telegram_bot.send_status(
            "🚀 <b>Agent started!</b>\n\n"
            "🔴 Scraping: OFF (default)\n\n"
            "Kirim /scrape_on untuk mulai scraping\n"
            "atau /scrape_once untuk 1x cycle."
        )

        # ── 7. Initial Persona Learning ──────────────────────────────
        skills = self.skill_manager.get_skills()
        if "[PERSONA]" not in skills:
            logger.info("🧠 Initializing: No persona found in SKILLS.md. Learning from past replies...")
            await self.trend_analyzer.learn_persona_from_past_replies()

        # ── 8. Main loop ───────────────────────────────────────────
        logger.info("🔄 Entering main loop (scraping OFF by default)")
        logger.info("   Send /scrape_on in Telegram to start")

        try:
            await asyncio.gather(
                self._main_loop(),
                self._notifications_loop(),
                self._auto_post_loop(),
                self._trend_analysis_loop(),
                self._post_mortem_loop(),
                self._cleanup_loop(),
            )
        except asyncio.CancelledError:
            logger.info("Main loop cancelled")
        finally:
            await self._shutdown()

    async def _check_session_locked(self) -> bool:
        """Lock-protected session validation."""
        async with self._browser_lock:
            return await self.browser.is_session_valid()

    async def _main_loop(self):
        """
        Main scraping loop — waits for activation via Telegram.

        When active (continuous or single-shot), cycles through
        engagement queries, discovers tweets, generates AI drafts,
        and sends them to Telegram for approval.
        """
        while not self._shutdown_event.is_set():
            try:
                # Wait for scraping to be activated
                bot = self.telegram_bot
                is_continuous = bot.is_scraping_active()
                is_once = bot.is_scrape_once_requested()

                if not is_continuous and not is_once:
                    # Sleep briefly and re-check
                    await asyncio.sleep(2)
                    continue

                # ── Check active hours ──────────────────────────────
                if not self._is_active_hours() and not is_once:
                    logger.info(
                        "💤 Outside active hours "
                        f"({self.settings.rate_limit.active_hours_start}:00 - "
                        f"{self.settings.rate_limit.active_hours_end}:00). "
                        "Sleeping..."
                    )
                    await asyncio.sleep(60)
                    continue

                # ── Pause if auto-post is busy ──────────────────────
                if self._auto_post_busy.is_set():
                    logger.debug("💤 Main loop paused — auto-post in progress")
                    await asyncio.sleep(3)
                    continue

                # ── Validate session periodically ───────────────────
                async with self._browser_lock:
                    session_valid = await self.browser.is_session_valid()
                if not session_valid:
                    logger.error("❌ Session invalid!")
                    await self.telegram_bot.send_alert(
                        "❌ X session invalid! Login ulang diperlukan.\n"
                        "Buka browser dan login manual, lalu kirim /scrape_on."
                    )
                    bot.scraping_active.clear()
                    bot.clear_scrape_once()
                    await db.log_activity(
                        "session_invalid", "Session expired, scraping paused"
                    )
                    continue

                # ── Run one scrape cycle ────────────────────────────
                await self._run_homepage_scrape_cycle()

                if is_once and not is_continuous:
                    bot.clear_scrape_once()
                    logger.info("🔂 Single scrape cycle complete, pausing")
                    await self.telegram_bot.send_status(
                        "🔂 <b>Single scrape cycle selesai.</b>\n"
                        "Scraping kembali ke mode pause."
                    )

                # ── Random delay before next cycle ──────────────────
                if is_continuous:
                    delay = random.randint(120, 300)  # 2-5 minutes
                    logger.info(
                        f"⏰ Next cycle in {delay // 60}m {delay % 60}s"
                    )
                    await db.log_activity(
                        "cycle_complete",
                        f"Next cycle in {delay}s"
                    )

                    # Interruptible sleep (can be woken by scrape_off)
                    for _ in range(delay):
                        if not bot.is_scraping_active():
                            break
                        if self._shutdown_event.is_set():
                            return
                        await asyncio.sleep(1)

            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Error in main loop: {type(e).__name__}: {e}")
                await db.log_activity("error", f"Main loop: {e}")
                await self.telegram_bot.send_alert(f"Error in main loop: {e}")
                await asyncio.sleep(30)

    async def _notifications_loop(self):
        """Dedicated background loop for monitoring notifications instantly."""
        if not self.settings.engagement.notifications_enabled:
            logger.info("🔔 Notifications checking is DISABLED in settings.")
            return

        logger.info("🔔 Notifications loop starting...")
        
        # We delay initialization of the second tab until needed to ensure main page is ready
        await asyncio.sleep(10)
        
        while not self._shutdown_event.is_set():
            try:
                bot = self.telegram_bot
                
                # Use bot's notification active flag
                if not bot.is_notif_active():
                    await asyncio.sleep(5)
                    continue

                # Pause if auto-post is in progress
                if self._auto_post_busy.is_set():
                    logger.debug("🔔 Notifications paused — auto-post in progress")
                    await asyncio.sleep(3)
                    continue

                if not await self.browser.is_session_valid():
                    await asyncio.sleep(10)
                    continue

                # Initialize notif tab if not done yet
                if not self.notif_browser_page:
                    logger.info("🔔 Creating dedicated browser tab for notifications")
                    self.notif_browser_page = await self.browser.create_new_page()
                    self.notif_scraper = TweetScraper(self.notif_browser_page, self.settings)

                # Check notifications
                async with self._browser_lock:
                    tweets = await self.notif_scraper.discover_notification_tweets()
                
                if tweets:
                    for tweet in tweets:
                        # Re-check rate limit per tweet
                        replies_hour = await db.get_replies_last_hour()
                        if replies_hour >= self.settings.rate_limit.max_replies_per_hour:
                            break
                        
                        await self._process_tweet(tweet, "Notification", auto_reply=True)
                        await asyncio.sleep(random.uniform(5, 10))

                # Sleep based on interval
                interval = self.settings.engagement.notifications_check_interval
                logger.debug(f"⏰ Notifications loop sleeping for {interval}s")
                for _ in range(interval):
                    if not bot.is_notif_active():
                        break
                    if self._shutdown_event.is_set():
                        return
                    await asyncio.sleep(1)

            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Error in notifications loop: {e}")
                await asyncio.sleep(30)

    async def _auto_post_loop(self):
        """Background loop to generate and post standalone tweets every interval."""
        if not self.settings.engagement.auto_post_enabled:
            logger.info("🤖 Auto-post is DISABLED in settings.")
            return

        interval_mins = self.settings.engagement.auto_post_interval_minutes
        logger.info(f"🤖 Auto-post loop starting... Interval: {interval_mins} mins")
        
        # Wait a bit before first auto-post to let other loops initialize
        logger.info("⏳ Waiting before first auto-post...")
        for _ in range(180): # Wait up to 3 minutes
            if self._shutdown_event.is_set():
                break
            await asyncio.sleep(1)
        
        while not self._shutdown_event.is_set():
            try:
                if not await self._check_session_locked():
                    await asyncio.sleep(10)
                    continue

                # 1. Generate a new post draft (using skills + trends)
                logger.info("📝 Generating new auto-post draft (Autonomous Mode)...")
                draft = await self._generate_new_post_draft()
                if draft:
                    # 2. AUTONOMOUS: Post directly to X, bypassing Telegram
                    logger.info("🤖 Autonomously posting to X...")
                    success, post_url = await self._create_new_post_locked(draft)
                    
                    if success:
                        await db.save_auto_post(draft, post_url)
                        await db.log_activity("auto_post_published", f"URL: {post_url}")
                        # Inform Telegram that we posted (FYI only)
                        await self.telegram_bot.send_status(
                            f"🤖 <b>Autonomous Auto-Post Published!</b>\n\n"
                            f"🔗 <a href='{post_url}'>Lihat Post</a>\n"
                            f"💬 <i>\"{draft}\"</i>"
                        )
                    else:
                        logger.error("Failed to publish autonomous post.")
                else:
                    logger.error("Failed to generate auto-post draft")

                # 3. Wait for interval
                logger.info(f"⏰ Auto-post loop sleeping for {interval_mins} minutes")
                
                # Sleep in smaller chunks to be responsive to shutdown
                for _ in range(interval_mins * 60):
                    if self._shutdown_event.is_set():
                        return
                    await asyncio.sleep(1)

            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Error in auto-post loop: {e}")
                await asyncio.sleep(60)

    async def _trend_analysis_loop(self):
        """Background loop to periodically analyze X trends to enrich auto-posts."""
        if not self.settings.engagement.auto_post_enabled:
            return

        interval_hours = 6
        logger.info(f"📈 Trend analysis loop starting... Interval: {interval_hours} hours")

        while not self._shutdown_event.is_set():
            try:
                if not await self._check_session_locked():
                    await asyncio.sleep(10)
                    continue

                logger.info("🔍 Running scheduled trend analysis...")
                async with self._browser_lock:
                    # Learn niche from timeline
                    await self.trend_analyzer.learn_from_timeline()

                # Wait for interval
                logger.info(f"⏰ Trend analysis loop sleeping for {interval_hours} hours")
                
                # Sleep in smaller chunks to be responsive to shutdown
                for _ in range(interval_hours * 3600):
                    if self._shutdown_event.is_set():
                        return
                    await asyncio.sleep(1)

            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Error in trend analysis loop: {e}")
                await asyncio.sleep(60)

    async def _post_mortem_loop(self):
        """Background loop to periodically analyze past auto-posts for learnings."""
        if not self.settings.engagement.auto_post_enabled:
            return

        interval_hours = 4
        logger.info(f"🔬 Post-mortem loop starting... Interval: {interval_hours} hours")

        while not self._shutdown_event.is_set():
            try:
                if not await self._check_session_locked():
                    await asyncio.sleep(10)
                    continue

                async with self._browser_lock:
                    await self.post_mortem.run_post_mortem()

                logger.info(f"⏰ Post-mortem loop sleeping for {interval_hours} hours")
                
                for _ in range(interval_hours * 3600):
                    if self._shutdown_event.is_set():
                        return
                    await asyncio.sleep(1)

            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Error in post-mortem loop: {e}")
                await asyncio.sleep(60)

    async def _cleanup_loop(self):
        """Background loop to periodically delete records older than 2 days to free space."""
        interval_hours = 24
        logger.info(f"🧹 Cleanup loop starting... Interval: {interval_hours} hours")

        while not self._shutdown_event.is_set():
            try:
                # Wait 5 minutes before the first cleanup to allow agent to start properly
                await asyncio.sleep(300)
                
                logger.info("🧹 Running database cleanup for records older than 2 days...")
                counts = await db.cleanup_old_data(days=2)
                
                total_deleted = sum(counts.values())
                if total_deleted > 0:
                    logger.info(f"🧹 Cleanup complete. Deleted {total_deleted} old records: {counts}")
                    await db.log_activity("cleanup", f"Deleted {total_deleted} old records: {counts}")
                else:
                    logger.debug("🧹 Cleanup complete: No old records found.")

                logger.info(f"⏰ Cleanup loop sleeping for {interval_hours} hours")
                
                for _ in range(interval_hours * 3600):
                    if self._shutdown_event.is_set():
                        return
                    await asyncio.sleep(1)

            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Error in cleanup loop: {e}")
                await asyncio.sleep(60)

    async def _run_homepage_scrape_cycle(self):
        """Run one scrape cycle on the homepage timeline."""
        logger.info("🔄 Starting homepage scrape cycle")

        # Discover tweets
        try:
            tweets = await self.scraper.discover_timeline_tweets()
        except Exception as e:
            logger.error(f"Error discovering timeline tweets: {e}")
            return

        if not tweets:
            logger.info("📭 No qualifying tweets found on timeline")
            return

        # Limit to 10 tweets
        tweets = tweets[:10]
        logger.info(f"📬 Found {len(tweets)} tweets on timeline to auto-reply")

        # Process each tweet
        for tweet in tweets:
            # Check rate limit
            replies_hour = await db.get_replies_last_hour()
            if replies_hour >= self.settings.rate_limit.max_replies_per_hour:
                logger.info("⏸️ Rate limit reached")
                break

            await self._process_tweet(tweet, "Timeline", auto_reply=True)
            await asyncio.sleep(random.uniform(5, 15))

    async def _run_scrape_cycle(self):
        """Run one full scrape cycle through all engagement queries."""
        queries = self.settings.engagement.queries
        logger.info(f"🔄 Starting scrape cycle ({len(queries)} queries)")

        for query in queries:
            # Check if we should stop mid-cycle
            if not self.telegram_bot.is_scraping_active() and \
               not self.telegram_bot.is_scrape_once_requested():
                logger.info("⏸️ Scraping paused mid-cycle")
                break

            # Check rate limit
            replies_hour = await db.get_replies_last_hour()
            if replies_hour >= self.settings.rate_limit.max_replies_per_hour:
                logger.info(
                    f"⏸️ Rate limit reached ({replies_hour}/"
                    f"{self.settings.rate_limit.max_replies_per_hour} per hour)"
                )
                await self.telegram_bot.send_status(
                    f"⏸️ Rate limit: {replies_hour}/"
                    f"{self.settings.rate_limit.max_replies_per_hour} replies/hour.\n"
                    "Menunggu cooldown..."
                )
                break

            # Discover tweets
            try:
                async with self._browser_lock:
                    tweets = await self.scraper.discover_tweets(query)
            except Exception as e:
                logger.error(f"Error discovering tweets for '{query}': {e}")
                continue

            if not tweets:
                logger.info(f"📭 No qualifying tweets found for '{query}'")
                continue

            logger.info(f"📬 Found {len(tweets)} tweets for '{query}'")

            # Process each tweet
            for tweet in tweets:
                # Re-check rate limit per tweet
                replies_hour = await db.get_replies_last_hour()
                if replies_hour >= self.settings.rate_limit.max_replies_per_hour:
                    break

                await self._process_tweet(tweet, query)

                # Random delay between tweets
                await asyncio.sleep(random.uniform(5, 15))

            # Random delay between queries
            delay = random.randint(30, 120)
            logger.info(f"⏰ Delay before next query: {delay}s")
            await asyncio.sleep(delay)

    async def _process_tweet(self, tweet, query: str, auto_reply: bool = False):
        """Process a single discovered tweet: save, generate drafts, send for approval or auto-reply."""
        try:
            # Save tweet to DB
            await db.save_tweet(tweet.to_dict())
            await db.log_activity(
                "tweet_discovered",
                f"@{tweet.username}: {tweet.tweet_text[:60]}...",
            )

            # Generate AI reply drafts
            drafts, provider = await self.ai.generate(
                tweet.to_dict(),
                topic=query,
                language="id",
                skills_context=self.skill_manager.get_skills(),
            )

            if not drafts:
                logger.warning(
                    f"No drafts generated for tweet {tweet.tweet_id}"
                )
                await db.update_tweet_status(tweet.tweet_id, "no_drafts")
                return

            # Save drafts to DB
            reply_id = await db.save_reply_draft(
                tweet.tweet_id, drafts, ai_provider=provider
            )
            if auto_reply:
                # Auto-approve the first draft and post it directly
                selected_draft = drafts[0]
                logger.info(f"⚡ Auto-replying with Draft 1: {selected_draft}")
                
                await db.update_reply_status(
                    reply_id, "approved", selected_draft=1, final_text=selected_draft
                )
                await db.update_tweet_status(tweet.tweet_id, "approved")
                
                tweet_url = tweet.tweet_url or f"https://x.com/{tweet.username}/status/{tweet.tweet_id}"
                success = await self._post_reply_locked(tweet_url, selected_draft)
                
                if success:
                    await db.update_reply_status(reply_id, "posted")
                    await db.update_tweet_status(tweet.tweet_id, "replied")
                    await db.log_activity("auto_reply_posted", f"reply_id={reply_id}")
                    # Use telegram bot's truncate method directly
                    truncated_text = selected_draft if len(selected_draft) <= 200 else selected_draft[:197] + "..."
                    await self.telegram_bot.send_status(
                        f"⚡ <b>Auto-Replied to Notification!</b>\n\n"
                        f"🔗 <a href=\"{tweet_url}\">Tweet Link</a>\n"
                        f"💬 <i>\"{truncated_text}\"</i>"
                    )
                else:
                    await db.update_reply_status(reply_id, "failed")
                    await db.update_tweet_status(tweet.tweet_id, "failed")
                    await self.telegram_bot.send_alert(f"❌ Auto-reply failed for {tweet_url}")
            else:
                await db.update_tweet_status(tweet.tweet_id, "drafts_sent")
                # Send to Telegram for approval
                await self.telegram_bot.send_approval_request(
                    tweet.to_dict(), drafts, reply_id
                )
    
                logger.info(
                    f"📤 Sent to Telegram: @{tweet.username} "
                    f"(reply_id={reply_id}, provider={provider})"
                )

        except Exception as e:
            logger.error(f"Error processing tweet {tweet.tweet_id}: {e}")

    async def _post_reply_locked(self, tweet_url: str, reply_text: str) -> bool:
        """Lock-protected wrapper for posting a reply."""
        async with self._browser_lock:
            return await self._post_reply(tweet_url, reply_text)

    async def _post_reply(self, tweet_url: str, reply_text: str) -> bool:
        """Callback for Telegram bot — posts reply via browser automation."""
        if not self.poster:
            logger.error("Reply poster not initialized")
            return False

        logger.info(f"📝 Posting approved reply to: {tweet_url}")
        return await self.poster.post_reply(tweet_url, reply_text)

    async def _create_new_post_locked(self, post_text: str) -> tuple[bool, str]:
        """Lock-protected wrapper for creating a new post. Pauses other loops."""
        self._auto_post_busy.set()
        try:
            await asyncio.sleep(2)
            async with self._browser_lock:
                if not self.poster:
                    return False, ""
                return await self.poster.create_new_post(post_text)
        finally:
            self._auto_post_busy.clear()

    async def _generate_new_post_draft(self) -> str:
        """Callback to generate a new post draft using trends and SKILLS.md."""
        skills = self.skill_manager.get_skills()
        combined_context = (
            f"--- KEAHLIAN & NICHE YANG DIPELAJARI (SKILLS.md) ---\n"
            f"{skills}\n\n"
        )
        draft, _ = await self.ai.generate_post(trend_context="", skills_context=combined_context)
        return draft

    def _is_active_hours(self) -> bool:
        """Check if current time is within active hours."""
        current_hour = datetime.now().hour
        start = self.settings.rate_limit.active_hours_start
        end = self.settings.rate_limit.active_hours_end

        if start <= end:
            return start <= current_hour < end
        else:
            # Wraps around midnight (e.g., 22-06)
            return current_hour >= start or current_hour < end

    async def _shutdown(self):
        """Graceful shutdown of all components."""
        logger.info("🛑 Shutting down...")

        if self.telegram_bot:
            try:
                await self.telegram_bot.send_status("🛑 <b>Agent shutting down.</b>")
            except Exception:
                pass
            await self.telegram_bot.stop()

        if self.browser:
            await self.browser.close()

        await db.log_activity("agent_stopped", "Graceful shutdown")
        logger.info("👋 Goodbye!")


# ── Entry Point ─────────────────────────────────────────────────────


def main():
    """Entry point — runs the agent."""
    agent = EngagementAgent()

    # Handle Ctrl+C gracefully
    def handle_signal(sig, frame):
        print("\n⚠️  Ctrl+C detected — shutting down gracefully...")
        agent._shutdown_event.set()

    signal.signal(signal.SIGINT, handle_signal)

    try:
        asyncio.run(agent.run())
    except KeyboardInterrupt:
        print("\n👋 Agent stopped.")


if __name__ == "__main__":
    main()
