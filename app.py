"""
Earnings App — Flask + SQLite (stdlib only, no native deps).

Value system: users earn POINTS (tasks, mining, referrals, bonuses, rewarded
ads). Points can be converted into the app's own custom coin (admin-set name,
symbol and rate). PKR has been removed entirely; withdrawals are behind a
global admin kill-switch (default OFF) and, when enabled, are points-based.
Admin panel controls: users, tasks (+per-task cooldown), mining (+claim
interval), daily bonus, commissions, custom coin, withdrawals switch and a
multi-provider ads system (mock rendering in this web demo — real mobile SDKs
are integrated in the mobile app build).
Run:  venv/bin/python app.py   (or: python app.py if Flask is installed)
"""
import os
import sqlite3
import secrets
import string
from datetime import datetime, timezone, timedelta
from functools import wraps

from flask import Flask, g, jsonify, request, session, send_from_directory
from werkzeug.security import generate_password_hash, check_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "earnings.db")

app = Flask(__name__, static_folder="static", template_folder="templates")
app.secret_key = os.environ.get("EARNINGS_SECRET", "dev-secret-change-me")
app.config["JSON_SORT_KEYS"] = False


# ---------------------------------------------------------------- DB helpers
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def parse_iso(s):
    try:
        dt = datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None
    # Legacy / hand-written rows may lack a timezone; treat them as UTC
    # instead of crashing on aware-vs-naive comparisons downstream.
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


# ---------------------------------------------------------------- settings
# NOTE: the internal balance column is still named `coins` in the DB, but it
# is POINTS everywhere user-facing. The custom coin lives in coin_balance.
DEFAULT_SETTINGS = {
    "app_name": "Meri Kamai",
    "mining_enabled": "1",
    "coins_per_hour": "120",
    "max_mining_hours": "8",
    "min_claim_interval_min": "60",
    "l1_pct": "10",
    "l2_pct": "5",
    "l3_pct": "2",
    # Withdrawals kill-switch (default OFF) + points-based withdrawal floor.
    "withdrawals_enabled": "0",
    "min_withdrawal_points": "5000",
    # Custom coin (replaces PKR as the value system).
    "coin_name": "Gold Coin",
    "coin_symbol": "GLD",
    "points_per_coin": "1000",
    "min_convert_points": "1000",
    # Daily bonus.
    "daily_bonus_points": "50",
    "daily_bonus_cooldown_hours": "24",
    # Ads.
    "rewarded_bonus_points": "20",
    "interstitial_every_n_tasks": "3",
    # v1.5: home banner, Watch & Earn, transfers, categories (JSON list).
    "home_banner_enabled": "1",
    "watch_ads_daily_limit": "10",
    "watch_ads_cooldown_min": "30",
    "watch_ads_reward": "20",
    "watch_ads_provider": "Unity",
    "transfers_enabled": "1",
    "transfer_min": "10",
    "transfer_max": "5000",
    "transfer_fee_percent": "0",
    "transfer_daily_limit": "10000",
    "categories": '["Daily", "Video", "Social", "Special"]',
    # v1.6: tap-to-earn + swap + leaderboard (mobile app server mode).
    "tap_points_per_tap": "5",
    "tap_daily_limit": "100",
    # v1.7: Daily Spin Wheel + Falcon Tap Levels (tap-game progression).
    "wheel_spins_per_day": "3",
    "wheel_min_reward": "10",
    "wheel_max_reward": "100",
    "wheel_cooldown_min": "60",
    "tap_level_bonus": "1",
    # v1.8: viral growth - invite-gated withdrawal tiers, milestones, streaks.
    "withdrawal_gating_enabled": "1",
    "tier_bronze_invites": "3",
    "tier_bronze_limit": "5000",
    "tier_silver_invites": "10",
    "tier_silver_limit": "25000",
    "tier_gold_invites": "25",
    "tier_gold_limit": "100000",
    "tier_diamond_invites": "50",
    "tier_diamond_limit": "0",
    "milestone_thresholds": "5,10,25,50,100",
    "milestone_bonuses": "500,1500,5000,15000,50000",
    "welcome_bonus": "100",
    "invite_task_reward": "200",
    "streak_bonus_per_day": "10",
    "streak_bonus_cap": "200",
    # v1.9: invite economy in TOKENS (GLD). Commissions stay points-based.
    "invite_token_reward": "0.2",
    "welcome_token_gld": "0.1",
    "milestone_token_bonuses": "0.5,1.5,5,15,50",
    # v1.9: inviter ranks (thresholds = valid invites; multipliers on the
    # per-invite token reward). Starter is the base rank below threshold 1.
    "inviter_rank_thresholds": "10,25,50",
    "inviter_rank_multipliers": "1.1,1.25,1.5",
    # v1.9: weekly top-10 prize pool (GLD) + split type.
    "weekly_pool_gld": "100",
    "weekly_pool_split": "weighted",
    # v1.9: social per-view rewards (manual verification, never an API).
    "social_rate_gld_per_1000": "0.5",
    "social_cap_per_post": "50",
    "social_cap_per_week": "100",
    # v1.9: token withdrawals + review threshold + update alert.
    "min_token_withdrawal": "5",
    "token_withdrawal_fee_pct": "0",
    "token_withdrawal_review_threshold": "50",
    "latest_version_code": "13",
    # v1.1 (v22): social approval mode (manual = team verifies, auto =
    # approve instantly at claimed views with the existing caps),
    # withdrawal method toggles, token transfers — all team-set.
    "social_approval_mode": "manual",
    "withdrawal_method_bank": "1",
    "withdrawal_method_mobile_wallet": "1",
    "withdrawal_method_crypto": "1",
    "token_transfer_enabled": "1",
    "token_transfer_min": "0.1",
    "token_transfer_max": "100",
    "token_transfer_fee_pct": "0",
    "token_transfer_daily_limit": "500",
    # v2.1: tap-level thresholds + weekly split pcts (were hardcoded).
    "tap_level_thresholds": "100,500,1500",
    "weekly_pool_pcts": "25,18,14,11,9,7,5,4,4,3",
    # v2.1 referral auto-capture: website link base for invite download
    # links (falls back to this backend's own URL when empty) + APK URL
    # shown on the /download landing page (empty = neutral "coming from
    # the team" text, never a guessed link).
    "website_url": "",
    "apk_download_url": "",
    # v2.0: Home banner slider (team-set; served to the app via /api/settings).
    "slider_enabled": "1",
    "slider_interval_sec": "4",
    "update_note": "",
    "swap_points_per_coin": "1000",
    "swap_min_coins": "1",
    "leaderboard_enabled": "1",
}

# Legacy keys that must never surface again (removed from the value system).
LEGACY_SETTINGS = ("pkr_per_1000_coins", "min_withdrawal_pkr")

AD_PROVIDERS = [
    # (provider, enabled, placement_type, unit_id)
    ("AdMob", 1, "banner", "ca-app-pub-3940256099942544/6300978111"),
    ("Unity Ads", 0, "interstitial", "Interstitial_Android"),
    ("StartApp", 0, "banner", "123456789"),
    ("Meta Audience Network", 0, "interstitial", "1234567890_1234567890"),
    ("Custom", 0, "custom", ""),
]


def get_settings(db):
    rows = db.execute("SELECT key, value FROM settings").fetchall()
    s = dict(DEFAULT_SETTINGS)
    for r in rows:
        if r["key"] in LEGACY_SETTINGS:
            continue
        s[r["key"]] = r["value"]
    return s


def get_setting(db, key):
    row = db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else DEFAULT_SETTINGS.get(key)


def set_setting(db, key, value):
    db.execute(
        "INSERT INTO settings(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(value)),
    )


def migrate(db):
    """Idempotent upgrade for databases created before this version.

    Adds new columns/tables, seeds the ads providers, drops the legacy PKR
    settings rows and backfills legacy withdrawal rows to points. Never
    deletes user data or balances.
    """
    user_cols = {r[1] for r in db.execute("PRAGMA table_info(users)")}
    if "coin_balance" not in user_cols:
        db.execute("ALTER TABLE users ADD COLUMN coin_balance REAL NOT NULL DEFAULT 0")
    if "auth_token" not in user_cols:
        db.execute("ALTER TABLE users ADD COLUMN auth_token TEXT")
    if "last_active" not in user_cols:
        db.execute("ALTER TABLE users ADD COLUMN last_active TEXT")
    task_cols = {r[1] for r in db.execute("PRAGMA table_info(tasks)")}
    if "cooldown_hours" not in task_cols:
        db.execute("ALTER TABLE tasks ADD COLUMN cooldown_hours REAL NOT NULL DEFAULT 24")
    if "daily_limit" not in task_cols:
        db.execute("ALTER TABLE tasks ADD COLUMN daily_limit INTEGER NOT NULL DEFAULT 0")
    wd_cols = {r[1] for r in db.execute("PRAGMA table_info(withdrawals)")}
    if "amount_points" not in wd_cols:
        db.execute("ALTER TABLE withdrawals ADD COLUMN amount_points INTEGER NOT NULL DEFAULT 0")
    # v1.1 (v22): transfers gain a currency ('points' or 'tokens').
    tr_cols = {r[1] for r in db.execute("PRAGMA table_info(transfers)")}
    if "currency" not in tr_cols:
        db.execute("ALTER TABLE transfers ADD COLUMN currency TEXT NOT NULL DEFAULT 'points'")
    db.execute("UPDATE withdrawals SET amount_points = coins_used WHERE amount_points = 0")
    db.executescript(NEW_SCHEMA)
    db.execute(
        "DELETE FROM settings WHERE key IN (?, ?)", LEGACY_SETTINGS)
    for k, v in DEFAULT_SETTINGS.items():
        db.execute("INSERT OR IGNORE INTO settings(key, value) VALUES(?, ?)", (k, v))
    # v1.8: seed the daily invite task once (reward follows invite_task_reward).
    if db.execute("SELECT COUNT(*) FROM tasks WHERE title = ?",
                  (INVITE_DAILY_TITLE,)).fetchone()[0] == 0:
        s_now = get_settings(db)
        db.execute(
            "INSERT INTO tasks(title, description, reward, task_type, enabled, cooldown_hours, daily_limit)"
            " VALUES(?, ?, ?, 'daily', 1, 24, 1)",
            (INVITE_DAILY_TITLE,
             "Invite a friend today and have them complete a task - this task completes automatically when you gain a valid invite today.",
             int(s_now["invite_task_reward"])))
    if db.execute("SELECT COUNT(*) FROM ad_providers").fetchone()[0] == 0:
        for name, enabled, ptype, unit in AD_PROVIDERS:
            db.execute(
                "INSERT INTO ad_providers(provider, enabled, placement_type, unit_id)"
                " VALUES(?, ?, ?, ?)", (name, enabled, ptype, unit))
    db.commit()


def init_db():
    """Create schema + seed data on first run; migrate on every run."""
    fresh = not os.path.exists(DB_PATH)
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    if fresh:
        seed(db)
    db.commit()
    migrate(db)
    db.close()


# ---------------------------------------------------------------- users
def make_referral_code(username):
    suffix = "".join(secrets.choice(string.digits) for _ in range(4))
    base = "".join(c for c in username.upper() if c.isalnum())[:8] or "USER"
    return f"{base}{suffix}"


def user_public(row):
    if row is None:
        return None
    return {
        "id": row["id"],
        "username": row["username"],
        # DB column `coins` IS the points balance.
        "points": row["coins"],
        "coin_balance": row["coin_balance"] if "coin_balance" in row.keys() else 0,
        "is_admin": bool(row["is_admin"]),
        "is_banned": bool(row["is_banned"]),
        "referral_code": row["referral_code"],
        "created_at": row["created_at"],
    }


def token_user(db):
    """User identified by an `Authorization: Bearer <token>` header.

    Returns the user row, or None when no Bearer token was presented.
    A presented-but-unknown token is NOT silently ignored — callers that
    require auth must reject it (see login_required). The column may be
    missing on databases that predate migrate(); treat that as no token.
    """
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    tok = auth[7:].strip()
    if not tok:
        return None
    try:
        return db.execute(
            "SELECT * FROM users WHERE auth_token = ?", (tok,)).fetchone()
    except sqlite3.OperationalError:
        return None


def current_user(db):
    # Bearer token takes precedence (mobile app server mode); otherwise
    # the browser session (built-in web UI). Token auth never creates a
    # session — it is checked on every request from the token itself.
    tu = token_user(db)
    if tu is not None:
        return tu
    uid = session.get("uid")
    if not uid:
        return None
    return db.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()


def login_required(f):
    @wraps(f)
    def wrapper(*a, **kw):
        db = get_db()
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer ") and token_user(db) is None:
            return jsonify({"ok": False, "error": "invalid token"}), 401
        u = current_user(db)
        if not u:
            return jsonify({"ok": False, "error": "login required"}), 401
        if u["is_banned"]:
            return jsonify({"ok": False, "error": "account banned"}), 403
        # v1.9 live stats: every authenticated call marks the user active.
        try:
            db.execute("UPDATE users SET last_active = ? WHERE id = ?",
                       (now_iso(), u["id"]))
            db.commit()
        except Exception:
            pass
        return f(*a, **kw)
    return wrapper


def admin_required(f):
    @wraps(f)
    def wrapper(*a, **kw):
        db = get_db()
        u = current_user(db)
        if not u or not u["is_admin"]:
            return jsonify({"ok": False, "error": "admin only"}), 403
        return f(*a, **kw)
    return wrapper


# ---------------------------------------------------------------- earnings
def credit_coins(db, user_id, amount, source, ref_user_id=None):
    """Credit POINTS to a user and distribute 3-level affiliate commissions.

    Commissions are paid on direct earnings only (not on commissions).
    The DB column is still named `coins`; it is the points balance.
    """
    if amount <= 0:
        return
    db.execute("UPDATE users SET coins = coins + ? WHERE id = ?", (amount, user_id))
    settings = get_settings(db)
    pcts = [int(settings["l1_pct"]), int(settings["l2_pct"]), int(settings["l3_pct"])]
    upline = db.execute(
        "SELECT referred_by FROM users WHERE id = ?", (user_id,)
    ).fetchone()
    level = 0
    seen = set()
    while upline and upline["referred_by"] and level < 3:
        up_id = upline["referred_by"]
        if up_id in seen:
            break
        seen.add(up_id)
        comm = int(amount * pcts[level] / 100)
        if comm > 0:
            db.execute("UPDATE users SET coins = coins + ? WHERE id = ?", (comm, up_id))
            db.execute(
                "INSERT INTO commissions(from_user, to_user, level, coins, source, created_at)"
                " VALUES(?, ?, ?, ?, ?, ?)",
                (user_id, up_id, level + 1, comm, source, now_iso()),
            )
        upline = db.execute(
            "SELECT referred_by FROM users WHERE id = ?", (up_id,)
        ).fetchone()
        level += 1


# ---------------------------------------------------------------- v1.7 helpers
# Falcon Tap Levels: lifetime taps unlock a tap-power bonus. Thresholds
# are fixed (Chick 0 / Hunter 100 / Elite 500 / Legend 1500); the bonus
# per level is admin-controlled via tap_level_bonus (default +1/level).
# v2.1: tap-level thresholds are a team-set setting (tap_level_thresholds,
# default "100,500,1500"). TAP_LEVELS below is the fallback only.
TAP_LEVELS = [("Chick", 0), ("Hunter", 100), ("Elite", 500), ("Legend", 1500)]


def tap_levels_for(settings):
    th = _int_list(settings.get("tap_level_thresholds"), [100, 500, 1500])[:3]
    names = ["Chick", "Hunter", "Elite", "Legend"]
    out = [("Chick", 0)] + [(names[i + 1], max(1, t)) for i, t in enumerate(th)]
    return sorted(out, key=lambda x: x[1])


def tap_level_for(lifetime_taps, bonus_per_level, settings=None):
    levels = tap_levels_for(settings) if settings else TAP_LEVELS
    idx = 0
    for i, (_name, minimum) in enumerate(levels):
        if lifetime_taps >= minimum:
            idx = i
    name, _minimum = levels[idx]
    nxt = levels[idx + 1] if idx + 1 < len(levels) else None
    return {
        "level": idx, "name": name, "lifetime_taps": lifetime_taps,
        "bonus": idx * bonus_per_level,
        "next_name": nxt[0] if nxt else None,
        "next_min": nxt[1] if nxt else None,
        "to_next": (nxt[1] - lifetime_taps) if nxt else 0,
        "progress": 100 if not nxt else min(
            100, round((lifetime_taps - levels[idx][1]) * 100 / (nxt[1] - levels[idx][1]))),
    }


def wheel_prizes(db):
    """8 honest wheel segments spread across the admin reward range."""
    s = get_settings(db)
    lo = max(0, int(s["wheel_min_reward"]))
    hi = max(lo, int(s["wheel_max_reward"]))
    prizes = []
    for i in range(8):
        prizes.append(hi if i == 7 else round(lo + (hi - lo) * i / 7))
    return prizes


INVITE_DAILY_TITLE = "Invite 1 friend today"


def _int_list(raw, fallback):
    try:
        out = [int(v.strip()) for v in str(raw).split(",") if v.strip() != ""]
        return out if out else fallback
    except (TypeError, ValueError):
        return fallback


def milestone_pairs(settings):
    th = _int_list(settings["milestone_thresholds"], [5, 10, 25, 50, 100])
    bo = _int_list(settings["milestone_bonuses"], [500, 1500, 5000, 15000, 50000])
    return [{"threshold": t, "bonus": bo[i] if i < len(bo) else 0}
            for i, t in enumerate(th)]


def growth_tiers(settings):
    return [
        {"key": "bronze", "name": "Bronze",
         "invites": int(settings["tier_bronze_invites"]),
         "limit": int(settings["tier_bronze_limit"])},
        {"key": "silver", "name": "Silver",
         "invites": int(settings["tier_silver_invites"]),
         "limit": int(settings["tier_silver_limit"])},
        {"key": "gold", "name": "Gold",
         "invites": int(settings["tier_gold_invites"]),
         "limit": int(settings["tier_gold_limit"])},
        {"key": "diamond", "name": "Diamond",
         "invites": int(settings["tier_diamond_invites"]),
         "limit": int(settings["tier_diamond_limit"])},
    ]


def valid_invite_rows(db, inviter_id):
    """Direct referrals who completed >=1 task that is NOT the auto daily
    invite task. Returns rows (id, first_valid_at) in completion order."""
    rows = db.execute(
        "SELECT u.id, MIN(tc.completed_at) AS first_at FROM users u"
        " JOIN task_completions tc ON tc.user_id = u.id"
        " JOIN tasks t ON t.id = tc.task_id"
        " WHERE u.referred_by = ? AND t.title != ?"
        " GROUP BY u.id ORDER BY first_at",
        (inviter_id, INVITE_DAILY_TITLE)).fetchall()
    return rows


def valid_invite_count(db, inviter_id):
    return len(valid_invite_rows(db, inviter_id))


def valid_invites_today(db, inviter_id):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return sum(1 for r in valid_invite_rows(db, inviter_id)
               if (r["first_at"] or "")[:10] == today)


def withdrawal_tier(db, user_id, settings):
    """Current tier dict. limit None = unlimited; Locked limit = 0."""
    gating = settings["withdrawal_gating_enabled"] == "1"
    valid = valid_invite_count(db, user_id)
    tiers = growth_tiers(settings)
    cur = None
    for t in tiers:
        if valid >= t["invites"]:
            cur = t
    nxt = next((t for t in tiers if valid < t["invites"]), None)
    return {"gating_enabled": gating, "valid": valid,
            "name": cur["name"] if cur else "Locked",
            "limit": (None if (cur and cur["limit"] == 0) else
                      (cur["limit"] if cur else 0)),
            "next": nxt, "to_next": (nxt["invites"] - valid) if nxt else 0}


def tier_error(db, user_id, settings, amount):
    """Withdrawal-tier block message, or None when allowed."""
    tier = withdrawal_tier(db, user_id, settings)
    if not tier["gating_enabled"]:
        return None
    if tier["name"] == "Locked":
        bronze = growth_tiers(settings)[0]
        return (f"Withdrawal locked - you have {tier['valid']} valid invites. "
                f"You need {bronze['invites'] - tier['valid']} more valid invites "
                f"to unlock Bronze (withdraw up to {bronze['limit']} points). "
                "A valid invite is a friend who registered with your code "
                "and completed a task.")
    if tier["limit"] is not None and amount > tier["limit"]:
        if tier["next"]:
            nxt_lim = ("unlimited" if tier["next"]["limit"] == 0
                       else str(tier["next"]["limit"]))
            return (f"Your {tier['name']} tier allows withdrawals up to "
                    f"{tier['limit']} points. You need {tier['to_next']} more "
                    f"valid invites to unlock {tier['next']['name']} "
                    f"(up to {nxt_lim} points).")
        return (f"Your {tier['name']} tier allows withdrawals up to "
                f"{tier['limit']} points.")
    return None


def award_milestones(db, inviter_id):
    """Award newly-crossed milestones exactly once (direct credit, no
    referral commission on milestone bonuses). Returns awarded pairs."""
    settings = get_settings(db)
    valid = valid_invite_count(db, inviter_id)
    awarded = []
    for pr in milestone_pairs(settings):
        row = db.execute(
            "SELECT id FROM milestone_awards WHERE user_id = ? AND threshold = ?",
            (inviter_id, pr["threshold"])).fetchone()
        if valid >= pr["threshold"] and not row:
            db.execute(
                "INSERT INTO milestone_awards(user_id, threshold, bonus, created_at)"
                " VALUES(?, ?, ?, ?)",
                (inviter_id, pr["threshold"], pr["bonus"], now_iso()))
            db.execute("UPDATE users SET coins = coins + ? WHERE id = ?",
                       (pr["bonus"], inviter_id))
            awarded.append(pr)
    return awarded


def auto_invite_task(db, inviter_id):
    """Auto-complete the daily invite task for an inviter who gained a
    valid invite today (once per day). Returns True when completed."""
    settings = get_settings(db)
    t = db.execute("SELECT * FROM tasks WHERE title = ? AND enabled = 1",
                   (INVITE_DAILY_TITLE,)).fetchone()
    if not t:
        return False
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    done = db.execute(
        "SELECT COUNT(*) FROM task_completions"
        " WHERE user_id = ? AND task_id = ? AND substr(completed_at, 1, 10) = ?",
        (inviter_id, t["id"], today)).fetchone()[0]
    if done:
        return False
    db.execute(
        "INSERT INTO task_completions(user_id, task_id, completed_at) VALUES(?, ?, ?)",
        (inviter_id, t["id"], now_iso()))
    credit_coins(db, inviter_id, int(t["reward"]), f"task:{t['title']}")
    return True


def streak_info(db, user_id, settings):
    """Login streak from daily-bonus claim dates (UTC).
    current: streak ending today, or ending yesterday if not claimed yet
    today (a missed day resets it). claim_streak: the streak the next
    claim will be paid on. Bonus = base + min(cap, per_day * streak)."""
    dates = set()
    for r in db.execute(
            "SELECT substr(claimed_at, 1, 10) AS d FROM bonus_claims WHERE user_id = ?",
            (user_id,)).fetchall():
        dates.add(r["d"])
    today = datetime.now(timezone.utc).date()
    def ending_on(day):
        count = 0
        cur = day
        while cur.isoformat() in dates:
            count += 1
            cur = cur - timedelta(days=1)
        return count
    streak_today = ending_on(today)
    streak_yday = ending_on(today - timedelta(days=1))
    has_today = today.isoformat() in dates
    current = streak_today if has_today else streak_yday
    claim_streak = streak_today if has_today else (streak_yday + 1 if streak_yday else 1)
    per_day = int(settings["streak_bonus_per_day"])
    cap = int(settings["streak_bonus_cap"])
    base = int(settings["daily_bonus_points"])
    return {"current": current, "claim_streak": claim_streak,
            "streak_bonus": min(cap, per_day * claim_streak),
            "total": base + min(cap, per_day * claim_streak),
            "base": base, "per_day": per_day, "cap": cap}


def growth_for_user(db, user_id):
    settings = get_settings(db)
    tier = withdrawal_tier(db, user_id, settings)
    valid = tier["valid"]
    total = db.execute("SELECT COUNT(*) FROM users WHERE referred_by = ?",
                       (user_id,)).fetchone()[0]
    earned = sorted({r["threshold"] for r in db.execute(
        "SELECT threshold FROM milestone_awards WHERE user_id = ?",
        (user_id,)).fetchall()} | {r["threshold"] for r in db.execute(
        "SELECT threshold FROM token_milestone_awards WHERE user_id = ?",
        (user_id,)).fetchall()})
    pairs = token_milestone_pairs(settings)
    nxt = next((pr for pr in pairs
                if valid < pr["threshold"] and pr["threshold"] not in earned),
               None)
    if nxt is None:
        nxt = next((pr for pr in pairs if pr["threshold"] not in earned), None)
    rank = inviter_rank(db, user_id, settings)
    per_invite, _r = per_invite_reward(db, user_id, settings)
    return {"valid_invites": valid, "total_invites": total,
            "valid_invites_today": valid_invites_today(db, user_id),
            "tier": {"name": tier["name"], "limit": tier["limit"],
                     "gating_enabled": tier["gating_enabled"],
                     "next": tier["next"], "to_next": tier["to_next"]},
            "milestones": {"earned": earned, "next": nxt, "pairs": pairs},
            "streak": streak_info(db, user_id, settings),
            "inviter_rank": rank, "per_invite_reward": per_invite}


def top_inviters(db, limit=10):
    out = []
    for r in db.execute(
            "SELECT id, username, coins FROM users WHERE is_admin = 0"
            " ORDER BY created_at ASC").fetchall():
        out.append({"id": r["id"], "username": r["username"],
                    "points": r["coins"],
                    "valid_invites": valid_invite_count(db, r["id"]),
                    "total_invites": db.execute(
                        "SELECT COUNT(*) FROM users WHERE referred_by = ?",
                        (r["id"],)).fetchone()[0]})
    out.sort(key=lambda x: (-x["valid_invites"], -x["points"]))
    return out[:limit]


def invite_stats(db):
    total = db.execute(
        "SELECT COUNT(*) FROM users WHERE referred_by IS NOT NULL").fetchone()[0]
    # Valid invites counted per inviter (a referred user counts once, for
    # their direct inviter).
    valid = 0
    for r in db.execute("SELECT DISTINCT referred_by FROM users WHERE referred_by IS NOT NULL").fetchall():
        valid += valid_invite_count(db, r["referred_by"])
    return {"total_invites": total, "valid_invites": valid,
            "conversion_rate": round(valid * 100.0 / total, 1) if total else 0.0}


def today_earnings(db, user_id):
    """Today's points by source (UTC day, server clock). Mining claims
    are excluded here — they are dated by claim, not by earning."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    like = today + "%"
    tap = db.execute(
        "SELECT COALESCE(SUM(points),0) FROM tap_events"
        " WHERE user_id = ? AND created_at LIKE ?", (user_id, like)).fetchone()[0]
    tasks = db.execute(
        "SELECT COALESCE(SUM(t.reward),0) FROM task_completions tc"
        " JOIN tasks t ON t.id = tc.task_id"
        " WHERE tc.user_id = ? AND tc.completed_at LIKE ?", (user_id, like)).fetchone()[0]
    bonus = db.execute(
        "SELECT COALESCE(SUM(points),0) FROM bonus_claims"
        " WHERE user_id = ? AND claimed_at LIKE ?", (user_id, like)).fetchone()[0]
    ads = db.execute(
        "SELECT COALESCE(SUM(points),0) FROM ad_rewards"
        " WHERE user_id = ? AND created_at LIKE ?", (user_id, like)).fetchone()[0]
    wheel = db.execute(
        "SELECT COALESCE(SUM(reward),0) FROM wheel_spins"
        " WHERE user_id = ? AND created_at LIKE ?", (user_id, like)).fetchone()[0]
    refs = db.execute(
        "SELECT COALESCE(SUM(coins),0) FROM commissions"
        " WHERE to_user = ? AND created_at LIKE ?", (user_id, like)).fetchone()[0]
    total = tap + tasks + bonus + ads + wheel + refs
    return {"total": total, "tap": tap, "tasks": tasks, "bonus": bonus,
            "ads": ads, "wheel": wheel, "referrals": refs}



# ---------------------------------------------------------------- v1.9 helpers
def _fval(raw, fallback):
    try:
        v = float(raw)
        return v if v == v and abs(v) != float("inf") else fallback
    except (TypeError, ValueError):
        return fallback


def _float_list(raw, fallback):
    try:
        out = [float(v.strip()) for v in str(raw).split(",") if v.strip() != ""]
        return out if out else fallback
    except (TypeError, ValueError):
        return fallback


def credit_tokens(db, user_id, amount, source, note=""):
    """Credit GLD tokens + append a ledger row. No commissions on tokens."""
    amount = round(float(amount), 4)
    if amount <= 0:
        return 0.0
    db.execute("UPDATE users SET coin_balance = coin_balance + ? WHERE id = ?",
               (amount, user_id))
    db.execute(
        "INSERT INTO token_ledger(user_id, amount, source, note, created_at)"
        " VALUES(?, ?, ?, ?, ?)", (user_id, amount, source, note[:200], now_iso()))
    return amount


def debit_tokens(db, user_id, amount, source, note=""):
    amount = round(float(amount), 4)
    if amount <= 0:
        return 0.0
    db.execute("UPDATE users SET coin_balance = coin_balance - ? WHERE id = ?",
               (amount, user_id))
    db.execute(
        "INSERT INTO token_ledger(user_id, amount, source, note, created_at)"
        " VALUES(?, ?, ?, ?, ?)", (user_id, -amount, source, note[:200], now_iso()))
    return amount


def token_milestone_pairs(settings):
    th = _int_list(settings["milestone_thresholds"], [5, 10, 25, 50, 100])
    bo = _float_list(settings["milestone_token_bonuses"], [0.5, 1.5, 5, 15, 50])
    return [{"threshold": t, "bonus": bo[i] if i < len(bo) else 0.0}
            for i, t in enumerate(th)]


INVITER_RANKS = ["Starter", "Promoter", "Influencer", "Ambassador"]


def inviter_rank(db, user_id, settings):
    """Rank + multiplier from valid-invite count (thresholds admin-set)."""
    valid = valid_invite_count(db, user_id)
    th = _int_list(settings["inviter_rank_thresholds"], [10, 25, 50])
    mu = _float_list(settings["inviter_rank_multipliers"], [1.1, 1.25, 1.5])
    idx = 0
    for i, t in enumerate(th):
        if valid >= t:
            idx = i + 1
    mult = 1.0 if idx == 0 else (mu[idx - 1] if idx - 1 < len(mu) else mu[-1])
    nxt = th[idx] if idx < len(th) else None
    return {"name": INVITER_RANKS[idx], "index": idx, "multiplier": mult,
            "valid": valid, "next_threshold": nxt,
            "to_next": (nxt - valid) if nxt else 0}


def per_invite_reward(db, user_id, settings):
    base = _fval(settings["invite_token_reward"], 0.2)
    rank = inviter_rank(db, user_id, settings)
    return round(base * rank["multiplier"], 4), rank


def award_invite_tokens(db, inviter_id):
    """Per-valid-invite token reward (rank-boosted). Called once, when a
    referral completes their first qualifying task."""
    settings = get_settings(db)
    amount, rank = per_invite_reward(db, inviter_id, settings)
    if amount > 0:
        credit_tokens(db, inviter_id, amount, "invite_reward",
                      f"valid invite (rank {rank['name']} x{rank['multiplier']})")
    return {"amount": amount, "rank": rank["name"], "multiplier": rank["multiplier"]}


def award_token_milestones(db, inviter_id):
    """Token milestones, exactly once each. A threshold already paid in
    v1.8 points is never re-paid in tokens (legacy rows are checked too)."""
    settings = get_settings(db)
    valid = valid_invite_count(db, inviter_id)
    awarded = []
    for pr in token_milestone_pairs(settings):
        legacy = db.execute(
            "SELECT id FROM milestone_awards WHERE user_id = ? AND threshold = ?",
            (inviter_id, pr["threshold"])).fetchone()
        done = db.execute(
            "SELECT id FROM token_milestone_awards WHERE user_id = ? AND threshold = ?",
            (inviter_id, pr["threshold"])).fetchone()
        if valid >= pr["threshold"] and not legacy and not done:
            db.execute(
                "INSERT INTO token_milestone_awards(user_id, threshold, bonus_tokens, created_at)"
                " VALUES(?, ?, ?, ?)",
                (inviter_id, pr["threshold"], pr["bonus"], now_iso()))
            if pr["bonus"] > 0:
                credit_tokens(db, inviter_id, pr["bonus"], "invite_milestone",
                              f"milestone at {pr['threshold']} valid invites")
            awarded.append(pr)
    return awarded


def week_monday(d):
    """Monday (UTC date) of the week containing date d."""
    return d - timedelta(days=d.weekday())


def week_bounds(monday_date):
    start = datetime(monday_date.year, monday_date.month, monday_date.day,
                     tzinfo=timezone.utc)
    return start, start + timedelta(days=7)


WEEKLY_SPLIT_PCTS = [25, 18, 14, 11, 9, 7, 5, 4, 4, 3]


def weekly_shares(pool, split_type, winners, settings=None):
    """Shares summing EXACTLY to pool (last winner absorbs rounding).
    Weighted: team-set pcts (weekly_pool_pcts); fewer than 10 winners ->
    pcts renormalised so the pool still pays out in full. Equal: even."""
    n = len(winners)
    if n == 0 or pool <= 0:
        return []
    if split_type == "equal":
        raw = [pool / n] * n
    else:
        pcts = (_float_list(settings.get("weekly_pool_pcts"), WEEKLY_SPLIT_PCTS)
                if settings else WEEKLY_SPLIT_PCTS)[:n]
        tot = sum(pcts)
        raw = [pool * p / tot for p in pcts]
    shares = [round(x, 4) for x in raw]
    shares[-1] = round(pool - sum(shares[:-1]), 4)
    return shares


def weekly_valid_invites(db, monday_date):
    """Valid invites gained during the week starting Monday (UTC).
    Returns list of {user_id, count, reached_at} sorted by count desc,
    then by who reached that count first (deterministic tie-break)."""
    start, end = week_bounds(monday_date)
    out = []
    for r in db.execute(
            "SELECT id FROM users WHERE is_admin = 0").fetchall():
        firsts = sorted(
            (parse_iso(x["first_at"]) for x in valid_invite_rows(db, r["id"])),
            key=lambda d: d or datetime.min.replace(tzinfo=timezone.utc))
        in_week = [d for d in firsts if d and start <= d < end]
        if in_week:
            out.append({"user_id": r["id"], "count": len(in_week),
                        "reached_at": in_week[-1]})
    out.sort(key=lambda x: (-x["count"], x["reached_at"], x["user_id"]))
    return out


def distribute_weekly_pool(db, settings):
    """Distribute every completed week not yet distributed (catch-up safe).
    Returns list of distribution summaries created on this call."""
    pool = _fval(settings["weekly_pool_gld"], 100)
    split_type = settings["weekly_pool_split"] or "weighted"
    today = datetime.now(timezone.utc).date()
    last_completed = week_monday(today) - timedelta(days=7)
    created = []
    monday = week_monday(datetime.now(timezone.utc).date() - timedelta(days=365))
    # Only scan back 8 weeks: older weeks were pre-v1.9 and are not paid.
    monday = max(monday, last_completed - timedelta(days=7 * 8))
    while monday <= last_completed:
        exists = db.execute(
            "SELECT id FROM weekly_distributions WHERE week_start = ?",
            (monday.isoformat(),)).fetchone()
        if not exists:
            winners = weekly_valid_invites(db, monday)[:10]
            if not winners:
                monday += timedelta(days=7)
                continue
            shares = weekly_shares(pool, split_type, winners, settings)
            cur = db.execute(
                "INSERT INTO weekly_distributions(week_start, pool, split_type, created_at)"
                " VALUES(?, ?, ?, ?)", (monday.isoformat(), pool, split_type, now_iso()))
            dist_id = cur.lastrowid
            total = 0.0
            for w, share in zip(winners, shares):
                db.execute(
                    "INSERT INTO weekly_payouts(distribution_id, user_id, rank, valid_invites, share, created_at)"
                    " VALUES(?, ?, ?, ?, ?, ?)",
                    (dist_id, w["user_id"], winners.index(w) + 1, w["count"], share, now_iso()))
                if share > 0:
                    credit_tokens(db, w["user_id"], share, "weekly_pool",
                                  f"weekly top-10 rank {winners.index(w) + 1} week of {monday.isoformat()}")
                total += share
            created.append({"week_start": monday.isoformat(), "pool": pool,
                            "split_type": split_type, "winners": len(winners),
                            "total_paid": round(total, 4)})
        monday += timedelta(days=7)
    return created


def weekly_pool_view(db, user_id, settings):
    """Current-week standings + countdown + last distribution."""
    distribute_weekly_pool(db, settings)
    pool = _fval(settings["weekly_pool_gld"], 100)
    split_type = settings["weekly_pool_split"] or "weighted"
    today = datetime.now(timezone.utc).date()
    monday = week_monday(today)
    _s, end = week_bounds(monday)
    winners = weekly_valid_invites(db, monday)[:10]
    shares = weekly_shares(pool, split_type, winners, settings)
    top = []
    for i, w in enumerate(winners):
        u = db.execute("SELECT username FROM users WHERE id = ?",
                       (w["user_id"],)).fetchone()
        top.append({"rank": i + 1, "user_id": w["user_id"],
                    "username": u["username"] if u else "?",
                    "valid_invites": w["count"],
                    "estimated_share": shares[i] if i < len(shares) else 0})
    my = next((t for t in top if t["user_id"] == user_id), None)
    last = db.execute(
        "SELECT * FROM weekly_distributions ORDER BY week_start DESC LIMIT 1").fetchone()
    last_winners = []
    if last:
        for p in db.execute(
                "SELECT wp.*, u.username FROM weekly_payouts wp"
                " JOIN users u ON u.id = wp.user_id"
                " WHERE wp.distribution_id = ? ORDER BY wp.rank", (last["id"],)).fetchall():
            last_winners.append({"rank": p["rank"], "username": p["username"],
                                 "valid_invites": p["valid_invites"], "share": p["share"]})
    return {"pool_gld": pool, "split_type": split_type,
            "week_start": monday.isoformat(),
            "week_end": (end - timedelta(seconds=1)).isoformat(),
            "seconds_to_week_end": max(
                0, int((end - datetime.now(timezone.utc)).total_seconds())),
            "top10": top,
            "my_rank": my["rank"] if my else None,
            "my_valid_invites": my["valid_invites"] if my else 0,
            "my_estimated_share": my["estimated_share"] if my else 0,
            "last_distribution": ({"week_start": last["week_start"], "pool": last["pool"],
                                   "split_type": last["split_type"],
                                   "winners": last_winners} if last else None)}


def live_stats(db):
    now = datetime.now(timezone.utc)
    rows = db.execute(
        "SELECT last_active, created_at FROM users WHERE is_admin = 0").fetchall()
    online = 0
    active24 = 0
    for r in rows:
        la = parse_iso(r["last_active"]) if r["last_active"] else None
        if la and (now - la).total_seconds() <= 300:
            online += 1
        if la and (now - la).total_seconds() <= 86400:
            active24 += 1
    today = now.strftime("%Y-%m-%d")
    new_today = sum(1 for r in rows if (r["created_at"] or "")[:10] == today)
    return {"online_now": online, "active_24h": active24,
            "total_users": len(rows), "new_today": new_today}


def social_credit(db, user_id, verified_views, settings):
    """Token credit for verified views, with per-post + weekly caps.
    Returns (amount, capped_by) — capped_by is None/'post'/'week'."""
    rate = _fval(settings["social_rate_gld_per_1000"], 0.5)
    per_post = _fval(settings["social_cap_per_post"], 50)
    per_week = _fval(settings["social_cap_per_week"], 100)
    raw = (int(verified_views) // 1000) * rate
    amount = min(raw, per_post)
    capped = "post" if raw > per_post else None
    monday = week_monday(datetime.now(timezone.utc).date())
    start, end = week_bounds(monday)
    week_total = db.execute(
        "SELECT COALESCE(SUM(token_amount),0) FROM social_posts"
        " WHERE user_id = ? AND status = 'approved'"
        " AND decided_at >= ? AND decided_at < ?",
        (user_id, start.isoformat(), end.isoformat())).fetchone()[0]
    remaining = max(0.0, per_week - week_total)
    if amount > remaining:
        amount = remaining
        capped = "week"
    return round(amount, 4), capped


def pushed_tasks_active(db, user_id):
    """Active, unexpired pushed tasks with the caller's claim state."""
    now = datetime.now(timezone.utc)
    out = []
    for t in db.execute(
            "SELECT * FROM pushed_tasks WHERE active = 1 ORDER BY created_at DESC").fetchall():
        created = parse_iso(t["created_at"])
        expiry = created + timedelta(hours=float(t["expiry_hours"]))
        if now >= expiry:
            continue
        claimed = db.execute(
            "SELECT id FROM pushed_claims WHERE user_id = ? AND task_id = ?",
            (user_id, t["id"])).fetchone()
        out.append({"id": t["id"], "title": t["title"],
                    "description": t["description"],
                    "reward_value": t["reward_value"],
                    "reward_type": t["reward_type"],
                    "expires_at": expiry.isoformat(),
                    "seconds_left": max(0, int((expiry - now).total_seconds())),
                    "claimed": bool(claimed)})
    return out


# ---------------------------------------------------------------- pages
@app.route("/")
def index():
    return send_from_directory("templates", "index.html")


@app.route("/admin")
def admin_page():
    return send_from_directory("templates", "index.html")


# ---------------------------------------------------------------- auth API
@app.route("/api/register", methods=["POST"])
def api_register():
    db = get_db()
    d = request.get_json(force=True, silent=True) or {}
    username = (d.get("username") or "").strip()
    password = d.get("password") or ""
    ref_code = (d.get("referral_code") or "").strip().upper()
    if len(username) < 3 or len(password) < 4:
        return jsonify({"ok": False, "error": "username min 3 chars, password min 4"}), 400
    if db.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone():
        return jsonify({"ok": False, "error": "username taken"}), 400
    referred_by = None
    if ref_code:
        ref = db.execute(
            "SELECT id FROM users WHERE referral_code = ?", (ref_code,)
        ).fetchone()
        if ref:
            referred_by = ref["id"]
    code = make_referral_code(username)
    cur = db.execute(
        "INSERT INTO users(username, password_hash, coins, referral_code, referred_by, created_at)"
        " VALUES(?, ?, 0, ?, ?, ?)",
        (username, generate_password_hash(password), code, referred_by, now_iso()),
    )
    db.commit()
    session["uid"] = cur.lastrowid
    return jsonify({"ok": True, "user": user_public(
        db.execute("SELECT * FROM users WHERE id = ?", (cur.lastrowid,)).fetchone())})


@app.route("/api/login", methods=["POST"])
def api_login():
    db = get_db()
    d = request.get_json(force=True, silent=True) or {}
    u = db.execute(
        "SELECT * FROM users WHERE username = ?", ((d.get("username") or "").strip(),)
    ).fetchone()
    if not u or not check_password_hash(u["password_hash"], d.get("password") or ""):
        return jsonify({"ok": False, "error": "wrong username or password"}), 401
    if u["is_banned"]:
        return jsonify({"ok": False, "error": "account banned"}), 403
    session["uid"] = u["id"]
    return jsonify({"ok": True, "user": user_public(u)})


@app.route("/api/logout", methods=["POST"])
def api_logout():
    session.clear()
    return jsonify({"ok": True})


@app.route("/api/me")
def api_me():
    db = get_db()
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        u = token_user(db)
        if not u:
            return jsonify({"ok": False, "error": "invalid token"}), 401
        if u["is_banned"]:
            return jsonify({"ok": False, "error": "account banned"}), 403
    else:
        u = current_user(db)
    if not u:
        return jsonify({"ok": True, "user": None})
    s = get_settings(db)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    taps = db.execute(
        "SELECT COUNT(*), COALESCE(SUM(points),0) FROM tap_events"
        " WHERE user_id = ? AND substr(created_at, 1, 10) = ?",
        (u["id"], today)).fetchone()
    wrows = db.execute(
        "SELECT COUNT(*), MAX(created_at) FROM ad_rewards"
        " WHERE user_id = ? AND substr(created_at, 1, 10) = ?",
        (u["id"], today)).fetchone()
    last_bonus = db.execute(
        "SELECT MAX(claimed_at) FROM bonus_claims WHERE user_id = ?",
        (u["id"],)).fetchone()[0]
    cd = float(s["daily_bonus_cooldown_hours"])
    si_me = streak_info(db, u["id"], s)
    bonus = {"can_claim": True, "points": si_me["total"],
             "base_points": si_me["base"], "streak_bonus": si_me["streak_bonus"],
             "streak_days": si_me["claim_streak"],
             "cooldown_hours": cd, "next_available_at": None}
    if last_bonus:
        nxt = parse_iso(last_bonus) + timedelta(hours=cd)
        if datetime.now(timezone.utc) < nxt:
            bonus["can_claim"] = False
            bonus["next_available_at"] = nxt.isoformat()
    lifetime = db.execute(
        "SELECT COUNT(*) FROM tap_events WHERE user_id = ?",
        (u["id"],)).fetchone()[0]
    level = tap_level_for(lifetime, int(s["tap_level_bonus"]), s)
    wrows_today = db.execute(
        "SELECT COUNT(*), MAX(created_at) FROM wheel_spins"
        " WHERE user_id = ? AND substr(created_at, 1, 10) = ?",
        (u["id"], today)).fetchone()
    return jsonify({
        "ok": True, "user": user_public(u),
        "tap_today": {"taps": taps[0], "points": taps[1]},
        "watch_today": {"watched": wrows[0], "last_at": wrows[1]},
        "bonus": bonus,
        "lifetime_taps": lifetime,
        "tap_level": level,
        "today_earnings": today_earnings(db, u["id"]),
        "wheel_today": {"spun": wrows_today[0], "last_at": wrows_today[1]},
        "growth": growth_for_user(db, u["id"]),
        "token_history": [dict(r) for r in db.execute(
            "SELECT amount, source, note, created_at FROM token_ledger"
            " WHERE user_id = ? ORDER BY created_at DESC LIMIT 30",
            (u["id"],)).fetchall()],
    })


# ---------------------------------------------------------------- user API
@app.route("/api/dashboard")
@login_required
def api_dashboard():
    db = get_db()
    u = current_user(db)
    s = get_settings(db)
    earned = db.execute(
        "SELECT COALESCE(SUM(coins),0) FROM commissions WHERE to_user = ?",
        (u["id"],)).fetchone()[0]
    refs = db.execute(
        "SELECT COUNT(*) FROM users WHERE referred_by = ?", (u["id"],)).fetchone()[0]
    pend = db.execute(
        "SELECT COUNT(*) FROM withdrawals WHERE user_id = ? AND status = 'pending'",
        (u["id"],)).fetchone()[0]
    return jsonify({
        "ok": True,
        "points": u["coins"],
        "coin_balance": u["coin_balance"],
        "coin_name": s["coin_name"],
        "coin_symbol": s["coin_symbol"],
        "points_per_coin": s["points_per_coin"],
        "min_convert_points": s["min_convert_points"],
        "withdrawals_enabled": s["withdrawals_enabled"] == "1",
        "commission_earned": earned,
        "referrals": refs,
        "pending_withdrawals": pend,
        "referral_code": u["referral_code"],
        "settings": {
            "mining_enabled": s["mining_enabled"] == "1",
            "coins_per_hour": s["coins_per_hour"],
            "max_mining_hours": s["max_mining_hours"],
            "min_claim_interval_min": s["min_claim_interval_min"],
            "app_name": s["app_name"],
        },
    })


def mining_status_for(db, u):
    """Mining status dict shared by /api/mining/status and /api/mining."""
    s = get_settings(db)
    row = db.execute(
        "SELECT * FROM mining_sessions WHERE user_id = ? AND claimed_at IS NULL"
        " ORDER BY started_at DESC LIMIT 1", (u["id"],)).fetchone()
    last_claim = db.execute(
        "SELECT MAX(claimed_at) FROM mining_sessions"
        " WHERE user_id = ? AND claimed_at IS NOT NULL", (u["id"],)).fetchone()[0]
    interval = float(s["min_claim_interval_min"])
    wait_min = 0
    if last_claim:
        mins = (datetime.now(timezone.utc) - parse_iso(last_claim)).total_seconds() / 60.0
        wait_min = max(0.0, interval - mins)
    out = {"ok": True, "active": False, "min_claim_interval_min": interval,
           "last_claim_at": last_claim, "claim_wait_minutes": round(wait_min, 1),
           "can_claim": wait_min <= 0}
    if not row:
        return out
    start = parse_iso(row["started_at"])
    elapsed_h = (datetime.now(timezone.utc) - start).total_seconds() / 3600.0
    elapsed_h = min(elapsed_h, float(s["max_mining_hours"]))
    accrued = int(elapsed_h * float(s["coins_per_hour"]))
    out.update({
        "active": True, "started_at": row["started_at"],
        "elapsed_hours": round(elapsed_h, 2), "accrued_coins": accrued,
        "rate": float(s["coins_per_hour"]),
        "max_hours": float(s["max_mining_hours"]),
    })
    return out


@app.route("/api/mining/status")
@login_required
def api_mining_status():
    db = get_db()
    return jsonify(mining_status_for(db, current_user(db)))


@app.route("/api/mining/start", methods=["POST"])
@login_required
def api_mining_start():
    db = get_db()
    u = current_user(db)
    s = get_settings(db)
    if s["mining_enabled"] != "1":
        return jsonify({"ok": False, "error": "mining is disabled by admin"}), 403
    active = db.execute(
        "SELECT id FROM mining_sessions WHERE user_id = ? AND claimed_at IS NULL",
        (u["id"],)).fetchone()
    if active:
        return jsonify({"ok": False, "error": "mining already running"}), 400
    db.execute(
        "INSERT INTO mining_sessions(user_id, started_at) VALUES(?, ?)",
        (u["id"], now_iso()))
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/mining/claim", methods=["POST"])
@login_required
def api_mining_claim():
    db = get_db()
    u = current_user(db)
    s = get_settings(db)
    row = db.execute(
        "SELECT * FROM mining_sessions WHERE user_id = ? AND claimed_at IS NULL"
        " ORDER BY started_at DESC LIMIT 1", (u["id"],)).fetchone()
    if not row:
        return jsonify({"ok": False, "error": "no active mining session"}), 400
    interval = float(s["min_claim_interval_min"])
    last_claim = db.execute(
        "SELECT MAX(claimed_at) FROM mining_sessions"
        " WHERE user_id = ? AND claimed_at IS NOT NULL", (u["id"],)).fetchone()[0]
    if last_claim:
        mins = (datetime.now(timezone.utc) - parse_iso(last_claim)).total_seconds() / 60.0
        if mins < interval:
            return jsonify({"ok": False,
                            "error": f"wait {round(interval - mins, 1)} more minutes before next claim"}), 400
    start = parse_iso(row["started_at"])
    elapsed_h = (datetime.now(timezone.utc) - start).total_seconds() / 3600.0
    elapsed_h = min(elapsed_h, float(s["max_mining_hours"]))
    accrued = int(elapsed_h * float(s["coins_per_hour"]))
    if accrued > 0:
        credit_coins(db, u["id"], accrued, "mining")
    db.execute(
        "UPDATE mining_sessions SET claimed_at = ?, coins_claimed = ? WHERE id = ?",
        (now_iso(), accrued, row["id"]))
    db.commit()
    return jsonify({"ok": True, "claimed": accrued})


def task_availability(db, user_id, task):
    """Return (available, next_available_at_iso, remaining_minutes)."""
    last = db.execute(
        "SELECT MAX(completed_at) FROM task_completions"
        " WHERE user_id = ? AND task_id = ?", (user_id, task["id"])).fetchone()[0]
    if not last:
        return True, None, 0
    cd = float(task["cooldown_hours"])
    nxt = parse_iso(last) + timedelta(hours=cd)
    remaining = (nxt - datetime.now(timezone.utc)).total_seconds() / 60.0
    if remaining <= 0:
        return True, None, 0
    return False, nxt.isoformat(), round(remaining, 1)


def task_daily_count(db, user_id, task_id):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return db.execute(
        "SELECT COUNT(*) FROM task_completions"
        " WHERE user_id = ? AND task_id = ? AND substr(completed_at, 1, 10) = ?",
        (user_id, task_id, today)).fetchone()[0]


@app.route("/api/tasks")
@login_required
def api_tasks():
    db = get_db()
    u = current_user(db)
    rows = db.execute(
        "SELECT * FROM tasks WHERE enabled = 1 ORDER BY id").fetchall()
    out = []
    for t in rows:
        avail, nxt, rem = task_availability(db, u["id"], t)
        dl = t["daily_limit"] if "daily_limit" in t.keys() else 0
        done_today = task_daily_count(db, u["id"], t["id"])
        limit_reached = dl > 0 and done_today >= dl
        out.append({
            "id": t["id"], "title": t["title"], "description": t["description"],
            "reward": t["reward"], "task_type": t["task_type"],
            "category": t["task_type"],
            "cooldown_hours": t["cooldown_hours"],
            "daily_limit": dl, "completed_today": done_today,
            "daily_limit_reached": limit_reached,
            "available": avail and not limit_reached,
            "next_available_at": nxt,
            "cooldown_remaining_minutes": rem,
        })
    return jsonify({"ok": True, "tasks": out})


def interstitial_for(db, user_id):
    """Return an enabled interstitial provider if the frequency rule fires."""
    s = get_settings(db)
    try:
        n = int(s["interstitial_every_n_tasks"])
    except (TypeError, ValueError):
        n = 0
    if n <= 0:
        return None
    total = db.execute(
        "SELECT COUNT(*) FROM task_completions WHERE user_id = ?",
        (user_id,)).fetchone()[0]
    if total % n != 0:
        return None
    prov = db.execute(
        "SELECT * FROM ad_providers WHERE enabled = 1 AND placement_type = 'interstitial'"
        " ORDER BY id LIMIT 1").fetchone()
    return dict(prov) if prov else None


def complete_task_for(db, u, task_id):
    """Shared task-completion logic (web route + REST route).

    Returns (payload_dict, http_status). Enforces: task exists + enabled,
    cooldown, and per-task daily limit. Commissions pay via credit_coins.
    """
    t = db.execute("SELECT * FROM tasks WHERE id = ? AND enabled = 1",
                   (task_id,)).fetchone()
    if not t:
        return {"ok": False, "error": "task not found or disabled"}, 404
    # v1.8: the daily invite task is never completable by hand - it only
    # completes automatically when the user gains a valid invite today.
    if t["title"] == INVITE_DAILY_TITLE and valid_invites_today(db, u["id"]) < 1:
        return {"ok": False,
                "error": "Invite a friend today first - this task completes "
                         "automatically when a friend you invited completes "
                         "their first task"}, 400
    avail, nxt, rem = task_availability(db, u["id"], t)
    if not avail:
        return {"ok": False,
                "error": f"on cooldown, available in {rem} minutes"}, 400
    dl = t["daily_limit"] if "daily_limit" in t.keys() else 0
    if dl > 0 and task_daily_count(db, u["id"], t["id"]) >= dl:
        return {"ok": False, "error": "daily limit reached for this task"}, 400
    # Was this the user's first qualifying (non-invite-task) completion?
    was_first_valid = (t["title"] != INVITE_DAILY_TITLE and
                       db.execute(
                           "SELECT COUNT(*) FROM task_completions tc"
                           " JOIN tasks tt ON tt.id = tc.task_id"
                           " WHERE tc.user_id = ? AND tt.title != ?",
                           (u["id"], INVITE_DAILY_TITLE)).fetchone()[0] == 0)
    inviter_id = u["referred_by"]
    db.execute(
        "INSERT INTO task_completions(user_id, task_id, completed_at) VALUES(?, ?, ?)",
        (u["id"], t["id"], now_iso()))
    credit_coins(db, u["id"], t["reward"], f"task:{t['title']}")
    milestones = []
    invite_token = None
    invite_task_done = False
    if was_first_valid and inviter_id:
        # v1.9: the inviter earns TOKENS (rank-boosted) + token milestones.
        # Points commissions are unchanged (credit_coins above).
        invite_token = award_invite_tokens(db, inviter_id)
        milestones = award_token_milestones(db, inviter_id)
        invite_task_done = auto_invite_task(db, inviter_id)
    db.commit()
    inter = interstitial_for(db, u["id"])
    return {"ok": True, "reward": t["reward"],
            "show_interstitial": bool(inter), "interstitial": inter,
            "milestones": milestones, "invite_token": invite_token,
            "invite_task_done": invite_task_done}, 200


@app.route("/api/tasks/complete", methods=["POST"])
@login_required
def api_tasks_complete():
    db = get_db()
    u = current_user(db)
    d = request.get_json(force=True, silent=True) or {}
    payload, code = complete_task_for(db, u, d.get("task_id"))
    return jsonify(payload), code


@app.route("/api/bonus/status")
@login_required
def api_bonus_status():
    db = get_db()
    u = current_user(db)
    s = get_settings(db)
    last = db.execute(
        "SELECT MAX(claimed_at) FROM bonus_claims WHERE user_id = ?",
        (u["id"],)).fetchone()[0]
    cd = float(s["daily_bonus_cooldown_hours"])
    si = streak_info(db, u["id"], s)
    if not last:
        return jsonify({"ok": True, "can_claim": True, "points": si["total"],
                        "base_points": si["base"], "streak_bonus": si["streak_bonus"],
                        "streak_days": si["claim_streak"],
                        "cooldown_hours": cd, "next_available_at": None})
    nxt = parse_iso(last) + timedelta(hours=cd)
    rem = (nxt - datetime.now(timezone.utc)).total_seconds() / 3600.0
    if rem <= 0:
        return jsonify({"ok": True, "can_claim": True, "points": si["total"],
                        "base_points": si["base"], "streak_bonus": si["streak_bonus"],
                        "streak_days": si["claim_streak"],
                        "cooldown_hours": cd, "next_available_at": None})
    return jsonify({"ok": True, "can_claim": False, "points": si["total"],
                    "base_points": si["base"], "streak_bonus": si["streak_bonus"],
                    "streak_days": si["claim_streak"],
                    "cooldown_hours": cd, "next_available_at": nxt.isoformat(),
                    "cooldown_remaining_hours": round(rem, 1)})


@app.route("/api/bonus/claim", methods=["POST"])
@login_required
def api_bonus_claim():
    db = get_db()
    u = current_user(db)
    s = get_settings(db)
    last = db.execute(
        "SELECT MAX(claimed_at) FROM bonus_claims WHERE user_id = ?",
        (u["id"],)).fetchone()[0]
    cd = float(s["daily_bonus_cooldown_hours"])
    if last:
        nxt = parse_iso(last) + timedelta(hours=cd)
        if datetime.now(timezone.utc) < nxt:
            return jsonify({"ok": False, "error": "daily bonus on cooldown"}), 400
    si = streak_info(db, u["id"], s)
    pts = si["total"]
    db.execute(
        "INSERT INTO bonus_claims(user_id, points, claimed_at) VALUES(?, ?, ?)",
        (u["id"], pts, now_iso()))
    credit_coins(db, u["id"], pts, "daily_bonus")
    db.commit()
    return jsonify({"ok": True, "points": pts, "base_points": si["base"],
                    "streak_bonus": si["streak_bonus"],
                    "streak_days": si["claim_streak"]})


@app.route("/api/convert", methods=["POST"])
@login_required
def api_convert():
    db = get_db()
    u = current_user(db)
    s = get_settings(db)
    d = request.get_json(force=True, silent=True) or {}
    try:
        pts = int(d.get("points") or 0)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "bad amount"}), 400
    min_pts = int(s["min_convert_points"])
    rate = float(s["points_per_coin"])
    if pts < min_pts:
        return jsonify({"ok": False, "error": f"minimum conversion is {min_pts} points"}), 400
    if u["coins"] < pts:
        return jsonify({"ok": False, "error": "not enough points"}), 400
    coins_out = round(pts / rate, 4)
    if coins_out <= 0:
        return jsonify({"ok": False, "error": "amount too small for this rate"}), 400
    db.execute("UPDATE users SET coins = coins - ?, coin_balance = coin_balance + ? WHERE id = ?",
               (pts, coins_out, u["id"]))
    db.execute(
        "INSERT INTO conversions(user_id, points_spent, coins_received, created_at)"
        " VALUES(?, ?, ?, ?)", (u["id"], pts, coins_out, now_iso()))
    db.commit()
    return jsonify({"ok": True, "coins_received": coins_out,
                    "coin_symbol": s["coin_symbol"]})


@app.route("/api/conversions")
@login_required
def api_conversions():
    db = get_db()
    u = current_user(db)
    rows = db.execute(
        "SELECT * FROM conversions WHERE user_id = ? ORDER BY created_at DESC LIMIT 50",
        (u["id"],)).fetchall()
    return jsonify({"ok": True, "conversions": [dict(r) for r in rows]})


@app.route("/api/referrals")
@login_required
def api_referrals():
    db = get_db()
    u = current_user(db)
    refs = db.execute(
        "SELECT id, username, coins, created_at FROM users WHERE referred_by = ?"
        " ORDER BY created_at DESC", (u["id"],)).fetchall()
    comms = db.execute(
        "SELECT c.level, c.coins, c.source, c.created_at, u.username AS from_user"
        " FROM commissions c JOIN users u ON u.id = c.from_user"
        " WHERE c.to_user = ? ORDER BY c.created_at DESC LIMIT 50",
        (u["id"],)).fetchall()
    return jsonify({
        "ok": True,
        "referral_code": u["referral_code"],
        "referrals": [{"id": r["id"], "username": r["username"], "points": r["coins"],
                       "created_at": r["created_at"]} for r in refs],
        "commissions": [dict(r) for r in comms],
    })


@app.route("/api/withdraw", methods=["POST"])
@login_required
def api_withdraw():
    db = get_db()
    u = current_user(db)
    s = get_settings(db)
    if s["withdrawals_enabled"] != "1":
        return jsonify({"ok": False, "error": "withdrawals are coming soon"}), 403
    d = request.get_json(force=True, silent=True) or {}
    method = (d.get("method") or "").strip()
    account = (d.get("account") or "").strip()
    try:
        amount_points = int(d.get("amount_points") or 0)
    except (TypeError, ValueError):
        amount_points = 0
    if method not in ("JazzCash", "Easypaisa", "Bank Transfer",
                     "Mobile Wallet", "Crypto Wallet"):
        return jsonify({"ok": False, "error": "invalid method"}), 400
    if not account:
        return jsonify({"ok": False, "error": "account number required"}), 400
    # v1.1 (v22): team-toggled withdrawal methods (JazzCash/Easypaisa are
    # web-only and stay always available; the three app methods toggle).
    _method_key = {"Bank Transfer": "withdrawal_method_bank",
                   "Mobile Wallet": "withdrawal_method_mobile_wallet",
                   "Crypto Wallet": "withdrawal_method_crypto"}.get(method)
    if _method_key and s[_method_key] != "1":
        return jsonify({"ok": False,
                        "error": "this withdrawal method is currently paused"}), 400
    # v1.8 invite-gated tiers, checked before the minimum so a locked user
    # always sees the tier message (the growth reason to invite).
    terr = tier_error(db, u["id"], s, amount_points)
    if terr:
        return jsonify({"ok": False, "error": terr}), 403
    if amount_points < int(s["min_withdrawal_points"]):
        return jsonify({"ok": False,
                        "error": f"minimum withdrawal is {s['min_withdrawal_points']} points"}), 400
    if u["coins"] < amount_points:
        return jsonify({"ok": False, "error": "not enough points"}), 400
    # Deduct at request time (held); refunded if admin rejects.
    db.execute("UPDATE users SET coins = coins - ? WHERE id = ?", (amount_points, u["id"]))
    db.execute(
        "INSERT INTO withdrawals(user_id, method, account, amount_pkr, amount_points, coins_used, status, created_at)"
        " VALUES(?, ?, ?, 0, ?, ?, 'pending', ?)",
        (u["id"], method, account, amount_points, amount_points, now_iso()))
    db.commit()
    return jsonify({"ok": True, "coins_used": amount_points})


@app.route("/api/withdrawals")
@login_required
def api_withdrawals():
    db = get_db()
    u = current_user(db)
    rows = db.execute(
        "SELECT * FROM withdrawals WHERE user_id = ? ORDER BY created_at DESC LIMIT 50",
        (u["id"],)).fetchall()
    return jsonify({"ok": True, "withdrawals": [{
        "id": r["id"], "method": r["method"], "account": r["account"],
        "amount_points": r["amount_points"], "coins_used": r["coins_used"],
        "status": r["status"], "admin_note": r["admin_note"],
        "note": r["admin_note"],
        "created_at": r["created_at"], "decided_at": r["decided_at"],
    } for r in rows]})


# ---------------------------------------------------------------- ads API
@app.route("/api/ads")
@login_required
def api_ads():
    db = get_db()
    s = get_settings(db)
    rows = db.execute(
        "SELECT * FROM ad_providers WHERE enabled = 1 ORDER BY id").fetchall()
    return jsonify({"ok": True, "providers": [dict(r) for r in rows],
                    "rewarded_bonus_points": int(s["rewarded_bonus_points"]),
                    "interstitial_every_n_tasks": int(s["interstitial_every_n_tasks"])})


@app.route("/api/ads/rewarded", methods=["POST"])
@login_required
def api_ads_rewarded():
    db = get_db()
    u = current_user(db)
    s = get_settings(db)
    d = request.get_json(force=True, silent=True) or {}
    prov = db.execute(
        "SELECT * FROM ad_providers WHERE id = ? AND enabled = 1 AND placement_type = 'rewarded'",
        (d.get("provider_id"),)).fetchone()
    if not prov:
        return jsonify({"ok": False, "error": "no enabled rewarded provider with that id"}), 400
    pts = int(s["rewarded_bonus_points"])
    db.execute(
        "INSERT INTO ad_rewards(user_id, provider, points, created_at) VALUES(?, ?, ?, ?)",
        (u["id"], prov["provider"], pts, now_iso()))
    credit_coins(db, u["id"], pts, f"ad:{prov['provider']}")
    db.commit()
    return jsonify({"ok": True, "points": pts})


# ---------------------------------------------------------------- key-auth admin API
# The admin dashboard hosted on Netlify / Vercel / GitHub Pages talks to
# this API with a shared key instead of a browser session, because the
# dashboard origin differs from the API origin. Session auth keeps working
# unchanged for the built-in web admin panel; the key is an ADDITIONAL way
# in, not a replacement.
# SECURITY: change SHAHEENCHAIN_ADMIN_KEY in production — anyone holding the
# key has full admin power. The default "admin123" is for local demo use only.
def _admin_key_ok():
    key = request.headers.get("X-Admin-Key", "")
    expected = os.environ.get("SHAHEENCHAIN_ADMIN_KEY", "admin123")
    return bool(key) and secrets.compare_digest(key, expected)


def admin_api_required(f):
    """Allow a logged-in admin session OR a valid X-Admin-Key header."""
    @wraps(f)
    def wrapper(*a, **kw):
        db = get_db()
        u = current_user(db)
        if u and u["is_admin"]:
            return f(*a, **kw)
        if _admin_key_ok():
            return f(*a, **kw)
        return jsonify({"ok": False, "error": "admin api key required"}), 401
    return wrapper


def validate_task_payload(d):
    """Strict dashboard contract: title + description required, reward >= 1,
    cooldown >= 0. Returns (values, None) or (None, error_message)."""
    title = (d.get("title") or "").strip()
    desc = (d.get("description") or "").strip()
    if not title:
        return None, "title required"
    if not desc:
        return None, "description required"
    try:
        reward = int(d.get("reward") or 0)
        cooldown = float(d.get("cooldown_hours") if d.get("cooldown_hours") is not None else 24)
    except (TypeError, ValueError):
        return None, "bad reward or cooldown"
    if reward < 1:
        return None, "reward must be at least 1"
    if cooldown < 0:
        return None, "cooldown cannot be negative"
    return {
        "title": title, "description": desc, "reward": reward,
        "task_type": (d.get("task_type") or "daily").strip(),
        "enabled": 1 if d.get("enabled", True) else 0,
        "cooldown_hours": cooldown,
    }, None


def team_size(db, user_id):
    """Downline size across 3 referral levels (direct + L2 + L3)."""
    seen = {user_id}
    frontier = [user_id]
    total = 0
    for _ in range(3):
        if not frontier:
            break
        rows = db.execute(
            "SELECT id FROM users WHERE referred_by IN (%s)"
            % ",".join("?" * len(frontier)), frontier).fetchall()
        ids = [r["id"] for r in rows if r["id"] not in seen]
        seen.update(ids)
        total += len(ids)
        frontier = ids
    return total


# CORS for /api/* — the dashboard is hosted on Netlify / Vercel / GitHub
# Pages while this API runs on a server, so browsers need cross-origin
# access. NOTE: allowing all origins is fine for a demo; in production,
# restrict Access-Control-Allow-Origin to the dashboard's exact domain.
@app.after_request
def add_cors_headers(resp):
    if request.path.startswith("/api/"):
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Admin-Key, Authorization"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
    return resp


@app.before_request
def handle_api_preflight():
    if request.method == "OPTIONS" and request.path.startswith("/api/"):
        return "", 200


# ---------------------------------------------------------------- admin API
@app.route("/api/admin/stats")
@admin_api_required
def api_admin_stats():
    db = get_db()
    total_users = db.execute("SELECT COUNT(*) FROM users WHERE is_admin = 0").fetchone()[0]
    points = db.execute("SELECT COALESCE(SUM(coins),0) FROM users WHERE is_admin = 0").fetchone()[0]
    coins_total = db.execute("SELECT COALESCE(SUM(coin_balance),0) FROM users WHERE is_admin = 0").fetchone()[0]
    pending = db.execute("SELECT COUNT(*) FROM withdrawals WHERE status = 'pending'").fetchone()[0]
    approved = db.execute("SELECT COUNT(*) FROM withdrawals WHERE status = 'approved'").fetchone()[0]
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    tasks_today = db.execute(
        "SELECT COUNT(*) FROM task_completions WHERE substr(completed_at, 1, 10) = ?",
        (today,)).fetchone()[0]
    total_refs = db.execute(
        "SELECT COUNT(*) FROM users WHERE referred_by IS NOT NULL").fetchone()[0]
    return jsonify({"ok": True, "total_users": total_users, "points_in_circulation": points,
                    "coins_in_circulation": round(coins_total, 4),
                    "pending_withdrawals": pending, "approved_withdrawals": approved,
                    "tasks_completed_today": tasks_today, "total_referrals": total_refs})


@app.route("/api/admin/users")
@admin_api_required
def api_admin_users():
    db = get_db()
    q = (request.args.get("q") or "").strip()
    if q:
        rows = db.execute(
            "SELECT * FROM users WHERE username LIKE ? ORDER BY created_at DESC LIMIT 100",
            (f"%{q}%",)).fetchall()
    else:
        rows = db.execute("SELECT * FROM users ORDER BY created_at DESC LIMIT 100").fetchall()
    users_out = []
    for r in rows:
        u = user_public(r)
        u["referred_by"] = r["referred_by"]
        u["referrals"] = db.execute(
            "SELECT COUNT(*) FROM users WHERE referred_by = ?", (r["id"],)).fetchone()[0]
        u["team_size"] = team_size(db, r["id"])
        u["commission_total"] = db.execute(
            "SELECT COALESCE(SUM(coins),0) FROM commissions WHERE to_user = ?",
            (r["id"],)).fetchone()[0]
        users_out.append(u)
    return jsonify({"ok": True, "users": users_out})


@app.route("/api/admin/users/ban", methods=["POST"])
@admin_api_required
def api_admin_ban():
    db = get_db()
    d = request.get_json(force=True, silent=True) or {}
    target = db.execute("SELECT * FROM users WHERE id = ?", (d.get("user_id"),)).fetchone()
    if not target or target["is_admin"]:
        return jsonify({"ok": False, "error": "invalid user"}), 400
    db.execute("UPDATE users SET is_banned = ? WHERE id = ?",
               (1 if d.get("ban") else 0, target["id"]))
    db.commit()
    return jsonify({"ok": True, "is_banned": bool(d.get("ban"))})


@app.route("/api/admin/users/adjust", methods=["POST"])
@admin_api_required
def api_admin_adjust():
    db = get_db()
    d = request.get_json(force=True, silent=True) or {}
    target = db.execute("SELECT * FROM users WHERE id = ?", (d.get("user_id"),)).fetchone()
    if not target:
        return jsonify({"ok": False, "error": "user not found"}), 404
    try:
        delta = int(d.get("delta") or 0)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "bad amount"}), 400
    if delta == 0:
        return jsonify({"ok": False, "error": "amount cannot be zero"}), 400
    if delta < 0 and target["coins"] + delta < 0:
        return jsonify({"ok": False, "error": "balance would go negative"}), 400
    db.execute("UPDATE users SET coins = coins + ? WHERE id = ?", (delta, target["id"]))
    db.execute(
        "INSERT INTO balance_adjustments(admin_id, user_id, delta, reason, created_at)"
        " VALUES(?, ?, ?, ?, ?)",
        (current_user(db)["id"], target["id"], delta,
         (d.get("reason") or "").strip()[:200], now_iso()))
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/admin/tasks", methods=["GET", "POST"])
@admin_api_required
def api_admin_tasks():
    db = get_db()
    if request.method == "GET":
        rows = db.execute("SELECT * FROM tasks ORDER BY id").fetchall()
        return jsonify({"ok": True, "tasks": [dict(r) for r in rows]})
    d = request.get_json(force=True, silent=True) or {}
    title = (d.get("title") or "").strip()
    if not title:
        return jsonify({"ok": False, "error": "title required"}), 400
    # Key-authenticated creates use the strict dashboard contract. The
    # built-in web admin panel edits through this same route without a
    # description, so session requests keep the legacy validation.
    if _admin_key_ok() and not d.get("id"):
        v, err = validate_task_payload(d)
        if err:
            return jsonify({"ok": False, "error": err}), 400
    try:
        reward = int(d.get("reward") or 0)
        cooldown = float(d.get("cooldown_hours") if d.get("cooldown_hours") is not None else 24)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "bad reward or cooldown"}), 400
    if cooldown < 0:
        return jsonify({"ok": False, "error": "cooldown cannot be negative"}), 400
    if d.get("id"):
        db.execute(
            "UPDATE tasks SET title=?, description=?, reward=?, task_type=?, enabled=?, cooldown_hours=? WHERE id=?",
            (title, (d.get("description") or "").strip(), reward,
             (d.get("task_type") or "daily").strip(), 1 if d.get("enabled") else 0,
             cooldown, d["id"]))
    else:
        db.execute(
            "INSERT INTO tasks(title, description, reward, task_type, enabled, cooldown_hours) VALUES(?, ?, ?, ?, ?, ?)",
            (title, (d.get("description") or "").strip(), reward,
             (d.get("task_type") or "daily").strip(), 1 if d.get("enabled", True) else 0,
             cooldown))
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/admin/tasks/delete", methods=["POST"])
@admin_api_required
def api_admin_tasks_delete():
    db = get_db()
    d = request.get_json(force=True, silent=True) or {}
    # Completions reference the task (FK) — remove them first, otherwise
    # deleting a task that anyone completed fails with IntegrityError.
    db.execute("DELETE FROM task_completions WHERE task_id = ?", (d.get("id"),))
    db.execute("DELETE FROM tasks WHERE id = ?", (d.get("id"),))
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/admin/settings", methods=["GET", "POST"])
@admin_api_required
def api_admin_settings():
    db = get_db()
    if request.method == "GET":
        return jsonify({"ok": True, "settings": get_settings(db)})
    d = request.get_json(force=True, silent=True) or {}
    allowed = ["app_name", "mining_enabled", "coins_per_hour", "max_mining_hours",
               "min_claim_interval_min", "l1_pct", "l2_pct", "l3_pct",
               "withdrawals_enabled", "min_withdrawal_points",
               "coin_name", "coin_symbol", "points_per_coin", "min_convert_points",
               "daily_bonus_points", "daily_bonus_cooldown_hours",
               "rewarded_bonus_points", "interstitial_every_n_tasks",
               # Dashboard-only keys (stored + returned by GET; the Flask web
               # admin ignores them; the mobile app reads them).
               "tap_points_per_tap", "tap_daily_limit",
               "swap_points_per_coin", "swap_min_coins", "leaderboard_enabled",
               "home_banner_enabled", "slider_enabled", "slider_interval_sec", "watch_ads_daily_limit", "watch_ads_cooldown_min",
               "watch_ads_reward", "watch_ads_provider",
               "transfers_enabled", "transfer_min", "transfer_max",
               "transfer_fee_percent", "transfer_daily_limit", "categories",
               "wheel_spins_per_day", "wheel_min_reward", "wheel_max_reward",
               "wheel_cooldown_min", "tap_level_bonus",
               "withdrawal_gating_enabled", "tier_bronze_invites", "tier_bronze_limit",
               "tier_silver_invites", "tier_silver_limit", "tier_gold_invites",
               "tier_gold_limit", "tier_diamond_invites", "tier_diamond_limit",
               "milestone_thresholds", "milestone_bonuses", "welcome_bonus",
               "invite_task_reward", "streak_bonus_per_day", "streak_bonus_cap",
               "invite_token_reward", "welcome_token_gld", "milestone_token_bonuses",
               "inviter_rank_thresholds", "inviter_rank_multipliers",
               "weekly_pool_gld", "weekly_pool_split",
               "social_rate_gld_per_1000", "social_cap_per_post", "social_cap_per_week",
               "min_token_withdrawal", "token_withdrawal_fee_pct",
               "token_withdrawal_review_threshold",
               "latest_version_code", "update_note",
               "tap_level_thresholds", "weekly_pool_pcts",
               "website_url", "apk_download_url",
               "social_approval_mode",
               "withdrawal_method_bank", "withdrawal_method_mobile_wallet",
               "withdrawal_method_crypto",
               "token_transfer_enabled", "token_transfer_min", "token_transfer_max",
               "token_transfer_fee_pct", "token_transfer_daily_limit"]
    for k in allowed:
        if k in d:
            set_setting(db, k, d[k])
    db.commit()
    return jsonify({"ok": True, "settings": get_settings(db)})


@app.route("/api/admin/categories", methods=["GET", "PUT"])
@admin_api_required
def api_admin_categories():
    import json as _json
    db = get_db()
    if request.method == "GET":
        raw = get_setting(db, "categories") or '["Daily", "Video", "Social", "Special"]'
        try:
            cats = _json.loads(raw)
            if not isinstance(cats, list):
                raise ValueError
        except Exception:
            cats = ["Daily", "Video", "Social", "Special"]
        return jsonify({"ok": True, "categories": cats})
    d = request.get_json(force=True, silent=True) or {}
    cats = d.get("categories")
    if not isinstance(cats, list) or not cats or len(cats) > 20:
        return jsonify({"ok": False, "error": "categories must be a non-empty list (max 20)"}), 400
    clean = []
    for c in cats:
        s = str(c).strip()
        if s and len(s) <= 30 and s not in clean:
            clean.append(s)
    if not clean:
        return jsonify({"ok": False, "error": "at least one valid category is required"}), 400
    set_setting(db, "categories", _json.dumps(clean))
    db.commit()
    return jsonify({"ok": True, "categories": clean})


@app.route("/api/admin/ads", methods=["GET", "POST"])
@admin_api_required
def api_admin_ads():
    db = get_db()
    if request.method == "GET":
        rows = db.execute("SELECT * FROM ad_providers ORDER BY id").fetchall()
        s = get_settings(db)
        return jsonify({"ok": True, "providers": [dict(r) for r in rows],
                        "rewarded_bonus_points": s["rewarded_bonus_points"],
                        "interstitial_every_n_tasks": s["interstitial_every_n_tasks"]})
    d = request.get_json(force=True, silent=True) or {}
    for p in d.get("providers") or []:
        db.execute(
            "UPDATE ad_providers SET enabled=?, placement_type=?, unit_id=?, snippet=?,"
            " image_url=?, click_url=? WHERE id=?",
            (1 if p.get("enabled") else 0, (p.get("placement_type") or "banner").strip(),
             (p.get("unit_id") or "").strip()[:300],
             (p.get("snippet") or "")[:4000],
             (p.get("image_url") or "").strip()[:500],
             (p.get("click_url") or "").strip()[:500], p.get("id")))
    for k in ("rewarded_bonus_points", "interstitial_every_n_tasks"):
        if k in d:
            set_setting(db, k, d[k])
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/admin/withdrawals")
@admin_api_required
def api_admin_withdrawals():
    db = get_db()
    rows = db.execute(
        "SELECT w.*, u.username FROM withdrawals w JOIN users u ON u.id = w.user_id"
        " ORDER BY w.created_at DESC LIMIT 100").fetchall()
    return jsonify({"ok": True, "withdrawals": [{
        "id": r["id"], "user_id": r["user_id"], "username": r["username"], "method": r["method"],
        "account": r["account"], "amount_points": r["amount_points"],
        "coins_used": r["coins_used"], "status": r["status"],
        "admin_note": r["admin_note"], "created_at": r["created_at"],
        "decided_at": r["decided_at"],
    } for r in rows]})


@app.route("/api/admin/transfers")
@admin_api_required
def api_admin_transfers():
    db = get_db()
    rows = db.execute(
        "SELECT t.*, fu.username AS from_name, tu.username AS to_name FROM transfers t"
        " JOIN users fu ON fu.id = t.from_user JOIN users tu ON tu.id = t.to_user"
        " ORDER BY t.created_at DESC LIMIT 100").fetchall()
    return jsonify({"ok": True, "transfers": [{
        "id": r["id"], "from": r["from_name"], "to": r["to_name"],
        "amount": r["points"], "fee": r["fee"], "received": r["received"],
        "currency": (r["currency"] if "currency" in r.keys() else "points"),
        "note": r["note"], "created_at": r["created_at"],
    } for r in rows]})


@app.route("/api/admin/withdrawals/decide", methods=["POST"])
@admin_api_required
def api_admin_decide():
    db = get_db()
    d = request.get_json(force=True, silent=True) or {}
    w = db.execute("SELECT * FROM withdrawals WHERE id = ?", (d.get("id"),)).fetchone()
    if not w or w["status"] != "pending":
        return jsonify({"ok": False, "error": "request not pending"}), 400
    action = d.get("action")
    note = (d.get("note") or "").strip()[:200]
    if action == "approve":
        # Points were already deducted at request time; approving marks it paid.
        db.execute(
            "UPDATE withdrawals SET status='approved', admin_note=?, decided_at=? WHERE id=?",
            (note, now_iso(), w["id"]))
    elif action == "reject":
        db.execute("UPDATE users SET coins = coins + ? WHERE id = ?",
                   (w["coins_used"], w["user_id"]))
        db.execute(
            "UPDATE withdrawals SET status='rejected', admin_note=?, decided_at=? WHERE id=?",
            (note, now_iso(), w["id"]))
    else:
        return jsonify({"ok": False, "error": "bad action"}), 400
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/admin/change-password", methods=["POST"])
@admin_api_required
def api_admin_change_password():
    db = get_db()
    d = request.get_json(force=True, silent=True) or {}
    new = d.get("new_password") or ""
    if len(new) < 4:
        return jsonify({"ok": False, "error": "password min 4 chars"}), 400
    u = current_user(db)
    db.execute("UPDATE users SET password_hash = ? WHERE id = ?",
               (generate_password_hash(new), u["id"]))
    db.commit()
    return jsonify({"ok": True})


# ---------------------------------------------------------------- key-auth REST routes
# RESTful shapes used by the hosted admin dashboard (API-key auth). The
# legacy session routes above keep working unchanged.
@app.route("/api/admin/users/<int:user_id>/ban", methods=["POST"])
@admin_api_required
def api_admin_user_ban(user_id):
    db = get_db()
    t = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not t:
        return jsonify({"ok": False, "error": "user not found"}), 404
    if t["is_admin"]:
        return jsonify({"ok": False, "error": "cannot ban an admin"}), 400
    db.execute("UPDATE users SET is_banned = 1 WHERE id = ?", (user_id,))
    db.commit()
    return jsonify({"ok": True, "is_banned": True})


@app.route("/api/admin/users/<int:user_id>/unban", methods=["POST"])
@admin_api_required
def api_admin_user_unban(user_id):
    db = get_db()
    t = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not t:
        return jsonify({"ok": False, "error": "user not found"}), 404
    db.execute("UPDATE users SET is_banned = 0 WHERE id = ?", (user_id,))
    db.commit()
    return jsonify({"ok": True, "is_banned": False})


@app.route("/api/admin/users/<int:user_id>/adjust", methods=["POST"])
@admin_api_required
def api_admin_user_adjust(user_id):
    db = get_db()
    t = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not t:
        return jsonify({"ok": False, "error": "user not found"}), 404
    d = request.get_json(force=True, silent=True) or {}
    try:
        raw = d.get("points") if d.get("points") is not None else d.get("delta") or 0
        delta = int(raw)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "bad amount"}), 400
    if delta == 0:
        return jsonify({"ok": False, "error": "amount cannot be zero"}), 400
    if delta < 0 and t["coins"] + delta < 0:
        return jsonify({"ok": False, "error": "balance would go negative"}), 400
    db.execute("UPDATE users SET coins = coins + ? WHERE id = ?", (delta, user_id))
    admin_row = db.execute(
        "SELECT id FROM users WHERE is_admin = 1 ORDER BY id LIMIT 1").fetchone()
    if admin_row:
        db.execute(
            "INSERT INTO balance_adjustments(admin_id, user_id, delta, reason, created_at)"
            " VALUES(?, ?, ?, ?, ?)",
            (admin_row["id"], user_id, delta,
             (d.get("reason") or "").strip()[:200], now_iso()))
    db.commit()
    return jsonify({"ok": True, "points": t["coins"] + delta})


@app.route("/api/admin/tasks/<int:task_id>", methods=["PUT"])
@admin_api_required
def api_admin_task_update(task_id):
    db = get_db()
    if not db.execute("SELECT id FROM tasks WHERE id = ?", (task_id,)).fetchone():
        return jsonify({"ok": False, "error": "task not found"}), 404
    d = request.get_json(force=True, silent=True) or {}
    v, err = validate_task_payload(d)
    if err:
        return jsonify({"ok": False, "error": err}), 400
    db.execute(
        "UPDATE tasks SET title=?, description=?, reward=?, task_type=?, enabled=?, cooldown_hours=? WHERE id=?",
        (v["title"], v["description"], v["reward"], v["task_type"], v["enabled"],
         v["cooldown_hours"], task_id))
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/admin/tasks/<int:task_id>", methods=["DELETE"])
@admin_api_required
def api_admin_task_delete(task_id):
    db = get_db()
    if not db.execute("SELECT id FROM tasks WHERE id = ?", (task_id,)).fetchone():
        return jsonify({"ok": False, "error": "task not found"}), 404
    db.execute("DELETE FROM task_completions WHERE task_id = ?", (task_id,))
    db.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/admin/settings", methods=["PUT"])
@admin_api_required
def api_admin_settings_put():
    db = get_db()
    d = request.get_json(force=True, silent=True) or {}
    allowed = ["app_name", "mining_enabled", "coins_per_hour", "max_mining_hours",
               "min_claim_interval_min", "l1_pct", "l2_pct", "l3_pct",
               "withdrawals_enabled", "min_withdrawal_points",
               "coin_name", "coin_symbol", "points_per_coin", "min_convert_points",
               "daily_bonus_points", "daily_bonus_cooldown_hours",
               "rewarded_bonus_points", "interstitial_every_n_tasks",
               "tap_points_per_tap", "tap_daily_limit",
               "swap_points_per_coin", "swap_min_coins", "leaderboard_enabled",
               "home_banner_enabled", "slider_enabled", "slider_interval_sec", "watch_ads_daily_limit", "watch_ads_cooldown_min",
               "watch_ads_reward", "watch_ads_provider",
               "transfers_enabled", "transfer_min", "transfer_max",
               "transfer_fee_percent", "transfer_daily_limit", "categories",
               "wheel_spins_per_day", "wheel_min_reward", "wheel_max_reward",
               "wheel_cooldown_min", "tap_level_bonus",
               "withdrawal_gating_enabled", "tier_bronze_invites", "tier_bronze_limit",
               "tier_silver_invites", "tier_silver_limit", "tier_gold_invites",
               "tier_gold_limit", "tier_diamond_invites", "tier_diamond_limit",
               "milestone_thresholds", "milestone_bonuses", "welcome_bonus",
               "invite_task_reward", "streak_bonus_per_day", "streak_bonus_cap",
               "invite_token_reward", "welcome_token_gld", "milestone_token_bonuses",
               "inviter_rank_thresholds", "inviter_rank_multipliers",
               "weekly_pool_gld", "weekly_pool_split",
               "social_rate_gld_per_1000", "social_cap_per_post", "social_cap_per_week",
               "min_token_withdrawal", "token_withdrawal_fee_pct",
               "token_withdrawal_review_threshold",
               "latest_version_code", "update_note",
               "tap_level_thresholds", "weekly_pool_pcts",
               "website_url", "apk_download_url",
               "social_approval_mode",
               "withdrawal_method_bank", "withdrawal_method_mobile_wallet",
               "withdrawal_method_crypto",
               "token_transfer_enabled", "token_transfer_min", "token_transfer_max",
               "token_transfer_fee_pct", "token_transfer_daily_limit"]
    for k in allowed:
        if k in d:
            set_setting(db, k, d[k])
    db.commit()
    return jsonify({"ok": True, "settings": get_settings(db)})


@app.route("/api/admin/ads", methods=["PUT"])
@admin_api_required
def api_admin_ads_put():
    db = get_db()
    d = request.get_json(force=True, silent=True) or {}
    for p in d.get("providers") or []:
        db.execute(
            "UPDATE ad_providers SET enabled=?, placement_type=?, unit_id=?, snippet=?,"
            " image_url=?, click_url=? WHERE id=?",
            (1 if p.get("enabled") else 0, (p.get("placement_type") or "banner").strip(),
             (p.get("unit_id") or "").strip()[:300],
             (p.get("snippet") or "")[:4000],
             (p.get("image_url") or "").strip()[:500],
             (p.get("click_url") or "").strip()[:500], p.get("id")))
    for k in ("rewarded_bonus_points", "interstitial_every_n_tasks"):
        if k in d:
            set_setting(db, k, d[k])
    db.commit()
    return jsonify({"ok": True})


def _decide_withdrawal_new(db, wid, action, note):
    w = db.execute("SELECT * FROM withdrawals WHERE id = ?", (wid,)).fetchone()
    if not w:
        return "withdrawal not found", 404
    if w["status"] != "pending":
        return "request not pending", 400
    if action == "approve":
        # Points were already deducted at request time; approving marks it paid.
        db.execute(
            "UPDATE withdrawals SET status='approved', admin_note=?, decided_at=? WHERE id=?",
            (note, now_iso(), wid))
    elif action == "reject":
        db.execute("UPDATE users SET coins = coins + ? WHERE id = ?",
                   (w["coins_used"], w["user_id"]))
        db.execute(
            "UPDATE withdrawals SET status='rejected', admin_note=?, decided_at=? WHERE id=?",
            (note, now_iso(), wid))
    else:
        return "bad action", 400
    db.commit()
    return None, None


@app.route("/api/admin/withdrawals/<int:wid>/approve", methods=["POST"])
@admin_api_required
def api_admin_wd_approve(wid):
    db = get_db()
    d = request.get_json(force=True, silent=True) or {}
    err, code = _decide_withdrawal_new(
        db, wid, "approve", (d.get("note") or "").strip()[:200])
    if err:
        return jsonify({"ok": False, "error": err}), code
    return jsonify({"ok": True})


@app.route("/api/admin/withdrawals/<int:wid>/reject", methods=["POST"])
@admin_api_required
def api_admin_wd_reject(wid):
    db = get_db()
    d = request.get_json(force=True, silent=True) or {}
    err, code = _decide_withdrawal_new(
        db, wid, "reject", (d.get("note") or "").strip()[:200])
    if err:
        return jsonify({"ok": False, "error": err}), code
    return jsonify({"ok": True})


@app.route("/api/admin/commissions")
@admin_api_required
def api_admin_commissions():
    db = get_db()
    rows = db.execute(
        "SELECT c.*, fu.username AS from_username, tu.username AS to_username"
        " FROM commissions c JOIN users fu ON fu.id = c.from_user"
        " JOIN users tu ON tu.id = c.to_user"
        " ORDER BY c.created_at DESC LIMIT 50").fetchall()
    return jsonify({"ok": True, "commissions": [dict(r) for r in rows]})


# ---------------------------------------------------------------- token user API
# v1.6: the mobile app in server mode talks to these endpoints with a
# Bearer token (see /api/auth/*). They reuse the same tables and rules as
# the session web routes above — server-side enforcement only, the client
# is never trusted for points, limits, or cooldowns. Demo-grade token auth:
# a random per-login token stored on the user row (NOT bcrypt-level, and
# CORS is open on /api/* — see the CORS note above).
def _new_token():
    return secrets.token_hex(32)


# ---------------------------------------------------------------- v2.1: referral landing page
# A plain website APK cannot carry a referrer into the installed app
# (no Play Install Referrer), so the practical automatic path is:
#   share link  {website_url}/download?ref=CODE
#   -> this page, referral pre-filled, registration reuses the exact
#      /api/auth/register logic (welcome bonus once, inviter reward on
#      the new user's first valid task, unknown codes ignored by it)
#   -> the user then logs into the app with the same account and the
#      referral is already attached (no second bonus: registration
#      inserts the bonus exactly once; login never re-applies it).
#   -> the app itself also auto-fills a clipboard code on its
#      registration screen (see the app's auth screen).
@app.route("/download")
def download_page():
    import html as _html
    db = get_db()
    settings = get_settings(db)
    ref = (request.args.get("ref") or "").strip().upper()
    inviter = None
    if ref:
        row = db.execute(
            "SELECT username FROM users WHERE referral_code = ?",
            (ref,)).fetchone()
        if row:
            inviter = row["username"]
    welcome = _fval(settings.get("welcome_token_gld"), 0.1)
    apk_url = (settings.get("apk_download_url") or "").strip()
    if inviter:
        banner = ("<p class=\"banner ok\">You were invited by <b>" + _html.escape(inviter) +
                  "</b> \u2014 welcome bonus <b>" + _html.escape(str(welcome)) +
                  " GLD</b> when you register below.</p>")
    elif ref:
        banner = ("<p class=\"banner warn\">This invite link\u2019s code was not recognised "
                  "\u2014 you can still register below, just without the welcome bonus.</p>")
    else:
        banner = ("<p class=\"banner\">Welcome to ShaheenChain \u2014 tap the falcon, earn gold.</p>")
    if apk_url:
        dl = ("<a class=\"btn\" href=\"" + _html.escape(apk_url) +
              "\">Download the app (APK)</a>")
    else:
        dl = ("<p class=\"muted\">The app download link will be provided by the team \u2014 "
              "you can register here now and log in once you have the app.</p>")
    page = ("<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
            "<title>ShaheenChain \u2014 Download &amp; Register</title>"
            "<style>body{margin:0;background:#060913;color:#f2f4fa;font-family:system-ui,sans-serif}"
            ".wrap{max-width:440px;margin:0 auto;padding:24px 16px}h1{color:#f5c542}"
            ".card{background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.12);"
            "border-radius:18px;padding:20px;margin:14px 0}"
            "input{width:100%;box-sizing:border-box;padding:13px;margin:6px 0;border-radius:13px;"
            "border:1px solid rgba(255,255,255,.15);background:rgba(0,0,0,.35);color:#fff;font-size:15px}"
            ".btn{display:inline-block;padding:13px 20px;border:0;border-radius:13px;font-weight:800;"
            "background:linear-gradient(135deg,#ffd76a,#f5c542 55%,#e8a020);color:#231a02;"
            "text-decoration:none;cursor:pointer;font-size:15px}"
            ".banner{padding:14px;border-radius:14px;background:rgba(245,197,66,.12);"
            "border:1px solid rgba(245,197,66,.4)}"
            ".banner.warn{background:rgba(248,113,113,.12);border-color:rgba(248,113,113,.4)}"
            ".muted{color:#93a1b8;font-size:13px;line-height:1.55}.msg{color:#f5c542;min-height:1em}</style></head>"
            "<body><div class=\"wrap\"><h1>ShaheenChain</h1>" + banner +
            "<div class=\"card\"><h2>Register</h2>"
            "<p class=\"muted\">Your referral code is already filled in from the invite link. "
            "Tap Register to confirm \u2014 nothing else to type.</p>"
            "<input id=\"u\" placeholder=\"Username (min 3 characters)\" autocomplete=\"username\">"
            "<input id=\"p\" type=\"password\" placeholder=\"Password (min 4 characters)\" autocomplete=\"new-password\">"
            "<input id=\"r\" placeholder=\"Referral code (optional)\" value=\"" + _html.escape(ref, quote=True) + "\">"
            "<button class=\"btn\" onclick=\"go()\">Register</button>"
            "<p class=\"msg\" id=\"m\"></p>"
            "<p class=\"muted\" id=\"done\" style=\"display:none\">Account created! "
            "Open the app and log in with this username and password \u2014 your referral is already attached, "
            "and the welcome bonus is paid only once, at registration.</p></div>"
            "<div class=\"card\">" + dl + "</div>"
            "<p class=\"muted\">Already have an account? Just log in inside the app.</p>"
            "</div><script>async function go(){"
            "var u=document.getElementById('u').value.trim();"
            "var p=document.getElementById('p').value;"
            "var r=document.getElementById('r').value.trim();"
            "var m=document.getElementById('m');"
            "if(u.length<3||p.length<4){m.textContent='Username min 3 characters, password min 4.';return;}"
            "m.textContent='Creating account\u2026';"
            "try{var res=await fetch('/api/auth/register',{method:'POST',"
            "headers:{'Content-Type':'application/json'},"
            "body:JSON.stringify({username:u,password:p,referral_code:r})});"
            "var d=await res.json();"
            "if(!res.ok||!d.ok){m.textContent=d.error||'Registration failed.';return;}"
            "m.textContent='';document.getElementById('done').style.display='block';"
            "}catch(e){m.textContent='Could not reach the server. Try again.';}"
            "}</script></body></html>")
    return page


@app.route("/api/auth/register", methods=["POST"])
def api_auth_register():
    db = get_db()
    d = request.get_json(force=True, silent=True) or {}
    username = (d.get("username") or "").strip()
    password = d.get("password") or ""
    ref_code = (d.get("referral_code") or "").strip().upper()
    if len(username) < 3 or len(password) < 4:
        return jsonify({"ok": False, "error": "username min 3 chars, password min 4"}), 400
    if db.execute("SELECT id FROM users WHERE username = ?",
                  (username,)).fetchone():
        return jsonify({"ok": False, "error": "username taken"}), 400
    referred_by = None
    if ref_code:
        ref = db.execute(
            "SELECT id FROM users WHERE referral_code = ?", (ref_code,)).fetchone()
        if ref:
            referred_by = ref["id"]
    # v1.9 welcome bonus: TOKENS (default 0.1 GLD) for registering with a
    # code. No points, no commission on it. Legacy welcome_bonus (points)
    # is no longer used for new registrations.
    welcome_tokens = _fval(get_settings(db)["welcome_token_gld"], 0.1) if referred_by else 0.0
    tok = _new_token()
    cur = db.execute(
        "INSERT INTO users(username, password_hash, coins, coin_balance, referral_code, referred_by, created_at, auth_token)"
        " VALUES(?, ?, 0, ?, ?, ?, ?, ?)",
        (username, generate_password_hash(password), welcome_tokens,
         make_referral_code(username), referred_by, now_iso(), tok))
    if welcome_tokens > 0:
        db.execute(
            "INSERT INTO token_ledger(user_id, amount, source, note, created_at)"
            " VALUES(?, ?, 'welcome_bonus', 'registered with a referral code', ?)",
            (cur.lastrowid, welcome_tokens, now_iso()))
    db.commit()
    u = db.execute("SELECT * FROM users WHERE id = ?", (cur.lastrowid,)).fetchone()
    return jsonify({"ok": True, "token": tok, "user": user_public(u)})


@app.route("/api/auth/login", methods=["POST"])
def api_auth_login():
    db = get_db()
    d = request.get_json(force=True, silent=True) or {}
    u = db.execute(
        "SELECT * FROM users WHERE username = ?", ((d.get("username") or "").strip(),)
    ).fetchone()
    if not u or not check_password_hash(u["password_hash"], d.get("password") or ""):
        return jsonify({"ok": False, "error": "wrong username or password"}), 401
    if u["is_banned"]:
        return jsonify({"ok": False, "error": "account banned"}), 403
    if u["is_admin"]:
        return jsonify({"ok": False, "error": "admin accounts use the dashboard"}), 403
    tok = _new_token()
    db.execute("UPDATE users SET auth_token = ? WHERE id = ?", (tok, u["id"]))
    db.commit()
    u = db.execute("SELECT * FROM users WHERE id = ?", (u["id"],)).fetchone()
    return jsonify({"ok": True, "token": tok, "user": user_public(u)})


@app.route("/api/mining")
@login_required
def api_mining():
    db = get_db()
    return jsonify(mining_status_for(db, current_user(db)))


@app.route("/api/tasks/<int:task_id>/complete", methods=["POST"])
@login_required
def api_task_complete_rest(task_id):
    db = get_db()
    payload, code = complete_task_for(db, current_user(db), task_id)
    return jsonify(payload), code


@app.route("/api/tap", methods=["POST"])
@login_required
def api_tap():
    db = get_db()
    u = current_user(db)
    s = get_settings(db)
    base = int(s["tap_points_per_tap"])
    limit = int(s["tap_daily_limit"])
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    taps = db.execute(
        "SELECT COUNT(*) FROM tap_events"
        " WHERE user_id = ? AND substr(created_at, 1, 10) = ?",
        (u["id"], today)).fetchone()[0]
    if taps >= limit:
        return jsonify({"ok": False,
                        "error": f"daily tap limit reached ({limit} taps)"}), 400
    lifetime = db.execute(
        "SELECT COUNT(*) FROM tap_events WHERE user_id = ?",
        (u["id"],)).fetchone()[0]
    level = tap_level_for(lifetime, int(s["tap_level_bonus"]), s)
    per_tap = base + level["bonus"]
    db.execute("INSERT INTO tap_events(user_id, points, created_at) VALUES(?, ?, ?)",
               (u["id"], per_tap, now_iso()))
    credit_coins(db, u["id"], per_tap, "tap")
    db.commit()
    new_level = tap_level_for(lifetime + 1, int(s["tap_level_bonus"]), s)
    return jsonify({"ok": True, "gained": per_tap, "taps": taps + 1, "limit": limit,
                    "base": base, "level_bonus": level["bonus"],
                    "tap_level": new_level,
                    "leveled_up": new_level["level"] > level["level"]})


@app.route("/api/wallet")
@login_required
def api_wallet():
    db = get_db()
    u = current_user(db)
    s = get_settings(db)
    hist = []
    for r in db.execute(
            "SELECT t.*, u2.username AS other_name FROM transfers t"
            " JOIN users u2 ON u2.id = CASE WHEN t.from_user = ? THEN t.to_user ELSE t.from_user END"
            " WHERE t.from_user = ? OR t.to_user = ? ORDER BY t.created_at DESC LIMIT 50",
            (u["id"], u["id"], u["id"])).fetchall():
        sent = r["from_user"] == u["id"]
        hist.append({"type": "transfer_sent" if sent else "transfer_received",
                     "other": r["other_name"], "amount": r["points"], "fee": r["fee"],
                     "received": r["received"], "note": r["note"],
                     "currency": (r["currency"] if "currency" in r.keys() else "points"),
                     "created_at": r["created_at"]})
    for r in db.execute(
            "SELECT * FROM swap_history WHERE user_id = ?"
            " ORDER BY created_at DESC LIMIT 50", (u["id"],)).fetchall():
        hist.append({"type": "swap", "direction": r["direction"],
                     "points": r["points"], "coins": r["coins"],
                     "created_at": r["created_at"]})
    for r in db.execute(
            "SELECT * FROM conversions WHERE user_id = ?"
            " ORDER BY created_at DESC LIMIT 50", (u["id"],)).fetchall():
        hist.append({"type": "swap", "direction": "p2c",
                     "points": r["points_spent"], "coins": r["coins_received"],
                     "created_at": r["created_at"]})
    for r in db.execute(
            "SELECT tc.completed_at, t.title, t.reward FROM task_completions tc"
            " JOIN tasks t ON t.id = tc.task_id WHERE tc.user_id = ?"
            " ORDER BY tc.completed_at DESC LIMIT 50", (u["id"],)).fetchall():
        hist.append({"type": "earn", "source": "task:" + r["title"],
                     "points": r["reward"], "created_at": r["completed_at"]})
    for r in db.execute(
            "SELECT * FROM bonus_claims WHERE user_id = ?"
            " ORDER BY claimed_at DESC LIMIT 50", (u["id"],)).fetchall():
        hist.append({"type": "earn", "source": "daily_bonus",
                     "points": r["points"], "created_at": r["claimed_at"]})
    for r in db.execute(
            "SELECT * FROM mining_sessions WHERE user_id = ? AND claimed_at IS NOT NULL"
            " AND coins_claimed > 0 ORDER BY claimed_at DESC LIMIT 50",
            (u["id"],)).fetchall():
        hist.append({"type": "earn", "source": "mining",
                     "points": r["coins_claimed"], "created_at": r["claimed_at"]})
    for r in db.execute(
            "SELECT * FROM ad_rewards WHERE user_id = ?"
            " ORDER BY created_at DESC LIMIT 50", (u["id"],)).fetchall():
        hist.append({"type": "earn", "source": "ad:" + r["provider"],
                     "points": r["points"], "created_at": r["created_at"]})
    for r in db.execute(
            "SELECT * FROM token_ledger WHERE user_id = ?"
            " ORDER BY created_at DESC LIMIT 50", (u["id"],)).fetchall():
        hist.append({"type": "token", "amount": r["amount"], "source": r["source"],
                     "note": r["note"], "created_at": r["created_at"]})
    for r in db.execute(
            "SELECT * FROM token_withdrawals WHERE user_id = ?"
            " ORDER BY created_at DESC LIMIT 50", (u["id"],)).fetchall():
        hist.append({"type": "token_withdrawal", "amount": r["amount"],
                     "fee": r["fee"], "status": r["status"],
                     "method": r["method"], "created_at": r["created_at"]})
    for r in db.execute(
            "SELECT c.*, fu.username AS from_name FROM commissions c"
            " JOIN users fu ON fu.id = c.from_user WHERE c.to_user = ?"
            " ORDER BY c.created_at DESC LIMIT 50", (u["id"],)).fetchall():
        hist.append({"type": "earn", "source": f"commission:L{r['level']}",
                     "points": r["coins"], "from": r["from_name"],
                     "created_at": r["created_at"]})
    hist.sort(key=lambda h: h["created_at"], reverse=True)
    return jsonify({
        "ok": True, "points": u["coins"], "coin_balance": u["coin_balance"],
        "coin_name": s["coin_name"], "coin_symbol": s["coin_symbol"],
        "points_per_coin": s["points_per_coin"],
        "history": hist[:50],
    })


@app.route("/api/swap", methods=["POST"])
@login_required
def api_swap():
    db = get_db()
    u = current_user(db)
    s = get_settings(db)
    d = request.get_json(force=True, silent=True) or {}
    direction = (d.get("direction") or "").strip()
    if direction == "p2c":
        try:
            amount = int(d.get("amount") or 0)
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "bad amount"}), 400
        min_pts = int(s["min_convert_points"])
        rate = float(s["points_per_coin"])
        if amount < min_pts:
            return jsonify({"ok": False, "error": f"minimum swap is {min_pts} points"}), 400
        if amount > u["coins"]:
            return jsonify({"ok": False, "error": "not enough points"}), 400
        coins_out = round(amount / rate, 4)
        if coins_out <= 0:
            return jsonify({"ok": False, "error": "amount too small for this rate"}), 400
        db.execute("UPDATE users SET coins = coins - ?, coin_balance = coin_balance + ? WHERE id = ?",
                   (amount, coins_out, u["id"]))
        db.execute(
            "INSERT INTO swap_history(user_id, direction, points, coins, created_at)"
            " VALUES(?, 'p2c', ?, ?, ?)", (u["id"], amount, coins_out, now_iso()))
        db.commit()
        return jsonify({"ok": True, "coins": coins_out, "coin_symbol": s["coin_symbol"]})
    if direction == "c2p":
        try:
            amount = float(d.get("amount") or 0)
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "bad amount"}), 400
        rate = float(s["swap_points_per_coin"])
        min_coins = float(s["swap_min_coins"])
        if amount < min_coins:
            return jsonify({"ok": False,
                            "error": f"minimum swap is {min_coins} {s['coin_symbol']}"}), 400
        if amount > u["coin_balance"] + 1e-9:
            return jsonify({"ok": False, "error": "not enough coins"}), 400
        points_out = round(amount * rate)
        db.execute("UPDATE users SET coins = coins + ?, coin_balance = coin_balance - ? WHERE id = ?",
                   (points_out, amount, u["id"]))
        db.execute(
            "INSERT INTO swap_history(user_id, direction, points, coins, created_at)"
            " VALUES(?, 'c2p', ?, ?, ?)", (u["id"], points_out, amount, now_iso()))
        db.commit()
        return jsonify({"ok": True, "points": points_out})
    return jsonify({"ok": False, "error": "direction must be p2c or c2p"}), 400


@app.route("/api/transfer", methods=["POST"])
@login_required
def api_transfer():
    db = get_db()
    u = current_user(db)
    s = get_settings(db)
    d = request.get_json(force=True, silent=True) or {}
    # v1.1 (v22): transfers in Points OR GLD tokens.
    currency = (d.get("currency") or "points").strip()
    if currency not in ("points", "tokens"):
        return jsonify({"ok": False, "error": "invalid currency"}), 400
    if currency == "tokens" and s["token_transfer_enabled"] != "1":
        return jsonify({"ok": False, "error": "token transfers are disabled"}), 403
    if currency == "points" and s["transfers_enabled"] != "1":
        return jsonify({"ok": False, "error": "transfers are disabled"}), 403
    to = db.execute("SELECT * FROM users WHERE LOWER(username) = LOWER(?)",
                    ((d.get("to_username") or "").strip(),)).fetchone()
    if not to:
        return jsonify({"ok": False, "error": "recipient not found"}), 404
    if to["id"] == u["id"]:
        unit_self = "GLD" if currency == "tokens" else "points"
        return jsonify({"ok": False, "error": f"you cannot send {unit_self} to yourself"}), 400
    if to["is_banned"]:
        return jsonify({"ok": False, "error": "recipient account is banned"}), 400
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    sent_today = db.execute(
        "SELECT COALESCE(SUM(points),0) FROM transfers"
        " WHERE from_user = ? AND currency = ? AND substr(created_at, 1, 10) = ?",
        (u["id"], currency, today)).fetchone()[0]
    note = (d.get("note") or "").strip()[:120]
    if currency == "tokens":
        try:
            amount = float(d.get("amount") or 0)
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "bad amount"}), 400
        min_v = _fval(s["token_transfer_min"], 0.1)
        max_v = _fval(s["token_transfer_max"], 100)
        fee_pct = _fval(s["token_transfer_fee_pct"], 0)
        daily_limit = _fval(s["token_transfer_daily_limit"], 500)
        if not (amount > 0):
            return jsonify({"ok": False, "error": "enter a valid GLD amount"}), 400
        if amount < min_v:
            return jsonify({"ok": False, "error": f"minimum token transfer is {min_v} GLD"}), 400
        if amount > max_v:
            return jsonify({"ok": False, "error": f"maximum token transfer is {max_v} GLD"}), 400
        if amount > u["coin_balance"] + 1e-9:
            return jsonify({"ok": False, "error": "not enough GLD"}), 400
        if sent_today + amount > daily_limit:
            return jsonify({"ok": False,
                            "error": f"daily token transfer limit reached ({daily_limit} GLD)"}), 400
        fee = round(amount * fee_pct / 100, 4)
        received = round(amount - fee, 4)
        db.execute("UPDATE users SET coin_balance = coin_balance - ? WHERE id = ?", (amount, u["id"]))
        db.execute("UPDATE users SET coin_balance = coin_balance + ? WHERE id = ?", (received, to["id"]))
        db.execute(
            "INSERT INTO transfers(from_user, to_user, points, fee, received, note, currency, created_at)"
            " VALUES(?, ?, ?, ?, ?, ?, 'tokens', ?)",
            (u["id"], to["id"], amount, fee, received, note, now_iso()))
        db.commit()
        return jsonify({"ok": True, "to": to["username"], "amount": amount,
                        "fee": fee, "received": received, "currency": "tokens"})
    try:
        amount = int(d.get("amount") or 0)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "bad amount"}), 400
    min_v, max_v = int(s["transfer_min"]), int(s["transfer_max"])
    fee_pct, daily_limit = int(s["transfer_fee_percent"]), int(s["transfer_daily_limit"])
    if amount < min_v:
        return jsonify({"ok": False, "error": f"minimum transfer is {min_v} points"}), 400
    if amount > max_v:
        return jsonify({"ok": False, "error": f"maximum transfer is {max_v} points"}), 400
    if amount > u["coins"]:
        return jsonify({"ok": False, "error": "not enough points"}), 400
    if sent_today + amount > daily_limit:
        return jsonify({"ok": False,
                        "error": f"daily transfer limit reached ({daily_limit} points)"}), 400
    fee = amount * fee_pct // 100
    received = amount - fee
    db.execute("UPDATE users SET coins = coins - ? WHERE id = ?", (amount, u["id"]))
    db.execute("UPDATE users SET coins = coins + ? WHERE id = ?", (received, to["id"]))
    db.execute(
        "INSERT INTO transfers(from_user, to_user, points, fee, received, note, currency, created_at)"
        " VALUES(?, ?, ?, ?, ?, ?, 'points', ?)",
        (u["id"], to["id"], amount, fee, received, note, now_iso()))
    db.commit()
    return jsonify({"ok": True, "to": to["username"], "amount": amount,
                    "fee": fee, "received": received, "currency": "points"})


@app.route("/api/leaderboard")
@login_required
def api_leaderboard():
    db = get_db()
    rows = db.execute(
        "SELECT id, username, coins FROM users WHERE is_admin = 0"
        " ORDER BY coins DESC, created_at ASC LIMIT 10").fetchall()
    return jsonify({"ok": True, "leaderboard": [
        {"id": r["id"], "username": r["username"], "points": r["coins"]}
        for r in rows],
        "top_inviters": top_inviters(db, 10)})


@app.route("/api/growth")
@login_required
def api_growth():
    db = get_db()
    u = current_user(db)
    g = growth_for_user(db, u["id"])
    g["top_inviters"] = top_inviters(db, 10)
    g["invite_stats"] = invite_stats(db)
    return jsonify({"ok": True, **g})


@app.route("/api/admin/growth")
@admin_api_required
def api_admin_growth():
    db = get_db()
    return jsonify({"ok": True, "invite_stats": invite_stats(db),
                    "top_inviters": top_inviters(db, 10),
                    "settings": get_settings(db)})


@app.route("/api/invite")
@login_required
def api_invite():
    db = get_db()
    u = current_user(db)
    s = get_settings(db)
    levels, seen, frontier = [[], [], []], {u["id"]}, [u["id"]]
    for lv in range(3):
        if not frontier:
            break
        rows = db.execute(
            "SELECT * FROM users WHERE referred_by IN (%s)"
            % ",".join("?" * len(frontier)), frontier).fetchall()
        nxt = []
        for r in rows:
            if r["id"] in seen:
                continue
            seen.add(r["id"])
            levels[lv].append(r)
            nxt.append(r["id"])
        frontier = nxt
    friends = []
    for lv, arr in enumerate(levels):
        for r in arr:
            friends.append({"id": r["id"], "username": r["username"],
                            "points": r["coins"], "level": lv + 1,
                            "referred_by": r["referred_by"]})
    friends.sort(key=lambda f: -f["points"])
    earned = db.execute(
        "SELECT COALESCE(SUM(coins),0) FROM commissions WHERE to_user = ?",
        (u["id"],)).fetchone()[0]
    comms = db.execute(
        "SELECT c.level, c.coins, c.source, c.created_at, fu.username AS from_user"
        " FROM commissions c JOIN users fu ON fu.id = c.from_user"
        " WHERE c.to_user = ? ORDER BY c.created_at DESC LIMIT 50",
        (u["id"],)).fetchall()
    g = growth_for_user(db, u["id"])
    return jsonify({
        "ok": True,
        "referral_code": u["referral_code"],
        "invite_link": ((s.get("website_url") or "").strip().rstrip("/") or request.host_url.rstrip("/")) + "/download?ref=" + u["referral_code"],
        "invites": len(levels[0]),
        "team_size": sum(len(a) for a in levels),
        "referral_earnings": earned,
        "l1_pct": s["l1_pct"], "l2_pct": s["l2_pct"], "l3_pct": s["l3_pct"],
        "friends": friends,
        "commissions": [dict(r) for r in comms],
        "valid_invites": g["valid_invites"],
        "total_invites": g["total_invites"],
        "valid_invites_today": g["valid_invites_today"],
        "tier": g["tier"],
        "milestones": g["milestones"],
        "top_inviters": top_inviters(db, 10),
    })


@app.route("/api/wheel", methods=["GET"])
@login_required
def api_wheel_state():
    db = get_db()
    u = current_user(db)
    s = get_settings(db)
    limit = int(s["wheel_spins_per_day"])
    cooldown = int(s["wheel_cooldown_min"])
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    spun = db.execute(
        "SELECT COUNT(*) FROM wheel_spins"
        " WHERE user_id = ? AND substr(created_at, 1, 10) = ?",
        (u["id"], today)).fetchone()[0]
    last = db.execute(
        "SELECT MAX(created_at) FROM wheel_spins WHERE user_id = ?",
        (u["id"],)).fetchone()[0]
    remaining_min = 0
    if last:
        mins = (datetime.now(timezone.utc) - parse_iso(last)).total_seconds() / 60.0
        if mins < cooldown:
            remaining_min = round(cooldown - mins, 1)
    return jsonify({"ok": True, "prizes": wheel_prizes(db),
                    "spun": spun, "limit": limit,
                    "cooldown_min": cooldown, "remaining_min": remaining_min,
                    "can_spin": not (limit > 0 and spun >= limit) and remaining_min <= 0,
                    "limit_reached": limit > 0 and spun >= limit})


@app.route("/api/wheel/spin", methods=["POST"])
@login_required
def api_wheel_spin():
    db = get_db()
    u = current_user(db)
    s = get_settings(db)
    limit = int(s["wheel_spins_per_day"])
    cooldown = int(s["wheel_cooldown_min"])
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    spun = db.execute(
        "SELECT COUNT(*) FROM wheel_spins"
        " WHERE user_id = ? AND substr(created_at, 1, 10) = ?",
        (u["id"], today)).fetchone()[0]
    if limit > 0 and spun >= limit:
        return jsonify({"ok": False,
                        "error": f"daily spin limit reached ({limit} spins today)"}), 400
    last = db.execute(
        "SELECT MAX(created_at) FROM wheel_spins WHERE user_id = ?",
        (u["id"],)).fetchone()[0]
    if last:
        mins = (datetime.now(timezone.utc) - parse_iso(last)).total_seconds() / 60.0
        if mins < cooldown:
            return jsonify({"ok": False,
                            "error": f"next spin in {round(cooldown - mins, 1)} min"}), 400
    prizes = wheel_prizes(db)
    segment = secrets.randbelow(len(prizes))
    reward = prizes[segment]
    db.execute(
        "INSERT INTO wheel_spins(user_id, reward, segment, created_at) VALUES(?, ?, ?, ?)",
        (u["id"], reward, segment, now_iso()))
    credit_coins(db, u["id"], reward, "wheel")
    db.commit()
    return jsonify({"ok": True, "reward": reward, "segment": segment,
                    "prizes": prizes, "spun": spun + 1, "limit": limit})


@app.route("/api/watch-ad", methods=["POST"])
@login_required
def api_watch_ad():
    db = get_db()
    u = current_user(db)
    s = get_settings(db)
    limit = int(s["watch_ads_daily_limit"])
    cooldown = int(s["watch_ads_cooldown_min"])
    reward = int(s["watch_ads_reward"])
    provider = s["watch_ads_provider"] or "Unity"
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    watched = db.execute(
        "SELECT COUNT(*) FROM ad_rewards"
        " WHERE user_id = ? AND substr(created_at, 1, 10) = ?",
        (u["id"], today)).fetchone()[0]
    if limit > 0 and watched >= limit:
        return jsonify({"ok": False,
                        "error": f"daily ad limit reached ({limit} ads today)"}), 400
    last = db.execute(
        "SELECT MAX(created_at) FROM ad_rewards WHERE user_id = ?",
        (u["id"],)).fetchone()[0]
    if last:
        mins = (datetime.now(timezone.utc) - parse_iso(last)).total_seconds() / 60.0
        if mins < cooldown:
            return jsonify({"ok": False,
                            "error": f"next ad in {round(cooldown - mins, 1)} min"}), 400
    db.execute(
        "INSERT INTO ad_rewards(user_id, provider, points, created_at) VALUES(?, ?, ?, ?)",
        (u["id"], provider, reward, now_iso()))
    credit_coins(db, u["id"], reward, f"ad:{provider}")
    db.commit()
    return jsonify({"ok": True, "reward": reward, "provider": provider,
                    "watched": watched + 1, "limit": limit})


@app.route("/api/settings")
@login_required
def api_public_settings():
    db = get_db()
    s = get_settings(db)
    try:
        import json as _json
        cats = _json.loads(s["categories"])
        if not isinstance(cats, list):
            raise ValueError
    except Exception:
        cats = ["Daily", "Video", "Social", "Special"]
    keys = ["app_name", "mining_enabled", "coins_per_hour", "max_mining_hours",
            "min_claim_interval_min", "l1_pct", "l2_pct", "l3_pct",
            "withdrawals_enabled", "min_withdrawal_points",
            "coin_name", "coin_symbol", "points_per_coin", "min_convert_points",
            "daily_bonus_points", "daily_bonus_cooldown_hours",
            "rewarded_bonus_points", "interstitial_every_n_tasks",
            "tap_points_per_tap", "tap_daily_limit",
            "swap_points_per_coin", "swap_min_coins", "leaderboard_enabled",
            "home_banner_enabled", "slider_enabled", "slider_interval_sec", "watch_ads_daily_limit", "watch_ads_cooldown_min",
            "watch_ads_reward", "watch_ads_provider",
            "transfers_enabled", "transfer_min", "transfer_max",
            "transfer_fee_percent", "transfer_daily_limit",
            "wheel_spins_per_day", "wheel_min_reward", "wheel_max_reward",
            "wheel_cooldown_min", "tap_level_bonus",
            "withdrawal_gating_enabled", "tier_bronze_invites", "tier_bronze_limit",
            "tier_silver_invites", "tier_silver_limit", "tier_gold_invites",
            "tier_gold_limit", "tier_diamond_invites", "tier_diamond_limit",
            "milestone_thresholds", "milestone_bonuses", "welcome_bonus",
            "invite_task_reward", "streak_bonus_per_day", "streak_bonus_cap",
            "invite_token_reward", "welcome_token_gld", "milestone_token_bonuses",
            "inviter_rank_thresholds", "inviter_rank_multipliers",
            "weekly_pool_gld", "weekly_pool_split",
            "social_rate_gld_per_1000", "social_cap_per_post", "social_cap_per_week",
            "min_token_withdrawal", "token_withdrawal_fee_pct",
            "token_withdrawal_review_threshold",
            "latest_version_code", "update_note",
               "tap_level_thresholds", "weekly_pool_pcts",
               "website_url", "apk_download_url",
               "social_approval_mode",
               "withdrawal_method_bank", "withdrawal_method_mobile_wallet",
               "withdrawal_method_crypto",
               "token_transfer_enabled", "token_transfer_min", "token_transfer_max",
               "token_transfer_fee_pct", "token_transfer_daily_limit"]
    return jsonify({"ok": True,
                    "settings": {k: s[k] for k in keys},
                    "categories": cats})




# ---------------------------------------------------------------- v2.0: Home banner slider
SLIDE_GRADIENTS = ("gold", "blue", "green", "purple")
SLIDE_ACTIONS = ("spin", "watch", "tasks", "invite", "wallet", "social", "leaderboard", "none")


def _slide_payload(d):
    title = (d.get("title") or "").strip()
    if not title:
        return None, "title required"
    gradient = (d.get("gradient") or "gold").strip()
    if gradient not in SLIDE_GRADIENTS:
        gradient = "gold"
    action = (d.get("cta_action") or "none").strip()
    if action not in SLIDE_ACTIONS and not action.startswith("https://"):
        action = "none"
    try:
        order = int(d.get("sort_order") or 0)
    except (TypeError, ValueError):
        order = 0
    return {
        "title": title[:200],
        "subtitle": (d.get("subtitle") or "").strip()[:500],
        "image_url": (d.get("image_url") or "").strip()[:500],
        "gradient": gradient,
        "cta_label": (d.get("cta_label") or "").strip()[:100],
        "cta_action": action[:500],
        "active": 1 if d.get("active", True) else 0,
        "sort_order": order,
    }, None


@app.route("/api/slides")
@login_required
def api_slides():
    db = get_db()
    rows = db.execute(
        "SELECT * FROM home_slides WHERE active = 1 ORDER BY sort_order ASC, id ASC").fetchall()
    s = get_settings(db)
    return jsonify({"ok": True, "slides": [dict(r) for r in rows],
                    "slider_enabled": s.get("slider_enabled", "1"),
                    "slider_interval_sec": s.get("slider_interval_sec", "4")})


@app.route("/api/admin/slides", methods=["GET", "POST"])
@admin_api_required
def api_admin_slides():
    db = get_db()
    if request.method == "POST":
        d = request.get_json(force=True, silent=True) or {}
        payload, err = _slide_payload(d)
        if err:
            return jsonify({"ok": False, "error": err}), 400
        cur = db.execute(
            "INSERT INTO home_slides(title, subtitle, image_url, gradient, cta_label,"
            " cta_action, active, sort_order, created_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (payload["title"], payload["subtitle"], payload["image_url"], payload["gradient"],
             payload["cta_label"], payload["cta_action"], payload["active"],
             payload["sort_order"], now_iso()))
        db.commit()
        return jsonify({"ok": True, "id": cur.lastrowid})
    rows = db.execute("SELECT * FROM home_slides ORDER BY sort_order ASC, id ASC").fetchall()
    return jsonify({"ok": True, "slides": [dict(r) for r in rows]})


@app.route("/api/admin/slides/<int:sid>", methods=["PUT", "DELETE"])
@admin_api_required
def api_admin_slide_one(sid):
    db = get_db()
    row = db.execute("SELECT * FROM home_slides WHERE id = ?", (sid,)).fetchone()
    if not row:
        return jsonify({"ok": False, "error": "not found"}), 404
    if request.method == "DELETE":
        db.execute("DELETE FROM home_slides WHERE id = ?", (sid,))
        db.commit()
        return jsonify({"ok": True})
    d = request.get_json(force=True, silent=True) or {}
    merged = dict(row)
    merged.update(d)
    payload, err = _slide_payload(merged)
    if err:
        return jsonify({"ok": False, "error": err}), 400
    db.execute(
        "UPDATE home_slides SET title=?, subtitle=?, image_url=?, gradient=?,"
        " cta_label=?, cta_action=?, active=?, sort_order=? WHERE id=?",
        (payload["title"], payload["subtitle"], payload["image_url"], payload["gradient"],
         payload["cta_label"], payload["cta_action"], payload["active"],
         payload["sort_order"], sid))
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/admin/slides/<int:sid>/toggle", methods=["POST"])
@admin_api_required
def api_admin_slide_toggle(sid):
    db = get_db()
    row = db.execute("SELECT * FROM home_slides WHERE id = ?", (sid,)).fetchone()
    if not row:
        return jsonify({"ok": False, "error": "not found"}), 404
    db.execute("UPDATE home_slides SET active = ? WHERE id = ?",
               (0 if row["active"] else 1, sid))
    db.commit()
    return jsonify({"ok": True, "active": 0 if row["active"] else 1})


@app.route("/api/admin/slides/reorder", methods=["POST"])
@admin_api_required
def api_admin_slides_reorder():
    db = get_db()
    d = request.get_json(force=True, silent=True) or {}
    ids = d.get("ids") or []
    if not isinstance(ids, list):
        return jsonify({"ok": False, "error": "ids must be a list"}), 400
    for i, sid in enumerate(ids):
        try:
            db.execute("UPDATE home_slides SET sort_order = ? WHERE id = ?", (i + 1, int(sid)))
        except (TypeError, ValueError):
            continue
    db.commit()
    return jsonify({"ok": True})


# ---------------------------------------------------------------- v1.9 user API
@app.route("/api/live-stats")
@login_required
def api_live_stats():
    db = get_db()
    return jsonify({"ok": True, **live_stats(db)})


@app.route("/api/announcements")
@login_required
def api_announcements():
    db = get_db()
    rows = db.execute(
        "SELECT * FROM announcements WHERE active = 1 ORDER BY created_at DESC LIMIT 50").fetchall()
    return jsonify({"ok": True, "announcements": [dict(r) for r in rows],
                    "latest_version_code": get_setting(db, "latest_version_code"),
                    "update_note": get_setting(db, "update_note")})


@app.route("/api/weekly-pool")
@login_required
def api_weekly_pool():
    db = get_db()
    u = current_user(db)
    s = get_settings(db)
    view = weekly_pool_view(db, u["id"], s)
    db.commit()
    return jsonify({"ok": True, **view})


@app.route("/api/social-posts", methods=["GET", "POST"])
@login_required
def api_social_posts():
    db = get_db()
    u = current_user(db)
    if request.method == "POST":
        d = request.get_json(force=True, silent=True) or {}
        s = get_settings(db)
        platform = (d.get("platform") or "Other").strip()[:30]
        if platform not in ("TikTok", "YouTube", "Instagram", "Facebook", "Other"):
            platform = "Other"
        link = (d.get("link") or "").strip()[:500]
        try:
            views = int(d.get("views_claimed") or 0)
        except (TypeError, ValueError):
            views = 0
        # v1.1 (v22): link is COMPULSORY and must be a real URL.
        if not link or not (link.startswith("http://") or link.startswith("https://")):
            return jsonify({"ok": False, "error": "a valid post/video link starting with http:// or https:// is required"}), 400
        if views < 0:
            return jsonify({"ok": False, "error": "views claimed cannot be negative"}), 400
        # v1.1 (v22): team-set approval mode. Auto approves instantly at
        # claimed views with the EXISTING per-post + weekly caps; manual
        # keeps the pending → team-verification flow.
        if s["social_approval_mode"] == "auto":
            amount, capped = social_credit(db, u["id"], views, s)
            if amount > 0:
                credit_tokens(db, u["id"], amount, "social_reward",
                              f"{platform} post (auto-approved, {views} views)")
            cur = db.execute(
                "INSERT INTO social_posts(user_id, platform, link, views_claimed, views_verified, token_amount, status, decided_at, created_at)"
                " VALUES(?, ?, ?, ?, ?, ?, 'approved', ?, ?)",
                (u["id"], platform, link, views, views, amount, now_iso(), now_iso()))
            db.commit()
            return jsonify({"ok": True, "id": cur.lastrowid, "status": "approved",
                            "token_amount": amount, "capped_by": capped})
        cur = db.execute(
            "INSERT INTO social_posts(user_id, platform, link, views_claimed, status, created_at)"
            " VALUES(?, ?, ?, ?, 'pending', ?)",
            (u["id"], platform, link, views, now_iso()))
        db.commit()
        return jsonify({"ok": True, "id": cur.lastrowid, "status": "pending"})
    rows = db.execute(
        "SELECT * FROM social_posts WHERE user_id = ? ORDER BY created_at DESC LIMIT 50",
        (u["id"],)).fetchall()
    return jsonify({"ok": True, "posts": [dict(r) for r in rows]})


@app.route("/api/pushed-tasks")
@login_required
def api_pushed_tasks():
    db = get_db()
    u = current_user(db)
    return jsonify({"ok": True, "tasks": pushed_tasks_active(db, u["id"])})


@app.route("/api/pushed-tasks/<int:task_id>/claim", methods=["POST"])
@login_required
def api_pushed_claim(task_id):
    db = get_db()
    u = current_user(db)
    active = {t["id"]: t for t in pushed_tasks_active(db, u["id"])}
    t = active.get(task_id)
    if not t:
        return jsonify({"ok": False, "error": "task not available or expired"}), 400
    if t["claimed"]:
        return jsonify({"ok": False, "error": "already claimed"}), 400
    db.execute(
        "INSERT INTO pushed_claims(user_id, task_id, created_at) VALUES(?, ?, ?)",
        (u["id"], task_id, now_iso()))
    if t["reward_type"] == "tokens":
        credit_tokens(db, u["id"], float(t["reward_value"]), "pushed_task",
                      t["title"])
    else:
        credit_coins(db, u["id"], int(float(t["reward_value"])),
                     f"pushed:{t['title']}")
    db.commit()
    return jsonify({"ok": True, "reward_value": t["reward_value"],
                    "reward_type": t["reward_type"]})


@app.route("/api/token-withdraw", methods=["POST"])
@login_required
def api_token_withdraw():
    db = get_db()
    u = current_user(db)
    s = get_settings(db)
    d = request.get_json(force=True, silent=True) or {}
    method = (d.get("method") or "").strip()
    account = (d.get("account") or "").strip()
    try:
        amount = float(d.get("amount_gld") or 0)
    except (TypeError, ValueError):
        amount = 0
    if method not in ("Bank Transfer", "Mobile Wallet", "Crypto Wallet",
                      "JazzCash", "Easypaisa"):
        return jsonify({"ok": False, "error": "invalid method"}), 400
    if not account:
        return jsonify({"ok": False, "error": "account number required"}), 400
    # v1.1 (v22): team-toggled withdrawal methods (same toggles as points).
    _method_key = {"Bank Transfer": "withdrawal_method_bank",
                   "Mobile Wallet": "withdrawal_method_mobile_wallet",
                   "Crypto Wallet": "withdrawal_method_crypto"}.get(method)
    if _method_key and s[_method_key] != "1":
        return jsonify({"ok": False,
                        "error": "this withdrawal method is currently paused"}), 400
    min_v = _fval(s["min_token_withdrawal"], 5)
    if amount < min_v:
        return jsonify({"ok": False, "error": f"minimum token withdrawal is {min_v} GLD"}), 400
    if amount > u["coin_balance"] + 1e-9:
        return jsonify({"ok": False, "error": "not enough GLD"}), 400
    fee_pct = _fval(s["token_withdrawal_fee_pct"], 0)
    fee = round(amount * fee_pct / 100, 4)
    threshold = _fval(s["token_withdrawal_review_threshold"], 50)
    # Deduction happens at request time. At or below the review threshold
    # the payout is automatic; above it the request waits for review.
    status = "approved" if amount <= threshold else "pending"
    debit_tokens(db, u["id"], amount, "token_withdraw_request",
                 f"{method} {account} ({status})")
    cur = db.execute(
        "INSERT INTO token_withdrawals(user_id, method, account, amount, fee, status, created_at)"
        " VALUES(?, ?, ?, ?, ?, ?, ?)",
        (u["id"], method, account, amount, fee, status, now_iso()))
    if status == "approved":
        db.execute("UPDATE token_withdrawals SET decided_at = ? WHERE id = ?",
                   (now_iso(), cur.lastrowid))
    db.commit()
    return jsonify({"ok": True, "id": cur.lastrowid, "status": status,
                    "amount": amount, "fee": fee, "payout": round(amount - fee, 4)})


@app.route("/api/token-withdrawals")
@login_required
def api_token_withdrawals():
    db = get_db()
    u = current_user(db)
    rows = db.execute(
        "SELECT * FROM token_withdrawals WHERE user_id = ? ORDER BY created_at DESC LIMIT 50",
        (u["id"],)).fetchall()
    return jsonify({"ok": True, "withdrawals": [dict(r) for r in rows]})


# ---------------------------------------------------------------- v1.9 team API
@app.route("/api/admin/insights")
@admin_api_required
def api_admin_insights():
    """v2.1 Insights — team-only summary + per-user table. NEVER served to
    the user app: this route requires the team key (admin_api_required)
    and returns other users' balances, so no user endpoint may call it.
    Totals are computed live from the DB, never hardcoded. Query params:
    q (username substring), sort (username|points|gld|valid_invites|streak|
    last_active|created_at), order (asc|desc)."""
    db = get_db()
    settings = get_settings(db)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    monday = week_monday(datetime.now(timezone.utc).date())
    week_start = monday.isoformat()
    now = datetime.now(timezone.utc)
    day_ago = (now - timedelta(hours=24)).isoformat()
    total_users = db.execute(
        "SELECT COUNT(*) FROM users WHERE is_admin = 0").fetchone()[0]
    new_today = db.execute(
        "SELECT COUNT(*) FROM users WHERE is_admin = 0 AND substr(created_at, 1, 10) = ?",
        (today,)).fetchone()[0]
    new_week = db.execute(
        "SELECT COUNT(*) FROM users WHERE is_admin = 0 AND substr(created_at, 1, 10) >= ?",
        (week_start,)).fetchone()[0]
    active_24h = db.execute(
        "SELECT COUNT(*) FROM users WHERE is_admin = 0 AND last_active IS NOT NULL AND last_active >= ?",
        (day_ago,)).fetchone()[0]
    total_points = db.execute(
        "SELECT COALESCE(SUM(coins), 0) FROM users WHERE is_admin = 0").fetchone()[0]
    total_gld = db.execute(
        "SELECT COALESCE(SUM(coin_balance), 0) FROM users WHERE is_admin = 0").fetchone()[0]
    valid_total = 0
    for r in db.execute("SELECT id FROM users WHERE is_admin = 0").fetchall():
        valid_total += valid_invite_count(db, r["id"])
    pend = db.execute(
        "SELECT COUNT(*), COALESCE(SUM(coins_used), 0) FROM withdrawals WHERE status = 'pending'").fetchone()
    rows = []
    for r in db.execute("SELECT * FROM users WHERE is_admin = 0").fetchall():
        tier = withdrawal_tier(db, r["id"], settings)
        si = streak_info(db, r["id"], settings)
        rows.append({
            "id": r["id"], "username": r["username"],
            "points": r["coins"], "gld": round(r["coin_balance"] or 0, 4),
            "valid_invites": valid_invite_count(db, r["id"]),
            "total_invites": db.execute(
                "SELECT COUNT(*) FROM users WHERE referred_by = ?", (r["id"],)).fetchone()[0],
            "withdrawal_tier": tier["name"],
            "streak": si["current"],
            "last_active": r["last_active"], "created_at": r["created_at"],
            "is_banned": bool(r["is_banned"]),
        })
    q = (request.args.get("q") or "").strip().lower()
    if q:
        rows = [x for x in rows if q in x["username"].lower()]
    sort_key = request.args.get("sort") or "points"
    sort_map = {"username": "username", "points": "points", "gld": "gld",
                "valid_invites": "valid_invites", "streak": "streak",
                "last_active": "last_active", "created_at": "created_at"}
    key = sort_map.get(sort_key, "points")
    reverse = (request.args.get("order") or "desc") != "asc"
    rows.sort(key=lambda x: (x[key] is None, x[key]), reverse=reverse)
    return jsonify({"ok": True, "summary": {
        "total_users": total_users, "new_today": new_today,
        "new_this_week": new_week, "active_24h": active_24h,
        "total_points": total_points, "total_gld": round(total_gld, 4),
        "total_valid_invites": valid_total,
        "pending_withdrawals": {"count": pend[0], "total_points": pend[1]},
    }, "users": rows})


@app.route("/api/admin/live-stats")
@admin_api_required
def api_admin_live_stats():
    db = get_db()
    return jsonify({"ok": True, **live_stats(db)})


@app.route("/api/admin/announcements", methods=["GET", "POST"])
@admin_api_required
def api_admin_announcements():
    db = get_db()
    if request.method == "POST":
        d = request.get_json(force=True, silent=True) or {}
        title = (d.get("title") or "").strip()
        if not title:
            return jsonify({"ok": False, "error": "title required"}), 400
        typ = (d.get("type") or "info").strip()
        if typ not in ("info", "update", "promo"):
            typ = "info"
        cur = db.execute(
            "INSERT INTO announcements(title, message, type, link, active, created_at)"
            " VALUES(?, ?, ?, ?, ?, ?)",
            (title, (d.get("message") or "").strip()[:2000],
             typ, (d.get("link") or "").strip()[:500],
             1 if d.get("active", True) else 0, now_iso()))
        db.commit()
        return jsonify({"ok": True, "id": cur.lastrowid})
    rows = db.execute("SELECT * FROM announcements ORDER BY created_at DESC LIMIT 100").fetchall()
    return jsonify({"ok": True, "announcements": [dict(r) for r in rows]})


@app.route("/api/admin/announcements/<int:aid>/toggle", methods=["POST"])
@admin_api_required
def api_admin_ann_toggle(aid):
    db = get_db()
    row = db.execute("SELECT * FROM announcements WHERE id = ?", (aid,)).fetchone()
    if not row:
        return jsonify({"ok": False, "error": "not found"}), 404
    db.execute("UPDATE announcements SET active = ? WHERE id = ?",
               (0 if row["active"] else 1, aid))
    db.commit()
    return jsonify({"ok": True, "active": 0 if row["active"] else 1})


@app.route("/api/admin/announcements/<int:aid>/delete", methods=["POST"])
@admin_api_required
def api_admin_ann_delete(aid):
    db = get_db()
    db.execute("DELETE FROM announcements WHERE id = ?", (aid,))
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/admin/pushed-tasks", methods=["GET", "POST"])
@admin_api_required
def api_admin_pushed_tasks():
    db = get_db()
    if request.method == "POST":
        d = request.get_json(force=True, silent=True) or {}
        title = (d.get("title") or "").strip()
        desc = (d.get("description") or "").strip()
        if not title or not desc:
            return jsonify({"ok": False, "error": "title and description required"}), 400
        try:
            val = float(d.get("reward_value") or 0)
            hours = float(d.get("expiry_hours") or 24)
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "bad reward or expiry"}), 400
        if val <= 0 or hours <= 0:
            return jsonify({"ok": False, "error": "reward and expiry must be positive"}), 400
        rtype = (d.get("reward_type") or "points").strip()
        if rtype not in ("points", "tokens"):
            rtype = "points"
        cur = db.execute(
            "INSERT INTO pushed_tasks(title, description, reward_value, reward_type, expiry_hours, active, created_at)"
            " VALUES(?, ?, ?, ?, ?, 1, ?)",
            (title, desc, val, rtype, hours, now_iso()))
        db.commit()
        return jsonify({"ok": True, "id": cur.lastrowid})
    rows = db.execute("SELECT * FROM pushed_tasks ORDER BY created_at DESC LIMIT 100").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["claims"] = db.execute(
            "SELECT COUNT(*) FROM pushed_claims WHERE task_id = ?", (r["id"],)).fetchone()[0]
        out.append(d)
    return jsonify({"ok": True, "tasks": out})


@app.route("/api/admin/pushed-tasks/<int:tid>/toggle", methods=["POST"])
@admin_api_required
def api_admin_pushed_toggle(tid):
    db = get_db()
    row = db.execute("SELECT * FROM pushed_tasks WHERE id = ?", (tid,)).fetchone()
    if not row:
        return jsonify({"ok": False, "error": "not found"}), 404
    db.execute("UPDATE pushed_tasks SET active = ? WHERE id = ?",
               (0 if row["active"] else 1, tid))
    db.commit()
    return jsonify({"ok": True, "active": 0 if row["active"] else 1})


@app.route("/api/admin/pushed-tasks/<int:tid>/delete", methods=["POST"])
@admin_api_required
def api_admin_pushed_delete(tid):
    db = get_db()
    db.execute("DELETE FROM pushed_claims WHERE task_id = ?", (tid,))
    db.execute("DELETE FROM pushed_tasks WHERE id = ?", (tid,))
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/admin/social-posts")
@admin_api_required
def api_admin_social_posts():
    db = get_db()
    rows = db.execute(
        "SELECT sp.*, u.username FROM social_posts sp JOIN users u ON u.id = sp.user_id"
        " ORDER BY sp.created_at DESC LIMIT 100").fetchall()
    return jsonify({"ok": True, "posts": [dict(r) for r in rows]})


@app.route("/api/admin/social-posts/<int:pid>/decide", methods=["POST"])
@admin_api_required
def api_admin_social_decide(pid):
    db = get_db()
    d = request.get_json(force=True, silent=True) or {}
    row = db.execute("SELECT * FROM social_posts WHERE id = ?", (pid,)).fetchone()
    if not row:
        return jsonify({"ok": False, "error": "not found"}), 404
    if row["status"] != "pending":
        return jsonify({"ok": False, "error": "already decided"}), 400
    action = (d.get("action") or "").strip()
    if action == "reject":
        db.execute(
            "UPDATE social_posts SET status='rejected', reason=?, decided_at=? WHERE id=?",
            ((d.get("reason") or "").strip()[:300], now_iso(), pid))
        db.commit()
        return jsonify({"ok": True, "status": "rejected"})
    if action != "approve":
        return jsonify({"ok": False, "error": "action must be approve or reject"}), 400
    try:
        verified = int(d.get("verified_views") or 0)
    except (TypeError, ValueError):
        verified = 0
    if verified < 0:
        return jsonify({"ok": False, "error": "verified views cannot be negative"}), 400
    s = get_settings(db)
    amount, capped = social_credit(db, row["user_id"], verified, s)
    if amount > 0:
        credit_tokens(db, row["user_id"], amount, "social_reward",
                      f"{row['platform']} post #{pid} ({verified} verified views)")
    db.execute(
        "UPDATE social_posts SET status='approved', views_verified=?, token_amount=?, decided_at=? WHERE id=?",
        (verified, amount, now_iso(), pid))
    db.commit()
    return jsonify({"ok": True, "status": "approved", "token_amount": amount,
                    "capped_by": capped})


@app.route("/api/admin/token-withdrawals")
@admin_api_required
def api_admin_token_withdrawals():
    db = get_db()
    rows = db.execute(
        "SELECT tw.*, u.username FROM token_withdrawals tw JOIN users u ON u.id = tw.user_id"
        " ORDER BY tw.created_at DESC LIMIT 100").fetchall()
    return jsonify({"ok": True, "withdrawals": [dict(r) for r in rows]})


@app.route("/api/admin/token-withdrawals/<int:wid>/approve", methods=["POST"])
@admin_api_required
def api_admin_token_wd_approve(wid):
    db = get_db()
    d = request.get_json(force=True, silent=True) or {}
    row = db.execute("SELECT * FROM token_withdrawals WHERE id = ?", (wid,)).fetchone()
    if not row:
        return jsonify({"ok": False, "error": "not found"}), 404
    if row["status"] != "pending":
        return jsonify({"ok": False, "error": "not pending"}), 400
    db.execute(
        "UPDATE token_withdrawals SET status='approved', note=?, decided_at=? WHERE id=?",
        ((d.get("note") or "").strip()[:200], now_iso(), wid))
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/admin/token-withdrawals/<int:wid>/reject", methods=["POST"])
@admin_api_required
def api_admin_token_wd_reject(wid):
    db = get_db()
    d = request.get_json(force=True, silent=True) or {}
    row = db.execute("SELECT * FROM token_withdrawals WHERE id = ?", (wid,)).fetchone()
    if not row:
        return jsonify({"ok": False, "error": "not found"}), 404
    if row["status"] != "pending":
        return jsonify({"ok": False, "error": "not pending"}), 400
    # Full refund of the amount deducted at request time.
    db.execute("UPDATE users SET coin_balance = coin_balance + ? WHERE id = ?",
               (row["amount"], row["user_id"]))
    db.execute(
        "INSERT INTO token_ledger(user_id, amount, source, note, created_at)"
        " VALUES(?, ?, 'token_withdraw_refund', 'token withdrawal rejected', ?)",
        (row["user_id"], row["amount"], now_iso()))
    db.execute(
        "UPDATE token_withdrawals SET status='rejected', note=?, decided_at=? WHERE id=?",
        ((d.get("note") or "").strip()[:200], now_iso(), wid))
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/admin/weekly-pool", methods=["GET", "POST"])
@admin_api_required
def api_admin_weekly_pool():
    db = get_db()
    s = get_settings(db)
    if request.method == "POST":
        created = distribute_weekly_pool(db, s)
        db.commit()
        return jsonify({"ok": True, "distributed": created})
    dists = db.execute(
        "SELECT * FROM weekly_distributions ORDER BY week_start DESC LIMIT 20").fetchall()
    out = []
    for dist in dists:
        winners = db.execute(
            "SELECT wp.*, u.username FROM weekly_payouts wp JOIN users u ON u.id = wp.user_id"
            " WHERE wp.distribution_id = ? ORDER BY wp.rank", (dist["id"],)).fetchall()
        d = dict(dist)
        d["winners"] = [dict(w) for w in winners]
        out.append(d)
    cur = weekly_valid_invites(db, week_monday(datetime.now(timezone.utc).date()))[:10]
    cur_top = []
    for i, w in enumerate(cur):
        u = db.execute("SELECT username FROM users WHERE id = ?",
                       (w["user_id"],)).fetchone()
        cur_top.append({"rank": i + 1, "username": u["username"] if u else "?",
                        "valid_invites": w["count"]})
    return jsonify({"ok": True, "pool_gld": _fval(s["weekly_pool_gld"], 100),
                    "split_type": s["weekly_pool_split"],
                    "current_week_top10": cur_top, "distributions": out})


# ---------------------------------------------------------------- schema + seed
SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    coins INTEGER NOT NULL DEFAULT 0,
    coin_balance REAL NOT NULL DEFAULT 0,
    referral_code TEXT UNIQUE NOT NULL,
    referred_by INTEGER REFERENCES users(id),
    is_admin INTEGER NOT NULL DEFAULT 0,
    is_banned INTEGER NOT NULL DEFAULT 0,
    last_active TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS mining_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    started_at TEXT NOT NULL,
    claimed_at TEXT,
    coins_claimed INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    reward INTEGER NOT NULL DEFAULT 0,
    task_type TEXT NOT NULL DEFAULT 'daily',
    enabled INTEGER NOT NULL DEFAULT 1,
    cooldown_hours REAL NOT NULL DEFAULT 24
);
CREATE TABLE IF NOT EXISTS task_completions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    task_id INTEGER NOT NULL REFERENCES tasks(id),
    completed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS commissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_user INTEGER NOT NULL REFERENCES users(id),
    to_user INTEGER NOT NULL REFERENCES users(id),
    level INTEGER NOT NULL,
    coins INTEGER NOT NULL,
    source TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS withdrawals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    method TEXT NOT NULL,
    account TEXT NOT NULL,
    amount_pkr REAL NOT NULL DEFAULT 0,
    amount_points INTEGER NOT NULL DEFAULT 0,
    coins_used INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    admin_note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    decided_at TEXT
);
CREATE TABLE IF NOT EXISTS balance_adjustments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    admin_id INTEGER NOT NULL REFERENCES users(id),
    user_id INTEGER NOT NULL REFERENCES users(id),
    delta INTEGER NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
"""

NEW_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    points_spent INTEGER NOT NULL,
    coins_received REAL NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS bonus_claims (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    points INTEGER NOT NULL,
    claimed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ad_providers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT UNIQUE NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 0,
    placement_type TEXT NOT NULL DEFAULT 'banner',
    unit_id TEXT NOT NULL DEFAULT '',
    snippet TEXT NOT NULL DEFAULT '',
    image_url TEXT NOT NULL DEFAULT '',
    click_url TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS ad_rewards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    provider TEXT NOT NULL,
    points INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
-- v1.6 tables for the mobile app server mode (tap tracking, P2P
-- transfers, two-way swap history). Created by migrate() on every run.
CREATE TABLE IF NOT EXISTS tap_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    points INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
-- v1.7: Daily Spin Wheel records (one row per spin).
CREATE TABLE IF NOT EXISTS wheel_spins (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    reward INTEGER NOT NULL,
    segment INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS transfers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_user INTEGER NOT NULL REFERENCES users(id),
    to_user INTEGER NOT NULL REFERENCES users(id),
    points INTEGER NOT NULL,
    fee INTEGER NOT NULL DEFAULT 0,
    received INTEGER NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    currency TEXT NOT NULL DEFAULT 'points',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS swap_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    direction TEXT NOT NULL,
    points INTEGER NOT NULL,
    coins REAL NOT NULL,
    created_at TEXT NOT NULL
);
-- v1.9: token ledger, token milestones, announcements, pushed tasks,
-- social posts (manual verification), token withdrawals, weekly pool.
CREATE TABLE IF NOT EXISTS token_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    amount REAL NOT NULL,
    source TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS token_milestone_awards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    threshold INTEGER NOT NULL,
    bonus_tokens REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    UNIQUE(user_id, threshold)
);
CREATE TABLE IF NOT EXISTS announcements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    message TEXT NOT NULL DEFAULT '',
    type TEXT NOT NULL DEFAULT 'info',
    link TEXT NOT NULL DEFAULT '',
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pushed_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    reward_value REAL NOT NULL DEFAULT 0,
    reward_type TEXT NOT NULL DEFAULT 'points',
    expiry_hours REAL NOT NULL DEFAULT 24,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pushed_claims (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    task_id INTEGER NOT NULL REFERENCES pushed_tasks(id),
    created_at TEXT NOT NULL,
    UNIQUE(user_id, task_id)
);
CREATE TABLE IF NOT EXISTS social_posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    platform TEXT NOT NULL DEFAULT 'Other',
    link TEXT NOT NULL DEFAULT '',
    views_claimed INTEGER NOT NULL DEFAULT 0,
    views_verified INTEGER,
    token_amount REAL NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending',
    reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    decided_at TEXT
);
CREATE TABLE IF NOT EXISTS token_withdrawals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    method TEXT NOT NULL,
    account TEXT NOT NULL,
    amount REAL NOT NULL DEFAULT 0,
    fee REAL NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending',
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    decided_at TEXT
);
CREATE TABLE IF NOT EXISTS home_slides (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    subtitle TEXT NOT NULL DEFAULT '',
    image_url TEXT NOT NULL DEFAULT '',
    gradient TEXT NOT NULL DEFAULT 'gold',
    cta_label TEXT NOT NULL DEFAULT '',
    cta_action TEXT NOT NULL DEFAULT 'none',
    active INTEGER NOT NULL DEFAULT 1,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS weekly_distributions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    week_start TEXT NOT NULL UNIQUE,
    pool REAL NOT NULL DEFAULT 0,
    split_type TEXT NOT NULL DEFAULT 'weighted',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS weekly_payouts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    distribution_id INTEGER NOT NULL REFERENCES weekly_distributions(id),
    user_id INTEGER NOT NULL REFERENCES users(id),
    rank INTEGER NOT NULL,
    valid_invites INTEGER NOT NULL DEFAULT 0,
    share REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
-- v1.8: one row per awarded invite milestone (UNIQUE prevents double pay).
CREATE TABLE IF NOT EXISTS milestone_awards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    threshold INTEGER NOT NULL,
    bonus INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(user_id, threshold)
);
"""


def seed(db):
    for k, v in DEFAULT_SETTINGS.items():
        db.execute("INSERT OR IGNORE INTO settings(key, value) VALUES(?, ?)", (k, v))
    db.execute(
        "INSERT INTO users(username, password_hash, coins, referral_code, referred_by, is_admin, created_at)"
        " VALUES('admin', ?, 0, 'ADMIN001', NULL, 1, ?)",
        (generate_password_hash("admin123"), now_iso()))
    # Demo referral chain: demo1 -> demo2 -> demo3 (so demo3's earnings pay
    # Level 1 to demo2 and Level 2 to demo1; a demo3 referral pays Level 3 to demo1)
    def add_demo(username, coins, ref_by):
        code = make_referral_code(username)
        cur = db.execute(
            "INSERT INTO users(username, password_hash, coins, referral_code, referred_by, created_at)"
            " VALUES(?, ?, ?, ?, ?, ?)",
            (username, generate_password_hash("demo123"), coins, code, ref_by, now_iso()))
        return cur.lastrowid
    d1 = add_demo("demo1", 500, None)
    d2 = add_demo("demo2", 6000, d1)
    d3 = add_demo("demo3", 100, d2)
    tasks = [
        ("Daily Check-in", "Open the app today and collect your reward.", 50, "daily"),
        ("Watch Video", "Watch a short promo video to the end.", 30, "video"),
        ("Visit Sponsor Link", "Open the sponsor link and come back.", 20, "link"),
        ("Invite a Friend", "Share your referral code and get a friend to join.", 100, "invite"),
        ("Share the App", "Share the app link with your friends.", 40, "share"),
        (INVITE_DAILY_TITLE, "Invite a friend today and have them complete a task - this task completes automatically when you gain a valid invite today.", 200, "daily"),
    ]
    for t in tasks:
        db.execute(
            "INSERT INTO tasks(title, description, reward, task_type, enabled, cooldown_hours) VALUES(?, ?, ?, ?, 1, 24)", t)
    # One pending withdrawal so the admin panel has something to act on.
    db.execute(
        "INSERT INTO withdrawals(user_id, method, account, amount_pkr, amount_points, coins_used, status, created_at)"
        " VALUES(?, 'JazzCash', '03001234567', 0, 5000, 5000, 'pending', ?)",
        (d2, now_iso()))
    db.execute("UPDATE users SET coins = coins - 5000 WHERE id = ?", (d2,))


# Initialize on import so production servers (gunicorn) get a ready DB.
# init_db() is idempotent: schema uses IF NOT EXISTS, seeding runs only
# on a fresh database, migrate() runs on every call.
init_db()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    host = os.environ.get("HOST", "127.0.0.1")
    print(f"Earnings app running on http://{host}:{port}")
    print("Admin login: admin / admin123   |   Demo users: demo1/demo2/demo3 / demo123")
    app.run(host=host, port=port, debug=False)
