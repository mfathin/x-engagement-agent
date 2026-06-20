"""
SQLite database schema and initialization.

Tables:
  - processed_tweets: tracks every tweet discovered and its processing state
  - reply_history: stores AI-generated drafts and the final posted reply
  - activity_log: audit trail for all tool actions
"""

import aiosqlite
from pathlib import Path

# Database file lives in the project root
_DB_PATH: Path | None = None


def set_db_path(path: Path):
    """Set the database file path. Called once at startup."""
    global _DB_PATH
    _DB_PATH = path


def get_db_path() -> Path:
    """Get the database file path."""
    if _DB_PATH is None:
        # Fallback to project root
        return Path(__file__).resolve().parent.parent / "engagement.db"
    return _DB_PATH


_SCHEMA = """
-- Tracks every tweet discovered by the scraper
CREATE TABLE IF NOT EXISTS processed_tweets (
    tweet_id        TEXT PRIMARY KEY,
    author_name     TEXT NOT NULL DEFAULT '',
    username        TEXT NOT NULL DEFAULT '',
    tweet_text      TEXT NOT NULL DEFAULT '',
    tweet_url       TEXT NOT NULL DEFAULT '',
    likes           INTEGER NOT NULL DEFAULT 0,
    replies         INTEGER NOT NULL DEFAULT 0,
    retweets        INTEGER NOT NULL DEFAULT 0,
    tweet_timestamp TEXT NOT NULL DEFAULT '',
    discovered_at   TEXT NOT NULL DEFAULT (datetime('now')),
    status          TEXT NOT NULL DEFAULT 'pending'
    -- status: pending | drafts_sent | approved | skipped | replied | failed
);

-- Stores AI-generated reply drafts and final posted text
CREATE TABLE IF NOT EXISTS reply_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    tweet_id        TEXT NOT NULL,
    draft_1         TEXT NOT NULL DEFAULT '',
    draft_2         TEXT NOT NULL DEFAULT '',
    draft_3         TEXT NOT NULL DEFAULT '',
    selected_draft  INTEGER DEFAULT NULL,
    final_text      TEXT DEFAULT NULL,
    ai_provider     TEXT DEFAULT NULL,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    approved_at     TEXT DEFAULT NULL,
    posted_at       TEXT DEFAULT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',
    -- status: pending | approved | editing | posted | failed
    FOREIGN KEY (tweet_id) REFERENCES processed_tweets(tweet_id)
);

-- Audit trail for every action taken by the tool
CREATE TABLE IF NOT EXISTS activity_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       TEXT NOT NULL DEFAULT (datetime('now')),
    action          TEXT NOT NULL,
    details         TEXT DEFAULT ''
);

-- Tracks autonomously generated posts to evaluate later
CREATE TABLE IF NOT EXISTS auto_posts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    post_text       TEXT NOT NULL,
    post_url        TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    likes           INTEGER DEFAULT 0,
    replies         INTEGER DEFAULT 0,
    analyzed        INTEGER DEFAULT 0 -- 0=false, 1=true
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_tweets_status ON processed_tweets(status);
CREATE INDEX IF NOT EXISTS idx_tweets_discovered ON processed_tweets(discovered_at);
CREATE INDEX IF NOT EXISTS idx_replies_tweet ON reply_history(tweet_id);
CREATE INDEX IF NOT EXISTS idx_replies_status ON reply_history(status);
CREATE INDEX IF NOT EXISTS idx_activity_timestamp ON activity_log(timestamp);
CREATE INDEX IF NOT EXISTS idx_auto_posts_analyzed ON auto_posts(analyzed);
"""


async def init_db() -> None:
    """Initialize the database: create tables if they don't exist."""
    db_path = get_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    async with aiosqlite.connect(str(db_path)) as db:
        await db.executescript(_SCHEMA)
        await db.commit()
