"""
Discord Wordle Tracker Bot

Scans all text channels for Wordle results posted in the last 24 hours,
updates a persistent scoreboard, and posts a daily summary.
"""

import os
import json
import re
import asyncio
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

# ── Wordle message parser ───────────────────────────────────────────────────

# Matches lines like "Wordle 1,234 3/6" or "Wordle 1234 X/6"
WORDLE_RE = re.compile(
    r"Wordle\s+[\d,]+\s+([1-6X])/6",
    re.IGNORECASE,
)


def parse_wordle_score(content: str) -> str | None:
    """Extract the score (e.g. '3' or 'X') from a Wordle share message."""
    m = WORDLE_RE.search(content)
    if not m:
        return None
    return m.group(1)


def extract_wordle_number(content: str) -> int | None:
    """Extract the puzzle number from a Wordle share message."""
    m = re.search(r"Wordle\s+([\d,]+)\s+[1-6X]/6", content, re.IGNORECASE)
    if not m:
        return None
    return int(m.group(1).replace(",", ""))


# ── Persistent data ────────────────────────────────────────────────────────

def load_data() -> dict:
    """Load the scoreboard JSON. Structure:
    {
        "streak": 0,
        "last_date": null,
        "users": {
            "<user_id>": {
                "display_name": "Nickname",
                "total_games": 10,
                "total_wins": 8,
                "score_counts": {"1": 0, "2": 1, "3": 3, ...},
                "current_streak": 5,
                "best_streak": 7
            }
        },
        "history": [
            {"date": "2025-01-15", "puzzle": 1234, "results": {"user_id": "3"}}
        ]
    }
    """
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
            "score_counts": {"1": 0, "2": 0, "3": 0, "4": 0, "5": 0, "6": 0, "X": 0},
            "current_streak": 0,
            "best_streak": 0,
        }
    # Always update display name to latest
    data["users"][user_id]["display_name"] = display_name
    return data["users"][user_id]


# ── Discord client ──────────────────────────────────────────────────────────

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

client = discord.Client(intents=intents)


async def scan_channels(guild: discord.Guild, after: datetime) -> dict[str, dict]:
    """
    Scan all readable text channels for Wordle results posted after `after`.
    Returns {user_id_str: {"score": "3", "display_name": "Nick", "puzzle": 1234}}
    Only keeps the first result per user (in case someone posted twice).
    """
    results: dict[str, dict] = {}

    for channel in guild.text_channels:
        perms = channel.permissions_for(guild.me)
        if not perms.read_messages or not perms.read_message_history:
            continue

        try:
            async for message in channel.history(after=after, limit=500):
                if message.author.bot:
                    continue

                score = parse_wordle_score(message.content)
                if score is None:
                    continue

                uid = str(message.author.id)
                if uid in results:
                    continue  # first result wins

                puzzle_num = extract_wordle_number(message.content)
                display = message.author.display_name

                results[uid] = {
                    "score": score,
                    "display_name": display,
                    "puzzle": puzzle_num,
                }
        except discord.Forbidden:
            continue
        except discord.HTTPException as e:
            print(f"  ⚠ Error reading #{channel.name}: {e}")

    return results


def build_summary(results: dict[str, dict], data: dict) -> str:
    """Build the daily summary message."""
    if not results:
        return "No Wordle results found today. The streak is broken! 😢"

    # Group by score
    by_score: dict[str, list[str]] = defaultdict(list)
    for uid, info in results.items():
        by_score[info["score"]].append(info["display_name"])

    # Sort: 1/6 first … 6/6 … X/6 last
    score_order = ["1", "2", "3", "4", "5", "6", "X"]
    lines = []
    for s in score_order:
        if s not in by_score:
            continue
        names = " ".join(f"@{n}" for n in sorted(by_score[s]))
        label = f"{s}/6"
        lines.append(f"**{label}:** {names}")

    streak = data["streak"]
    header = f"Your group is on a **{streak} day streak!** Here are yesterday's results:\n"
    if streak == 0:
        header = "The streak was broken! Here are yesterday's results:\n"

    return header + "\n".join(lines)


def build_scoreboard(data: dict) -> str:
    """Build an all-time scoreboard embed-friendly string."""
    users = data["users"]
    if not users:
        return "No scores recorded yet."

    # Sort by total wins desc, then by average score asc
    def sort_key(item):
        u = item[1]
        total = u["total_games"]
        if total == 0:
            return (0, 99)
        avg = sum(
            int(k) * v for k, v in u["score_counts"].items() if k != "X"
        ) / max(total, 1)
        return (-u["total_wins"], avg)

    sorted_users = sorted(users.items(), key=sort_key)

    lines = ["🏆 **All-Time Wordle Scoreboard** 🏆\n"]
    for rank, (uid, u) in enumerate(sorted_users, 1):
        medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(rank, f"**{rank}.**")
        wins = u["total_wins"]
        games = u["total_games"]
        best = u["best_streak"]
        current = u["current_streak"]
        avg_parts = [int(k) * v for k, v in u["score_counts"].items() if k != "X"]
        avg = sum(avg_parts) / max(wins, 1) if wins else 0
        lines.append(
            f"{medal} **{u['display_name']}** — "
            f"{wins}/{games} wins, "
            f"avg {avg:.1f}/6, "
            f"streak {current} (best {best})"
        )

    return "\n".join(lines)


def update_scoreboard(data: dict, results: dict[str, dict], today_str: str) -> None:
    """Update the persistent scoreboard with today's results."""
    # Check if everyone failed (X) — breaks the group streak
    all_failed = all(r["score"] == "X" for r in results.values())
    no_results = len(results) == 0

    # Update group streak
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    if no_results or all_failed:
        data["streak"] = 0
    elif data["last_date"] == yesterday or data["last_date"] is None:
        data["streak"] += 1
    else:
        # Gap in days — streak broken
        data["streak"] = 1

    data["last_date"] = today_str

    # Puzzle number (pick the first one found)
    puzzle = None
    for r in results.values():
        if r.get("puzzle"):
            puzzle = r["puzzle"]
            break

    # Track which users played today
    played_today = set()

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

        played_today.add(uid)

    # Users who didn't play today — break their personal streak
    for uid in data["users"]:
        if uid not in played_today:
            data["users"][uid]["current_streak"] = 0

    # Record in history
    history_entry = {
        "date": today_str,
        "puzzle": puzzle,
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

        # Time window — look at last SCAN_WINDOW_HOURS
        after = datetime.now(timezone.utc) - timedelta(hours=SCAN_WINDOW_HOURS)
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        # Load existing data
        data = load_data()

        # Check if we already ran today
        if data["history"] and data["history"][-1]["date"] == today_str:
            print("Already ran today — skipping.")
            await client.close()
            return

        # Scan all channels
        results = await scan_channels(guild, after)
        print(f"Found {len(results)} Wordle results")

        # Update scoreboard
        update_scoreboard(data, results, today_str)
        save_data(data)

        # Post summary to the designated channel
        channel = guild.get_channel(SUMMARY_CHANNEL_ID)
        if channel is None:
            channel = await client.fetch_channel(SUMMARY_CHANNEL_ID)

        summary = build_summary(results, data)
        await channel.send(summary)
        print("Posted daily summary")

        # Post scoreboard
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
