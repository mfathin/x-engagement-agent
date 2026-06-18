"""
Database query functions for the engagement agent.

All functions are async and use aiosqlite for non-blocking I/O.
"""

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from contextlib import asynccontextmanager

import aiosqlite

from db.models import get_db_path


@asynccontextmanager
async def _connect():
    """Get a database connection."""
    async with aiosqlite.connect(str(get_db_path())) as db:
        db.row_factory = aiosqlite.Row
        yield db


# ── Tweet Queries ───────────────────────────────────────────────────


async def is_tweet_processed(tweet_id: str) -> bool:
    """Check if a tweet has already been processed (exists in DB)."""
    async with _connect() as db:
        cursor = await db.execute(
            "SELECT 1 FROM processed_tweets WHERE tweet_id = ?",
            (tweet_id,),
        )
        row = await cursor.fetchone()
        return row is not None


async def save_tweet(tweet_data: Dict[str, Any]) -> None:
    """Save a newly discovered tweet to the database."""
    async with _connect() as db:
        await db.execute(
            """
            INSERT OR IGNORE INTO processed_tweets 
            (tweet_id, author_name, username, tweet_text, tweet_url,
             likes, replies, retweets, tweet_timestamp, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
            """,
            (
                tweet_data["tweet_id"],
                tweet_data.get("author_name", ""),
                tweet_data.get("username", ""),
                tweet_data.get("tweet_text", ""),
                tweet_data.get("tweet_url", ""),
                tweet_data.get("likes", 0),
                tweet_data.get("replies", 0),
                tweet_data.get("retweets", 0),
                tweet_data.get("tweet_timestamp", ""),
            ),
        )
        await db.commit()


async def update_tweet_status(tweet_id: str, status: str) -> None:
    """Update the processing status of a tweet."""
    async with _connect() as db:
        await db.execute(
            "UPDATE processed_tweets SET status = ? WHERE tweet_id = ?",
            (status, tweet_id),
        )
        await db.commit()


async def get_tweet(tweet_id: str) -> Optional[Dict[str, Any]]:
    """Get a tweet by its ID."""
    async with _connect() as db:
        cursor = await db.execute(
            "SELECT * FROM processed_tweets WHERE tweet_id = ?",
            (tweet_id,),
        )
        row = await cursor.fetchone()
        if row:
            return dict(row)
        return None


# ── Reply Queries ───────────────────────────────────────────────────


async def save_reply_draft(
    tweet_id: str, drafts: List[str], ai_provider: str = ""
) -> int:
    """
    Save AI-generated reply drafts for a tweet.
    Returns the reply_history row id.
    """
    # Pad drafts to exactly 3
    while len(drafts) < 3:
        drafts.append("")

    async with _connect() as db:
        cursor = await db.execute(
            """
            INSERT INTO reply_history 
            (tweet_id, draft_1, draft_2, draft_3, ai_provider, status)
            VALUES (?, ?, ?, ?, ?, 'pending')
            """,
            (tweet_id, drafts[0], drafts[1], drafts[2], ai_provider),
        )
        await db.commit()
        return cursor.lastrowid


async def get_reply_drafts(reply_id: int) -> Optional[Dict[str, Any]]:
    """Get reply drafts by reply history ID."""
    async with _connect() as db:
        cursor = await db.execute(
            "SELECT * FROM reply_history WHERE id = ?",
            (reply_id,),
        )
        row = await cursor.fetchone()
        if row:
            return dict(row)
        return None


async def update_reply_status(
    reply_id: int,
    status: str,
    selected_draft: Optional[int] = None,
    final_text: Optional[str] = None,
) -> None:
    """Update reply status after approval/skip/post."""
    async with _connect() as db:
        updates = ["status = ?"]
        params: list = [status]

        if selected_draft is not None:
            updates.append("selected_draft = ?")
            params.append(selected_draft)

        if final_text is not None:
            updates.append("final_text = ?")
            params.append(final_text)

        if status == "approved":
            updates.append("approved_at = datetime('now')")
        elif status == "posted":
            updates.append("posted_at = datetime('now')")

        params.append(reply_id)

        await db.execute(
            f"UPDATE reply_history SET {', '.join(updates)} WHERE id = ?",
            params,
        )
        await db.commit()


async def get_replies_last_hour() -> int:
    """Count how many replies were posted in the last hour (for rate limiting)."""
    one_hour_ago = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    async with _connect() as db:
        cursor = await db.execute(
            """
            SELECT COUNT(*) as cnt FROM reply_history 
            WHERE status = 'posted' AND posted_at >= ?
            """,
            (one_hour_ago,),
        )
        row = await cursor.fetchone()
        return row["cnt"] if row else 0


# ── Activity Log ────────────────────────────────────────────────────


async def log_activity(action: str, details: str = "") -> None:
    """Log an activity for audit purposes."""
    async with _connect() as db:
        await db.execute(
            "INSERT INTO activity_log (action, details) VALUES (?, ?)",
            (action, details),
        )
        await db.commit()


async def get_recent_activity(limit: int = 50) -> List[Dict[str, Any]]:
    """Get recent activity log entries."""
    async with _connect() as db:
        cursor = await db.execute(
            "SELECT * FROM activity_log ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]
