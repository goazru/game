#!/usr/bin/env python3
"""
NovelAI image generation tool.
Usage: python3 tools/nai_gen.py --prompt "..." [--size 832x1216|1216x832|1024x1024] [--out assets/generated/]
"""
import argparse
import fcntl
import io
import json
import os
import sys
import time
import zipfile
from datetime import date, datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).parent.parent
STATE_FILE = REPO_ROOT / ".nai_state.json"
LOCK_FILE = REPO_ROOT / ".nai_lock"
LOG_FILE = REPO_ROOT / "assets" / "generated" / "log.jsonl"
OUT_DIR = REPO_ROOT / "assets" / "generated"
API_URL = "https://image.novelai.net/ai/generate-image"

ALLOWED_SIZES = {
    "832x1216": (832, 1216),
    "1216x832": (1216, 832),
    "1024x1024": (1024, 1024),
}
MIN_INTERVAL_SECS = 15
DAILY_LIMIT = 100


def load_state():
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            return json.load(f)
    return {}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


def log_result(prompt, size, output_path, success, error=None):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "time": datetime.utcnow().isoformat() + "Z",
        "prompt": prompt,
        "size": size,
        "output": str(output_path) if output_path else None,
        "success": success,
        "error": error,
    }
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def generate(token, prompt, width, height):
    import random
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    payload = {
        "input": prompt,
        "model": "nai-diffusion-3",
        "action": "generate",
        "parameters": {
            "width": width,
            "height": height,
            "scale": 7,
            "sampler": "k_euler_ancestral",
            "steps": 28,
            "seed": random.randint(0, 2**32 - 1),
            "n_samples": 1,
            "ucPreset": 0,
            "qualityToggle": True,
            "sm": False,
            "sm_dyn": False,
            "negative_prompt": (
                "lowres, bad anatomy, bad hands, text, error, missing fingers, "
                "extra digit, fewer digits, cropped, worst quality, low quality, "
                "normal quality, jpeg artifacts, signature, watermark, username, blurry"
            ),
        },
    }
    resp = requests.post(API_URL, headers=headers, json=payload, timeout=120)
    return resp


def extract_image(resp_bytes):
    with zipfile.ZipFile(io.BytesIO(resp_bytes)) as z:
        names = z.namelist()
        return z.read(names[0])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--size", default="832x1216", choices=list(ALLOWED_SIZES))
    parser.add_argument("--out", default=str(OUT_DIR))
    args = parser.parse_args()

    load_dotenv(REPO_ROOT / ".env")
    token = os.environ.get("NAI_TOKEN")
    if not token:
        print("ERROR: NAI_TOKEN not set in .env", file=sys.stderr)
        sys.exit(1)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    # flock: single process
    lock_fd = open(LOCK_FILE, "w")
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except IOError:
        print("ERROR: Another nai_gen.py is already running", file=sys.stderr)
        sys.exit(1)

    try:
        state = load_state()
        today = str(date.today())

        # Daily limit check
        daily_count = state.get("daily", {}).get(today, 0)
        if daily_count >= DAILY_LIMIT:
            print(f"ERROR: Daily limit {DAILY_LIMIT} reached for {today}", file=sys.stderr)
            log_result(args.prompt, args.size, None, False, "daily_limit")
            sys.exit(1)

        # Rate limit: min 15s between runs
        last_run = state.get("last_run", 0)
        elapsed = time.time() - last_run
        if elapsed < MIN_INTERVAL_SECS:
            wait = MIN_INTERVAL_SECS - elapsed
            print(f"Rate limit: waiting {wait:.1f}s...", file=sys.stderr)
            time.sleep(wait)

        width, height = ALLOWED_SIZES[args.size]

        def try_generate():
            resp = generate(token, args.prompt, width, height)
            if resp.status_code == 200:
                return resp
            if resp.status_code in (429, 500, 502, 503, 504):
                print(f"HTTP {resp.status_code}, retrying in 60s...", file=sys.stderr)
                time.sleep(60)
                resp2 = generate(token, args.prompt, width, height)
                if resp2.status_code == 200:
                    return resp2
                print(f"ERROR: HTTP {resp2.status_code} on retry", file=sys.stderr)
                return None
            print(f"ERROR: HTTP {resp.status_code}: {resp.text[:200]}", file=sys.stderr)
            return None

        resp = try_generate()

        state["last_run"] = time.time()
        daily = state.get("daily", {})
        daily[today] = daily.get(today, 0) + 1
        state["daily"] = daily
        save_state(state)

        if resp is None:
            log_result(args.prompt, args.size, None, False, "http_error")
            sys.exit(1)

        img_data = extract_image(resp.content)
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        out_path = out_dir / f"{ts}_{args.size}.png"
        out_path.write_bytes(img_data)

        log_result(args.prompt, args.size, out_path, True)
        print(str(out_path))

    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        lock_fd.close()
        try:
            LOCK_FILE.unlink()
        except FileNotFoundError:
            pass


if __name__ == "__main__":
    main()
