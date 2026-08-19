"""
Discord Wordle Tracker Bot

Reads daily results posted by the WordleAPP bot, parses scores,
updates a persistent scoreboard, and posts a daily summary + all-time leaderboard.
"""

import os
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from collections import defaultdict

import discord

# ── Config ──────────────────────────────────────────────────────────────────

DISCORD_TOKEN = os.environ["DISCORD_BOT_TOKEN"]
GUILD_ID = int(os.environ["DISCORD_GUILD_ID"])
SUMMARY_CHANNEL_ID = int(os.environ["DISCORD_SUMMARY_CHANNEL_ID"])
DATA_FILE = Path(os.environ.get("DATA_FILE", "data/scoreboard.json"))

# How far back to scan for results (hours)
SCAN_WINDOW_HOURS = int(os.environ.get("SCAN_WINDOW_HOURS", "26"))

# Name of the Wordle bot whose messages we parse
WORDLE_BOT_NAME = os.environ.get("WORDLE_BOT_NAME", "Wordle")

# ── WordleAPP message parser ───────────────────────────────────────────────

# Matches score sections like "3/6:" or "X/6:"
SCORE_SECTION_RE = re.compile(r"([1-6X])/6:")

# Matches Discord user mentions like <@123456> or <@!123456>
MENTION_RE = re.compile(r"<@!?(\d+)>")


def parse_wordleapp_message(content: str, members: list[discord.Member]) -> dict[str, str]:
    """
    Parse a WordleAPP summary message. Handles both real Discord mentions
    (<@123456>) and plain text @mentions (@Username).

    Input format examples:
      "2/6: @Paćenik (čuo sam od mnogih) 3/6: @ja samuel ivana @Iza oblaka [65]"
      "2/6: <@123> 3/6: <@456> <@789>"

    Returns {user_id_str: score_str} e.g. {"123": "2", "456": "3", ...}
    """
    results = {}

    # Find all score section positions
    sections = list(SCORE_SECTION_RE.finditer(content))
    if not sections:
        return results

    # Build a lookup of display names -> user IDs (longest names first for greedy matching)
    name_to_id: dict[str, str] = {}
    for member in members:
        name_to_id[member.display_name.lower()] = str(member.id)
        name_to_id[member.name.lower()] = str(member.id)
    # Sort by length descending so "ja samuel ivana" matches before "ja"
    sorted_names = sorted(name_to_id.keys(), key=len, reverse=True)

    for i, match in enumerate(sections):
        score = match.group(1)  # "2", "3", "X", etc.
        start = match.end()
        end = sections[i + 1].start() if i + 1 < len(sections) else len(content)

        section_text = content[start:end]

        # Try real Discord mentions first
        discord_mentions = list(MENTION_RE.finditer(section_text))
        if discord_mentions:
            for mention in discord_mentions:
                user_id = mention.group(1)
                results[user_id] = score
        else:
            # Fall back to plain text @mentions — match against guild members
            section_lower = section_text.lower()
            matched_positions = set()  # avoid double-matching overlapping names

            for name in sorted_names:
                # Look for @name in the section text
                search_for = f"@{name}"
                pos = section_lower.find(search_for)
                while pos != -1:
                    # Check this position hasn't been claimed by a longer name
                    if not any(pos >= mp and pos < mp + ml for mp, ml in matched_positions):
                        uid = name_to_id[name]
                        if uid not in results:  # first match wins per user
                            results[uid] = score
                            print(f"      Matched '@{name}' -> user {uid} with score {score}")
                        matched_positions.add((pos, len(search_for)))
                    pos = section_lower.find(search_for, pos + 1)

    return results


def extract_streak_from_message(content: str) -> int | None:
    """Extract the group streak number from a WordleAPP message."""
    m = re.search(r"on an? (\d+) day streak", content)
    if m:
        return int(m.group(1))
    return None


# ── Persistent data ────────────────────────────────────────────────────────

def load_data() -> dict:
    if DATA_FILE.exists():
        return json.loads(DATA_FILE.read_text())
    return {
        "streak": 0,
        "last_date": None,
        "users": {},
        "history": [],
    }


def save_data(data: dict) -> None:
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False))


def ensure_user(data: dict, user_id: str, display_name: str) -> dict:
    """Make sure a user entry exists; update their display name."""
    if user_id not in data["users"]:
        data["users"][user_id] = {
            "display_name": display_name,
            "total_games": 0,
            "total_wins": 0,
            "best_count": 0,
            "score_counts": {"1": 0, "2": 0, "3": 0, "4": 0, "5": 0, "6": 0, "X": 0},
            "current_streak": 0,
            "best_streak": 0,
        }
    data["users"][user_id]["display_name"] = display_name
    return data["users"][user_id]


# ── Discord client ──────────────────────────────────────────────────────────

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

client = discord.Client(intents=intents)


async def find_wordleapp_message(guild: discord.Guild, after: datetime, members: list[discord.Member]) -> tuple[dict[str, str], int | None, discord.Message | None]:
    """
    Scan all channels for the most recent WordleAPP bot message.
    Returns (results_dict, streak, message) or ({}, None, None) if not found.
    """
    all_channels = list(guild.text_channels) + list(guild.voice_channels)
    print(f"Total channels to check: {len(all_channels)}")

    best_message = None
    best_results = {}
    best_streak = None

    for channel in all_channels:
        ch_type = "text" if isinstance(channel, discord.TextChannel) else "voice"
        perms = channel.permissions_for(guild.me)
        if not perms.read_messages or not perms.read_message_history:
            print(f"  SKIP (no perms): #{channel.name} [{ch_type}]")
            continue

        print(f"  Scanning: #{channel.name} [{ch_type}]")
        msg_count = 0
        try:
            async for message in channel.history(after=after, limit=500):
                msg_count += 1

                # Only look at messages from the WordleAPP bot
                if not message.author.bot:
                    continue
                if WORDLE_BOT_NAME.lower() not in message.author.name.lower():
                    continue

                print(f"    Found WordleAPP message in #{channel.name}: {message.content[:100]}...")

                results = parse_wordleapp_message(message.content, members)
                if results:
                    streak = extract_streak_from_message(message.content)
                    # Keep the most recent one
                    if best_message is None or message.created_at > best_message.created_at:
                        best_message = message
                        best_results = results
                        best_streak = streak
                        print(f"    Parsed {len(results)} results, streak={streak}")

            print(f"    Read {msg_count} messages in #{channel.name}")
        except discord.Forbidden:
            print(f"    FORBIDDEN: #{channel.name}")
            continue
        except discord.HTTPException as e:
            print(f"  ⚠ Error reading #{channel.name}: {e}")

    return best_results, best_streak, best_message


def build_summary(results: dict[str, dict], data: dict) -> str:
    """Build the daily summary message in the same format as WordleAPP."""
    if not results:
        return "No Wordle results found today. The streak is broken! 😢"

    # Group by score
    by_score: dict[str, list[str]] = defaultdict(list)
    for uid, info in results.items():
        by_score[info["score"]].append(f"<@{uid}>")

    # Sort: 1/6 first … 6/6 … X/6 last
    score_order = ["1", "2", "3", "4", "5", "6", "X"]
    parts = []
    for s in score_order:
        if s not in by_score:
            continue
        names = " ".join(by_score[s])
        parts.append(f"{s}/6: {names}")

    streak = data["streak"]
    if streak > 0:
        header = f"Your group is on a {streak} day streak! Here are yesterday's results:\n"
    else:
        header = "The streak was broken! Here are yesterday's results:\n"

    return header + "\n".join(parts)


def build_scoreboard(data: dict) -> str:
    """Build an all-time scoreboard, sorted by best count."""
    users = data["users"]
    if not users:
        return "No scores recorded yet."

    # Sort by best_count desc, then avg asc
    def sort_key(item):
        u = item[1]
        best_count = u.get("best_count", 0)
        wins = u["total_wins"]
        avg_parts = [int(k) * v for k, v in u["score_counts"].items() if k != "X"]
        avg = sum(avg_parts) / max(wins, 1) if wins else 99
        return (-best_count, avg)

    sorted_users = sorted(users.items(), key=sort_key)

    lines = ["🏆 **All-Time Wordle Scoreboard** 🏆\n"]
    for rank, (uid, u) in enumerate(sorted_users, 1):
        medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(rank, f"**{rank}.**")
        best_count = u.get("best_count", 0)
        wins = u["total_wins"]
        games = u["total_games"]
        avg_parts = [int(k) * v for k, v in u["score_counts"].items() if k != "X"]
        avg = sum(avg_parts) / max(wins, 1) if wins else 0
        current = u["current_streak"]
        best_streak = u["best_streak"]

        lines.append(
            f"{medal} **{u['display_name']}** — **⭐ {best_count} best**  ·  avg {avg:.1f}/6\n"
            f"　　　{wins}/{games} solved"
        )

    return "\n".join(lines)


def update_scoreboard(data: dict, results: dict[str, dict], today_str: str, wordleapp_streak: int | None) -> None:
    """Update the persistent scoreboard with today's results."""
    all_failed = all(r["score"] == "X" for r in results.values())
    no_results = len(results) == 0

    # Use WordleAPP's streak if available, otherwise calculate our own
    if wordleapp_streak is not None:
        data["streak"] = wordleapp_streak
    elif no_results or all_failed:
        data["streak"] = 0
    else:
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        if data["last_date"] == yesterday or data["last_date"] is None:
            data["streak"] += 1
        else:
            data["streak"] = 1

    data["last_date"] = today_str

    played_today = set()

    # Find today's best score (lowest number, X doesn't count)
    numeric_scores = [
        (uid, int(info["score"]))
        for uid, info in results.items()
        if info["score"] != "X"
    ]
    best_score = min((s for _, s in numeric_scores), default=None)
    todays_best_uids = {uid for uid, s in numeric_scores if s == best_score} if best_score else set()

    for uid, info in results.items():
        user = ensure_user(data, uid, info["display_name"])
        score = info["score"]

        user["total_games"] += 1
        user["score_counts"][score] = user["score_counts"].get(score, 0) + 1

        if score != "X":
            user["total_wins"] += 1
            user["current_streak"] += 1
            user["best_streak"] = max(user["best_streak"], user["current_streak"])
        else:
            user["current_streak"] = 0

        # Track best of the day
        if uid in todays_best_uids:
            user["best_count"] = user.get("best_count", 0) + 1

        played_today.add(uid)

    # Users who didn't play today — break their personal streak
    for uid in data["users"]:
        if uid not in played_today:
            data["users"][uid]["current_streak"] = 0

    # Record in history
    history_entry = {
        "date": today_str,
        "results": {uid: info["score"] for uid, info in results.items()},
    }
    data["history"].append(history_entry)


@client.event
async def on_ready():
    print(f"Logged in as {client.user}")

    try:
        guild = client.get_guild(GUILD_ID)
        if guild is None:
            guild = await client.fetch_guild(GUILD_ID)

        print(f"Scanning guild: {guild.name}")

        # Time window
        after = datetime.now(timezone.utc) - timedelta(hours=SCAN_WINDOW_HOURS)
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        # Load existing data
        data = load_data()

        # Check if we already ran today
        if data["history"] and data["history"][-1]["date"] == today_str:
            print("Already ran today — skipping.")
            await client.close()
            return

        # Fetch all guild members for name matching
        members = []
        async for member in guild.fetch_members(limit=None):
            members.append(member)
        print(f"Fetched {len(members)} guild members")

        # Find the WordleAPP message
        raw_results, wordleapp_streak, wordle_msg = await find_wordleapp_message(guild, after, members)
        print(f"Found {len(raw_results)} Wordle results from WordleAPP")

        if not raw_results:
            print("No WordleAPP message found in the scan window.")

        # Resolve user IDs to display names
        results = {}
        for uid, score in raw_results.items():
            try:
                member = guild.get_member(int(uid))
                if member is None:
                    member = await guild.fetch_member(int(uid))
                display_name = member.display_name
            except Exception:
                display_name = f"User-{uid}"
            results[uid] = {"score": score, "display_name": display_name}

        # Update scoreboard
        update_scoreboard(data, results, today_str, wordleapp_streak)
        save_data(data)

        # Post summary
        channel = guild.get_channel(SUMMARY_CHANNEL_ID)
        if channel is None:
            channel = await client.fetch_channel(SUMMARY_CHANNEL_ID)

        # If the original Wordle bot message has real Discord mentions, repost it as-is
        if wordle_msg and MENTION_RE.search(wordle_msg.content):
            summary = wordle_msg.content
            print("Reposting original WordleAPP message (has real mentions)")
        else:
            summary = build_summary(results, data)
            print("Built custom summary (plain text mentions)")
        await channel.send(summary)
        print("Posted daily summary")

        scoreboard = build_scoreboard(data)
        await channel.send(scoreboard)
        print("Posted scoreboard")

    except Exception as e:
        print(f"Error: {e}")
        raise
    finally:
        await client.close()


def main():
    client.run(DISCORD_TOKEN)


if __name__ == "__main__":
    main()
