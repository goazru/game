#!/usr/bin/env python3
"""
Discord notification tool.
Usage: python3 tools/notify.py "message text"
Exits 0 on success, 1 on error.
"""
import json
import os
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).parent.parent
STATE_FILE = REPO_ROOT / ".notify_state.json"
MIN_INTERVAL_SECS = 5
MAX_LENGTH = 2000
TRUNCATE_TO = 1900


def load_state():
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            return json.load(f)
    return {}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


def send(token, channel_id, text):
    if len(text) > MAX_LENGTH:
        text = text[:TRUNCATE_TO] + "…(省略)"  # …(省略)
    url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
    headers = {
        "Authorization": f"Bot {token}",
        "Content-Type": "application/json",
    }
    resp = requests.post(url, headers=headers, json={"content": text}, timeout=30)
    return resp


def main():
    if len(sys.argv) < 2:
        print("Usage: notify.py <message>", file=sys.stderr)
        sys.exit(1)

    message = " ".join(sys.argv[1:])

    load_dotenv(REPO_ROOT / ".env")
    token = os.environ.get("DISCORD_BOT_TOKEN")
    channel_id = os.environ.get("DISCORD_CHANNEL_ID")

    if not token or not channel_id:
        print("ERROR: DISCORD_BOT_TOKEN or DISCORD_CHANNEL_ID not set", file=sys.stderr)
        sys.exit(1)

    state = load_state()
    last_sent = state.get("last_sent", 0)
    elapsed = time.time() - last_sent
    if elapsed < MIN_INTERVAL_SECS:
        time.sleep(MIN_INTERVAL_SECS - elapsed)

    resp = send(token, channel_id, message)

    state["last_sent"] = time.time()
    save_state(state)

    if not resp.ok:
        print(f"ERROR: Discord API {resp.status_code}: {resp.text[:200]}", file=sys.stderr)
        sys.exit(1)

    sys.exit(0)


if __name__ == "__main__":
    main()
