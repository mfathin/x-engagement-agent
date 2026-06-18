"""
Browser Session Manager — persistent Chromium session for X/Twitter.

Handles:
- Launching Chromium with persistent context (cookies/state saved to disk)
- First-time manual login flow
- Session validation (detect if logged out)
- Alert to Telegram if session becomes invalid
"""

import asyncio
import logging
from pathlib import Path
from typing import Optional

from playwright.async_api import (
    BrowserContext,
    Page,
    Playwright,
    async_playwright,
)
from playwright_stealth import Stealth

from config.settings import Settings
from config import selectors as sel

logger = logging.getLogger(__name__)


class BrowserSessionManager:
    """
    Manages a persistent Chromium browser session for X/Twitter.

    The browser data (cookies, localStorage, cache) is stored in a
    persistent directory so the user only needs to log in once.
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self._playwright: Optional[Playwright] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None

    @property
    def page(self) -> Page:
        """Get the active browser page. Raises if not launched."""
        if self._page is None:
            raise RuntimeError("Browser not launched. Call launch() first.")
        return self._page

    @property
    def context(self) -> BrowserContext:
        """Get the browser context."""
        if self._context is None:
            raise RuntimeError("Browser not launched. Call launch() first.")
        return self._context

    async def launch(self) -> Page:
        """
        Launch Chromium with persistent context.

        The browser opens in headful mode (visible window) to reduce
        bot detection. All session data is saved to browser_data_dir.

        Returns:
            The active Page instance.
        """
        data_dir = self.settings.get_browser_data_path()
        logger.info(f"🌐 Launching browser (data dir: {data_dir})")

        self._playwright = await async_playwright().start()

        self._context = await self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(data_dir),
            channel="msedge",  # Use real Microsoft Edge to bypass bot detection on Windows
            headless=False,
            viewport={
                "width": self.settings.browser.viewport_width,
                "height": self.settings.browser.viewport_height,
            },
            # Anti-detection measures
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
            ignore_default_args=["--enable-automation"],
            # Realistic browser settings
            locale="en-US",
            timezone_id="Asia/Jakarta",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0"
            ),
            # Accept cookies & permissions
            permissions=["notifications"],
            color_scheme="dark",
        )

        # Inject auth_token if provided via .env
        auth_token = self.settings.twitter.auth_token
        if auth_token:
            logger.info("🔑 Injecting auth_token from .env to bypass login")
            await self._context.add_cookies([{
                'name': 'auth_token',
                'value': auth_token,
                'domain': '.x.com',
                'path': '/',
                'secure': True,
            }, {
                'name': 'auth_token',
                'value': auth_token,
                'domain': '.twitter.com',
                'path': '/',
                'secure': True,
            }])

        # Use the first page or create a new one
        if self._context.pages:
            self._page = self._context.pages[0]
        else:
            self._page = await self._context.new_page()

        # Apply stealth plugin to evade bot detection
        await Stealth().apply_stealth_async(self._page)

        # Remove webdriver property (anti-detection)
        await self._page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined,
            });
            // Remove automation-related properties
            delete window.cdc_adoQpoasnfa76pfcZLmcfl_Array;
            delete window.cdc_adoQpoasnfa76pfcZLmcfl_Promise;
            delete window.cdc_adoQpoasnfa76pfcZLmcfl_Symbol;
        """)

        logger.info("✅ Browser launched successfully")
        return self._page

    async def create_new_page(self) -> Page:
        """Create and return a new browser tab/page with stealth applied."""
        if not self._context:
            raise RuntimeError("Browser not launched.")
        
        new_page = await self._context.new_page()
        await Stealth().apply_stealth_async(new_page)
        
        await new_page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined,
            });
            delete window.cdc_adoQpoasnfa76pfcZLmcfl_Array;
            delete window.cdc_adoQpoasnfa76pfcZLmcfl_Promise;
            delete window.cdc_adoQpoasnfa76pfcZLmcfl_Symbol;
        """)
        return new_page

    async def is_session_valid(self) -> bool:
        """
        Check if the X/Twitter session is still valid (user is logged in).

        Navigates to x.com/home and checks for timeline elements.
        Returns True if logged in, False if redirected to login page.
        """
        try:
            page = self.page
            logger.info("🔍 Checking session validity...")

            await page.goto("https://x.com/home", wait_until="domcontentloaded")
            await asyncio.sleep(3)  # Wait for redirects

            current_url = page.url

            # Check if redirected to login
            if "/login" in current_url or "/i/flow/login" in current_url:
                logger.warning("❌ Session invalid — redirected to login page")
                return False

            # Check for logged-in indicators
            try:
                await page.wait_for_selector(
                    sel.LOGGED_IN_INDICATOR, timeout=10000
                )
                logger.info("✅ Session is valid — user is logged in")
                return True
            except Exception:
                # Try compose button as secondary check
                try:
                    await page.wait_for_selector(
                        sel.COMPOSE_TWEET_BUTTON, timeout=5000
                    )
                    logger.info("✅ Session is valid (compose button found)")
                    return True
                except Exception:
                    logger.warning("❌ Session invalid — no logged-in indicators found")
                    return False

        except Exception as e:
            logger.error(f"Error checking session: {e}")
            return False

    async def wait_for_manual_login(self, timeout_minutes: int = 10):
        """
        Navigate to X login page and wait for user to log in manually.

        Args:
            timeout_minutes: How long to wait for manual login.
        """
        page = self.page
        logger.info("🔐 Opening X login page for manual login...")

        await page.goto("https://x.com/i/flow/login", wait_until="domcontentloaded")

        print("\n" + "=" * 60)
        print("🔐 MANUAL LOGIN REQUIRED")
        print("=" * 60)
        print("Please log in to X/Twitter in the browser window.")
        print(f"Waiting up to {timeout_minutes} minutes...")
        print("=" * 60 + "\n")

        # Poll for successful login
        deadline = asyncio.get_event_loop().time() + (timeout_minutes * 60)

        while asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(5)

            current_url = page.url
            if "/home" in current_url:
                # Double-check with selector
                try:
                    await page.wait_for_selector(
                        sel.LOGGED_IN_INDICATOR, timeout=5000
                    )
                    logger.info("✅ Manual login successful!")
                    print("\n✅ Login successful! Session saved.\n")
                    return
                except Exception:
                    pass

        raise TimeoutError(
            f"Login timed out after {timeout_minutes} minutes. "
            "Please restart the tool and try again."
        )

    async def navigate_to_home(self):
        """Navigate to X home timeline."""
        page = self.page
        await page.goto("https://x.com/home", wait_until="domcontentloaded")
        await self._random_delay(2, 4)

    async def close(self):
        """Gracefully close the browser."""
        try:
            if self._context:
                await self._context.close()
                logger.info("🌐 Browser context closed")
            if self._playwright:
                await self._playwright.stop()
                logger.info("🌐 Playwright stopped")
        except Exception as e:
            logger.warning(f"Error closing browser: {e}")
        finally:
            self._page = None
            self._context = None
            self._playwright = None

    @staticmethod
    async def _random_delay(min_sec: float = 1.0, max_sec: float = 3.0):
        """Wait for a random duration to simulate human behavior."""
        import random
        delay = random.uniform(min_sec, max_sec)
        await asyncio.sleep(delay)
