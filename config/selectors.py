"""
X (Twitter) DOM selectors — centralized for easy maintenance.

Twitter/X frequently changes their frontend. When selectors break,
update ONLY this file to fix the entire application.
"""


# ── Tweet Discovery ─────────────────────────────────────────────────

# Individual tweet container
TWEET_ARTICLE = 'article[data-testid="tweet"]'

# Tweet text content
TWEET_TEXT = '[data-testid="tweetText"]'

# User name & handle section (contains both display name and @username)
USER_NAME = '[data-testid="User-Name"]'

# Timestamp link (the <time> element inside the tweet)
TWEET_TIMESTAMP = "time[datetime]"

# Tweet link — the <a> with the timestamp usually has the permalink
TWEET_LINK = 'a[href*="/status/"]'

# ── Engagement Counts ───────────────────────────────────────────────

# Reply count button
REPLY_COUNT_BUTTON = '[data-testid="reply"]'

# Retweet count button
RETWEET_COUNT_BUTTON = '[data-testid="retweet"]'

# Like count button
LIKE_COUNT_BUTTON = '[data-testid="like"]'

# Views / impressions (aria-label often contains "views")
VIEWS_BUTTON = 'a[href*="/analytics"]'

# ── Reply Posting ───────────────────────────────────────────────────

# Reply text input box on tweet detail page
REPLY_TEXT_BOX = '[data-testid="tweetTextarea_0"]'

# Fallback: the contenteditable div inside the reply composer
REPLY_TEXT_BOX_FALLBACK = 'div[data-testid="tweetTextarea_0"] div[contenteditable="true"]'

# The reply button that submits the reply
REPLY_SUBMIT_BUTTON = '[data-testid="tweetButtonInline"]'

# ── Session Validation ──────────────────────────────────────────────

# Element present when logged in (the main timeline/feed)
LOGGED_IN_INDICATOR = '[data-testid="AppTabBar_Home_Link"]'

# Element present on the login page
LOGIN_PAGE_INDICATOR = '[data-testid="loginButton"]'

# The compose tweet button (visible when logged in on home)
COMPOSE_TWEET_BUTTON = '[data-testid="AppTabBar_Profile_Link"]'

# ── Search ──────────────────────────────────────────────────────────

# Search input field
SEARCH_INPUT = '[data-testid="SearchBox_Search_Input"]'

# Search "Latest" tab
SEARCH_LATEST_TAB = 'a[href*="f=live"]'

# ── Navigation ──────────────────────────────────────────────────────

# Home tab
HOME_TAB = 'a[data-testid="AppTabBar_Home_Link"]'

# ── Notifications ───────────────────────────────────────────────────

# A link inside a notification item that points to a tweet
NOTIFICATION_LINK = 'div[data-testid="cellInnerDiv"] a[href*="/status/"]'
