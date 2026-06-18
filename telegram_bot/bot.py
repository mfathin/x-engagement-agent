"""
Telegram Approval Bot — sends tweet drafts for manual approval.

Workflow:
1. Receives tweet + AI-generated drafts from the orchestrator
2. Sends formatted message with inline keyboard to the owner
3. Handles callback: Approve Draft 1/2/3, Edit Manual, Skip
4. For "Edit Manual": enters conversation state waiting for user text
5. On approval: signals the browser module to post the reply
6. Sends confirmation back after posting

Scraping Control:
- /scrape_on   — Start continuous scraping loop
- /scrape_off  — Pause scraping (keeps browser open)
- /scrape_once — Run a single scrape cycle then pause
- Default state: OFF (user must manually start scraping)
"""

import asyncio
import logging
from typing import Any, Callable, Coroutine, Dict, List, Optional

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from config.settings import Settings
from db import queries as db

logger = logging.getLogger(__name__)

# Conversation states
WAITING_MANUAL_REPLY = 1


class ApprovalBot:
    """
    Telegram bot for tweet reply approval workflow.

    The bot sends AI-generated reply drafts and waits for manual approval
    before any reply is posted to X.
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self.chat_id = settings.telegram.chat_id
        self._app: Optional[Application] = None
        # Callback to post reply: async func(tweet_url, reply_text) -> bool
        self._post_reply_callback: Optional[
            Callable[[str, str], Coroutine[Any, Any, bool]]
        ] = None
        # Callback for single scrape: async func() -> None
        self._scrape_once_callback: Optional[
            Callable[[], Coroutine[Any, Any, None]]
        ] = None
        # Callback for auto post: async func(post_text) -> bool
        self._auto_post_callback: Optional[
            Callable[[str], Coroutine[Any, Any, bool]]
        ] = None
        # Callback to regenerate auto post: async func() -> str
        self._regenerate_post_callback: Optional[
            Callable[[], Coroutine[Any, Any, str]]
        ] = None
        # Store pending edit sessions: {reply_id: tweet_data}
        self._pending_edits: Dict[int, Dict[str, Any]] = {}
        # Store pending auto post draft
        self._pending_auto_post: str = ""

        # ── Scraping control ────────────────────────────────────────
        # Default: NOT set (scraping off — user must /scrape_on first)
        self.scraping_active = asyncio.Event()
        # Flag for single-shot scraping
        self._scrape_once_requested = asyncio.Event()

        # Flag for notification scraping
        self.notif_active = asyncio.Event()
        # Auto-enable if configured in settings
        if settings.engagement.notifications_enabled:
            self.notif_active.set()

    def set_post_reply_callback(
        self, callback: Callable[[str, str], Coroutine[Any, Any, bool]]
    ):
        """Set the callback function that posts replies via browser automation."""
        self._post_reply_callback = callback

    def set_scrape_once_callback(
        self, callback: Callable[[], Coroutine[Any, Any, None]]
    ):
        """Set the callback that triggers a single scrape cycle."""
        self._scrape_once_callback = callback

    def set_auto_post_callbacks(
        self, 
        post_callback: Callable[[str], Coroutine[Any, Any, bool]],
        regen_callback: Callable[[], Coroutine[Any, Any, str]]
    ):
        """Set callbacks for posting and regenerating auto-posts."""
        self._auto_post_callback = post_callback
        self._regenerate_post_callback = regen_callback

    def is_scraping_active(self) -> bool:
        """Check if continuous scraping is currently enabled."""
        return self.scraping_active.is_set()

    def is_scrape_once_requested(self) -> bool:
        """Check if a single scrape cycle was requested."""
        return self._scrape_once_requested.is_set()

    def clear_scrape_once(self):
        """Clear the single-scrape flag after it's been executed."""
        self._scrape_once_requested.clear()

    def is_notif_active(self) -> bool:
        """Check if notification monitoring is enabled."""
        return self.notif_active.is_set()

    async def start(self):
        """Initialize and start the Telegram bot with polling."""
        self._app = (
            Application.builder()
            .token(self.settings.telegram.bot_token)
            .build()
        )

        # Conversation handler for Edit Manual flow
        conv_handler = ConversationHandler(
            entry_points=[
                CallbackQueryHandler(
                    self._handle_callback, pattern=r"^(approve|edit|skip|autopost)_"
                )
            ],
            states={
                WAITING_MANUAL_REPLY: [
                    MessageHandler(
                        filters.TEXT & ~filters.COMMAND,
                        self._handle_manual_reply,
                    )
                ],
            },
            fallbacks=[CommandHandler("cancel", self._handle_cancel)],
            per_message=False,
        )

        # Register handlers
        self._app.add_handler(CommandHandler("start", self._handle_start))
        self._app.add_handler(CommandHandler("status", self._handle_status))
        self._app.add_handler(CommandHandler("scrape_on", self._handle_scrape_on))
        self._app.add_handler(CommandHandler("scrape_off", self._handle_scrape_off))
        self._app.add_handler(CommandHandler("scrape_once", self._handle_scrape_once))
        self._app.add_handler(CommandHandler("notif_on", self._handle_notif_on))
        self._app.add_handler(CommandHandler("notif_off", self._handle_notif_off))
        self._app.add_handler(CommandHandler("post", self._handle_post))
        self._app.add_handler(conv_handler)

        # Initialize and start polling (non-blocking)
        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling(drop_pending_updates=True)

        # Register command list with Telegram (shows in "/" menu)
        from telegram import BotCommand
        await self._app.bot.set_my_commands([
            BotCommand("start", "Tampilkan info dan daftar perintah"),
            BotCommand("status", "Cek status agent saat ini"),
            BotCommand("scrape_on", "Mulai scraping otomatis"),
            BotCommand("scrape_off", "Pause scraping"),
            BotCommand("scrape_once", "Jalankan 1x scrape cycle"),
            BotCommand("notif_on", "Mulai monitor notifikasi"),
            BotCommand("notif_off", "Pause monitor notifikasi"),
            BotCommand("post", "Generate & kirim auto-post sekarang"),
            BotCommand("cancel", "Batalkan edit yang sedang berlangsung"),
        ])

        logger.info("🤖 Telegram bot started (polling mode)")

    async def stop(self):
        """Gracefully stop the Telegram bot."""
        if self._app:
            try:
                await self._app.updater.stop()
                await self._app.stop()
                await self._app.shutdown()
                logger.info("🤖 Telegram bot stopped")
            except Exception as e:
                logger.warning(f"Error stopping Telegram bot: {e}")

    async def test_connection(self) -> bool:
        """Test if the bot token is valid and can send messages."""
        try:
            from telegram import Bot

            bot = Bot(token=self.settings.telegram.bot_token)
            me = await bot.get_me()
            logger.info(f"✅ Telegram bot connected: @{me.username}")
            return True
        except Exception as e:
            logger.error(f"❌ Telegram bot connection failed: {e}")
            return False

    # ── Send Approval Request ───────────────────────────────────────

    async def send_approval_request(
        self,
        tweet_data: Dict[str, Any],
        drafts: List[str],
        reply_id: int,
    ):
        """
        Send an approval request message with inline keyboard.

        Args:
            tweet_data: Dict with tweet info (tweet_text, username, etc.)
            drafts: List of AI-generated reply options (1-3)
            reply_id: Database reply_history row ID
        """
        # Format the message
        username = tweet_data.get("username", "unknown")
        author_name = tweet_data.get("author_name", username)
        tweet_text = tweet_data.get("tweet_text", "")
        tweet_url = tweet_data.get("tweet_url", "")
        likes = tweet_data.get("likes", 0)
        replies_count = tweet_data.get("replies", 0)
        retweets = tweet_data.get("retweets", 0)

        # Format engagement numbers
        def fmt_num(n: int) -> str:
            if n >= 1_000_000:
                return f"{n / 1_000_000:.1f}M"
            elif n >= 1_000:
                return f"{n / 1_000:.1f}K"
            return str(n)

        message = (
            f"🔔 <b>New Tweet Found!</b>\n\n"
            f"👤 <b>@{username}</b> ({author_name})\n"
            f"📊 ❤️ {fmt_num(likes)}  💬 {fmt_num(replies_count)}  🔄 {fmt_num(retweets)}\n"
            f"🔗 <a href=\"{tweet_url}\">Open Tweet</a>\n\n"
            f"📝 <b>Tweet:</b>\n"
            f"<i>\"{self._truncate(tweet_text, 500)}\"</i>\n\n"
            f"✍️ <b>Draft Replies:</b>\n"
        )

        for i, draft in enumerate(drafts, 1):
            message += f"\n{i}️⃣ {draft}\n"

        # Build inline keyboard
        buttons = []

        # Row 1: Approve buttons for each draft
        approve_row = []
        for i in range(len(drafts)):
            approve_row.append(
                InlineKeyboardButton(
                    f"✅ Draft {i + 1}",
                    callback_data=f"approve_{reply_id}_{i + 1}",
                )
            )
        buttons.append(approve_row)

        # Row 2: Edit Manual + Skip
        buttons.append([
            InlineKeyboardButton(
                "✏️ Edit Manual",
                callback_data=f"edit_{reply_id}",
            ),
            InlineKeyboardButton(
                "⏭️ Skip",
                callback_data=f"skip_{reply_id}",
            ),
        ])

        keyboard = InlineKeyboardMarkup(buttons)

        try:
            await self._app.bot.send_message(
                chat_id=self.chat_id,
                text=message,
                parse_mode="HTML",
                reply_markup=keyboard,
                disable_web_page_preview=False,
            )
            logger.info(f"📤 Sent approval request for reply_id={reply_id}")
            await db.log_activity(
                "approval_sent",
                f"reply_id={reply_id}, tweet=@{username}",
            )
        except Exception as e:
            logger.error(f"Failed to send approval request: {e}")

    async def send_auto_post_approval_request(self, draft_text: str):
        """Send an approval request specifically for a new auto-post."""
        self._pending_auto_post = draft_text
        
        message = (
            f"🤖 <b>New Auto-Post Draft</b>\n\n"
            f"<i>\"{self._truncate(draft_text, 500)}\"</i>\n\n"
            f"Silakan pilih aksi:"
        )

        buttons = [
            [
                InlineKeyboardButton("✅ Approve & Post", callback_data="autopost_approve"),
                InlineKeyboardButton("🔄 Regenerate", callback_data="autopost_regen"),
            ],
            [
                InlineKeyboardButton("✏️ Edit Manual", callback_data="autopost_edit"),
                InlineKeyboardButton("⏭️ Skip", callback_data="autopost_skip"),
            ]
        ]
        keyboard = InlineKeyboardMarkup(buttons)

        try:
            await self._app.bot.send_message(
                chat_id=self.chat_id,
                text=message,
                parse_mode="HTML",
                reply_markup=keyboard,
            )
            logger.info("📤 Sent auto-post approval request")
        except Exception as e:
            logger.error(f"Failed to send auto-post approval request: {e}")

    # ── Callback Handlers ───────────────────────────────────────────

    async def _handle_callback(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        """Handle inline keyboard button presses."""
        query = update.callback_query
        await query.answer()

        data = query.data
        parts = data.split("_")

        if len(parts) < 2:
            return ConversationHandler.END

        action = parts[0]

        if action == "approve" and len(parts) == 3:
            reply_id = int(parts[1])
            draft_num = int(parts[2])
            return await self._process_approval(query, reply_id, draft_num)

        elif action == "edit" and len(parts) == 2:
            reply_id = int(parts[1])
            return await self._process_edit_request(query, reply_id, context)

        elif action == "skip" and len(parts) == 2:
            reply_id = int(parts[1])
            return await self._process_skip(query, reply_id)

        elif action == "autopost":
            sub_action = parts[1]
            if sub_action == "approve":
                return await self._process_autopost_approve(query)
            elif sub_action == "regen":
                return await self._process_autopost_regen(query)
            elif sub_action == "edit":
                return await self._process_autopost_edit(query, context)
            elif sub_action == "skip":
                return await self._process_autopost_skip(query)

        return ConversationHandler.END

    async def _process_approval(self, query, reply_id: int, draft_num: int) -> int:
        """Process approval of a specific draft."""
        # Fetch the draft from DB
        reply_data = await db.get_reply_drafts(reply_id)
        if not reply_data:
            await query.edit_message_text("❌ Reply not found in database.")
            return ConversationHandler.END

        # Get the selected draft text
        draft_key = f"draft_{draft_num}"
        final_text = reply_data.get(draft_key, "")

        if not final_text:
            await query.edit_message_text("❌ Selected draft is empty.")
            return ConversationHandler.END

        # Update DB
        await db.update_reply_status(
            reply_id, "approved", selected_draft=draft_num, final_text=final_text
        )
        tweet_id = reply_data["tweet_id"]
        await db.update_tweet_status(tweet_id, "approved")

        # Update message
        await query.edit_message_text(
            f"✅ <b>Approved Draft {draft_num}!</b>\n\n"
            f"<i>\"{final_text}\"</i>\n\n"
            f"⏳ Posting reply...",
            parse_mode="HTML",
        )

        # Trigger reply posting
        await self._trigger_post_reply(query, reply_id, tweet_id, final_text)

        return ConversationHandler.END

    async def _process_edit_request(
        self, query, reply_id: int, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        """Ask user to type a custom reply."""
        reply_data = await db.get_reply_drafts(reply_id)
        if not reply_data:
            await query.edit_message_text("❌ Reply not found in database.")
            return ConversationHandler.END

        # Store pending edit info in bot context
        context.user_data["pending_edit_reply_id"] = reply_id
        context.user_data["pending_edit_tweet_id"] = reply_data["tweet_id"]

        await query.edit_message_text(
            f"✏️ <b>Manual Edit Mode</b>\n\n"
            f"Ketik reply Anda di bawah ini (max 280 karakter).\n"
            f"Kirim /cancel untuk membatalkan.",
            parse_mode="HTML",
        )

        await db.update_reply_status(reply_id, "editing")

        return WAITING_MANUAL_REPLY

    async def _handle_manual_reply(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        """Handle the manual reply text from user."""
        reply_id = context.user_data.get("pending_edit_reply_id")
        tweet_id = context.user_data.get("pending_edit_tweet_id")

        if not reply_id or not tweet_id:
            await update.message.reply_text("❌ No pending edit found.")
            return ConversationHandler.END

        final_text = update.message.text.strip()

        if len(final_text) > 280:
            await update.message.reply_text(
                f"⚠️ Terlalu panjang ({len(final_text)} karakter). "
                f"Maksimal 280. Coba lagi:"
            )
            return WAITING_MANUAL_REPLY

        # Check if it's an auto-post edit
        is_autopost = context.user_data.get("pending_edit_autopost", False)
        
        if is_autopost:
            await update.message.reply_text(
                f"✅ <b>Manual auto-post approved!</b>\n\n"
                f"<i>\"{final_text}\"</i>\n\n"
                f"⏳ Posting...",
                parse_mode="HTML",
            )
            context.user_data.pop("pending_edit_autopost", None)
            
            if self._auto_post_callback:
                success = await self._auto_post_callback(final_text)
                if success:
                    await self.send_status(f"✅ <b>Auto-Post berhasil terkirim!</b>\n\n💬 <i>\"{self._truncate(final_text, 200)}\"</i>")
                else:
                    await self.send_alert("❌ Auto-Post gagal terkirim!")
            return ConversationHandler.END

        # Update DB
        await db.update_reply_status(
            reply_id, "approved", selected_draft=0, final_text=final_text
        )
        await db.update_tweet_status(tweet_id, "approved")

        await update.message.reply_text(
            f"✅ <b>Manual reply approved!</b>\n\n"
            f"<i>\"{final_text}\"</i>\n\n"
            f"⏳ Posting reply...",
            parse_mode="HTML",
        )

        # Clear pending edit
        context.user_data.pop("pending_edit_reply_id", None)
        context.user_data.pop("pending_edit_tweet_id", None)

        # Trigger posting
        await self._trigger_post_reply(
            update.message, reply_id, tweet_id, final_text
        )

        return ConversationHandler.END

    async def _process_skip(self, query, reply_id: int) -> int:
        """Skip this tweet — don't reply."""
        reply_data = await db.get_reply_drafts(reply_id)
        if reply_data:
            await db.update_reply_status(reply_id, "skipped")
            await db.update_tweet_status(reply_data["tweet_id"], "skipped")

        await query.edit_message_text("⏭️ <b>Skipped.</b>", parse_mode="HTML")
        await db.log_activity("reply_skipped", f"reply_id={reply_id}")

        return ConversationHandler.END

    async def _handle_cancel(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        """Cancel the current edit operation."""
        context.user_data.pop("pending_edit_reply_id", None)
        context.user_data.pop("pending_edit_tweet_id", None)
        context.user_data.pop("pending_edit_autopost", None)
        await update.message.reply_text("❌ Edit dibatalkan.")
        return ConversationHandler.END

    # ── Auto-Post Callbacks ─────────────────────────────────────────

    async def _process_autopost_approve(self, query) -> int:
        draft = self._pending_auto_post
        if not draft:
            await query.edit_message_text("❌ Draft auto-post tidak ditemukan.")
            return ConversationHandler.END

        await query.edit_message_text(
            f"✅ <b>Auto-Post Approved!</b>\n\n<i>\"{draft}\"</i>\n\n⏳ Posting...",
            parse_mode="HTML",
        )
        
        if self._auto_post_callback:
            success = await self._auto_post_callback(draft)
            if success:
                await self._app.bot.send_message(self.chat_id, f"✅ <b>Auto-Post berhasil terkirim!</b>\n\n💬 <i>\"{self._truncate(draft, 200)}\"</i>", parse_mode="HTML")
            else:
                await self.send_alert("❌ Auto-Post gagal terkirim!")
        self._pending_auto_post = ""
        return ConversationHandler.END

    async def _process_autopost_regen(self, query) -> int:
        await query.edit_message_text("⏳ Regenerating draft...")
        
        if not self._regenerate_post_callback:
            await query.edit_message_text("❌ Callback regenerate tidak tersedia.")
            return ConversationHandler.END
            
        new_draft = await self._regenerate_post_callback()
        if not new_draft:
            await query.edit_message_text("❌ Gagal membuat draft baru.")
            return ConversationHandler.END
            
        # Re-send the approval request with new draft
        await self.send_auto_post_approval_request(new_draft)
        return ConversationHandler.END

    async def _process_autopost_edit(self, query, context) -> int:
        context.user_data["pending_edit_autopost"] = True
        await query.edit_message_text(
            f"✏️ <b>Manual Edit Auto-Post</b>\n\n"
            f"Ketik post Anda di bawah ini (max 280 karakter).\n"
            f"Kirim /cancel untuk membatalkan.",
            parse_mode="HTML",
        )
        return WAITING_MANUAL_REPLY

    async def _process_autopost_skip(self, query) -> int:
        self._pending_auto_post = ""
        await query.edit_message_text("⏭️ <b>Auto-Post Skipped.</b>", parse_mode="HTML")
        # Regenerate automatically since user skipped
        if self._regenerate_post_callback:
            new_draft = await self._regenerate_post_callback()
            if new_draft:
                await self.send_auto_post_approval_request(new_draft)
        return ConversationHandler.END

    # ── Post Reply Trigger ──────────────────────────────────────────

    async def _trigger_post_reply(
        self, message_or_query, reply_id: int, tweet_id: str, final_text: str
    ):
        """Trigger the browser automation to post the reply."""
        if not self._post_reply_callback:
            logger.error("No post_reply_callback set!")
            return

        # Get tweet URL from DB
        tweet_data = await db.get_tweet(tweet_id)
        if not tweet_data:
            logger.error(f"Tweet {tweet_id} not found in DB")
            return

        tweet_url = tweet_data.get("tweet_url", "")

        try:
            success = await self._post_reply_callback(tweet_url, final_text)

            if success:
                await db.update_reply_status(reply_id, "posted")
                await db.update_tweet_status(tweet_id, "replied")
                await db.log_activity(
                    "reply_posted",
                    f"reply_id={reply_id}, tweet_url={tweet_url}",
                )

                # Send confirmation
                await self._app.bot.send_message(
                    chat_id=self.chat_id,
                    text=(
                        f"✅ <b>Reply berhasil terkirim!</b>\n\n"
                        f"🔗 <a href=\"{tweet_url}\">Lihat Tweet</a>\n"
                        f"💬 <i>\"{self._truncate(final_text, 200)}\"</i>"
                    ),
                    parse_mode="HTML",
                )
            else:
                await db.update_reply_status(reply_id, "failed")
                await db.update_tweet_status(tweet_id, "failed")
                await db.log_activity(
                    "reply_failed",
                    f"reply_id={reply_id}, tweet_url={tweet_url}",
                )

                await self._app.bot.send_message(
                    chat_id=self.chat_id,
                    text=(
                        f"❌ <b>Reply gagal terkirim!</b>\n\n"
                        f"🔗 <a href=\"{tweet_url}\">Tweet</a>\n"
                        f"Cek log untuk detail error."
                    ),
                    parse_mode="HTML",
                )

        except Exception as e:
            logger.error(f"Error posting reply: {e}")
            await db.update_reply_status(reply_id, "failed")
            await self.send_alert(f"❌ Error posting reply: {e}")

    # ── Utility Methods ─────────────────────────────────────────────

    async def send_alert(self, message: str):
        """Send an alert message to the owner."""
        try:
            if self._app:
                await self._app.bot.send_message(
                    chat_id=self.chat_id,
                    text=f"⚠️ <b>Alert:</b> {message}",
                    parse_mode="HTML",
                )
            else:
                # If bot not started yet, use direct Bot instance
                from telegram import Bot

                bot = Bot(token=self.settings.telegram.bot_token)
                await bot.send_message(
                    chat_id=self.chat_id,
                    text=f"⚠️ <b>Alert:</b> {message}",
                    parse_mode="HTML",
                )
        except Exception as e:
            logger.error(f"Failed to send Telegram alert: {e}")

    async def send_status(self, message: str):
        """Send a status update to the owner."""
        try:
            if self._app:
                await self._app.bot.send_message(
                    chat_id=self.chat_id,
                    text=message,
                    parse_mode="HTML",
                )
        except Exception as e:
            logger.error(f"Failed to send status: {e}")

    async def _handle_start(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """Handle /start command."""
        scrape_status = "🟢 ON" if self.scraping_active.is_set() else "🔴 OFF"
        await update.message.reply_text(
            "🤖 <b>X Engagement Agent Bot</b>\n\n"
            "Saya akan mengirimkan tweet trending beserta draft reply "
            "untuk Anda approve sebelum posting.\n\n"
            f"Scraping: {scrape_status}\n\n"
            "/scrape_on — Mulai scraping otomatis\n"
            "/scrape_off — Pause scraping\n"
            "/scrape_once — Jalankan 1x scrape cycle\n"
            "/notif_on — Mulai memonitor notifikasi\n"
            "/notif_off — Berhenti memonitor notifikasi\n"
            "/post — Generate auto-post sekarang\n"
            "/status — Cek status agent\n"
            "/cancel — Batalkan edit yang sedang berlangsung",
            parse_mode="HTML",
        )

    async def _handle_status(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """Handle /status command — show current agent status."""
        replies_hour = await db.get_replies_last_hour()
        max_replies = self.settings.rate_limit.max_replies_per_hour
        recent = await db.get_recent_activity(10)
        scrape_status = "🟢 ON" if self.scraping_active.is_set() else "🔴 OFF"
        notif_status = "🟢 ON" if self.notif_active.is_set() else "🔴 OFF"

        status_text = (
            f"📊 <b>Agent Status</b>\n\n"
            f"🔍 Scraping: {scrape_status}\n"
            f"🔔 Notifications: {notif_status}\n"
            f"💬 Replies (1h): {replies_hour}/{max_replies}\n\n"
            f"📋 <b>Recent Activity:</b>\n"
        )

        for act in recent[:5]:
            status_text += f"• <code>{act['action']}</code>: {act['details'][:60]}\n"

        if not recent:
            status_text += "• No activity yet\n"

        await update.message.reply_text(status_text, parse_mode="HTML")

    # ── Auto-Post Command ───────────────────────────────────────────

    async def _handle_post(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """Handle /post — manually trigger auto-post generation."""
        if not self._regenerate_post_callback:
            await update.message.reply_text(
                "❌ Auto-post belum diaktifkan.", parse_mode="HTML"
            )
            return

        await update.message.reply_text(
            "⏳ <b>Generating auto-post draft...</b>",
            parse_mode="HTML",
        )

        new_draft = await self._regenerate_post_callback()
        if new_draft:
            await self.send_auto_post_approval_request(new_draft)
        else:
            await update.message.reply_text(
                "❌ Gagal membuat draft. Cek log untuk detail.",
                parse_mode="HTML",
            )

    # ── Scraping Control Commands ───────────────────────────────────

    async def _handle_scrape_on(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """Handle /scrape_on — start continuous scraping."""
        if self.scraping_active.is_set():
            await update.message.reply_text(
                "ℹ️ Scraping sudah aktif.", parse_mode="HTML"
            )
            return

        self.scraping_active.set()
        await db.log_activity("scraping_started", "Manual start via Telegram")
        logger.info("🟢 Scraping activated via Telegram")
        await update.message.reply_text(
            "🟢 <b>Scraping dimulai!</b>\n\n"
            "Agent akan mulai mencari tweet trending secara berkala.\n"
            "Kirim /scrape_off untuk pause.",
            parse_mode="HTML",
        )

    async def _handle_scrape_off(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """Handle /scrape_off — pause scraping."""
        if not self.scraping_active.is_set():
            await update.message.reply_text(
                "ℹ️ Scraping sudah dalam keadaan pause.", parse_mode="HTML"
            )
            return

        self.scraping_active.clear()
        await db.log_activity("scraping_stopped", "Manual stop via Telegram")
        logger.info("🔴 Scraping paused via Telegram")
        await update.message.reply_text(
            "🔴 <b>Scraping di-pause.</b>\n\n"
            "Browser tetap terbuka. Session tidak terpengaruh.\n"
            "Kirim /scrape_on untuk melanjutkan atau /scrape_once untuk 1x cycle.",
            parse_mode="HTML",
        )

    async def _handle_scrape_once(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """Handle /scrape_once — trigger a single scrape cycle."""
        self._scrape_once_requested.set()
        await db.log_activity("scrape_once", "Single cycle requested via Telegram")
        logger.info("🔂 Single scrape cycle requested via Telegram")
        await update.message.reply_text(
            "🔂 <b>Single scrape cycle dimulai...</b>\n\n"
            "Agent akan melakukan 1 siklus pencarian tweet, lalu pause kembali.",
            parse_mode="HTML",
        )

    async def _handle_notif_on(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """Handle /notif_on — start monitoring notifications."""
        if self.notif_active.is_set():
            await update.message.reply_text(
                "ℹ️ Monitor notifikasi sudah aktif.", parse_mode="HTML"
            )
            return

        self.notif_active.set()
        await db.log_activity("notif_started", "Manual start via Telegram")
        logger.info("🟢 Notifications monitoring activated via Telegram")
        await update.message.reply_text(
            "🟢 <b>Monitor Notifikasi dimulai!</b>\n\n"
            "Agent akan selalu mengecek tab Notifications.\n"
            "Kirim /notif_off untuk pause.",
            parse_mode="HTML",
        )

    async def _handle_notif_off(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """Handle /notif_off — stop monitoring notifications."""
        if not self.notif_active.is_set():
            await update.message.reply_text(
                "ℹ️ Monitor notifikasi sudah pause.", parse_mode="HTML"
            )
            return

        self.notif_active.clear()
        await db.log_activity("notif_stopped", "Manual stop via Telegram")
        logger.info("🔴 Notifications monitoring paused via Telegram")
        await update.message.reply_text(
            "🔴 <b>Monitor Notifikasi di-pause.</b>\n\n"
            "Kirim /notif_on untuk melanjutkan.",
            parse_mode="HTML",
        )

    @staticmethod
    def _truncate(text: str, max_len: int = 300) -> str:
        """Truncate text with ellipsis if too long."""
        if len(text) <= max_len:
            return text
        return text[: max_len - 3] + "..."
