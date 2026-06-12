#!/usr/bin/env python3
"""
Discord bot for game development task queue.
Slash commands:
  /task <content> - Queue a task for claude to execute
  /status         - Show current task and queue length
"""
import asyncio
import os
import re
import subprocess
import sys
from collections import deque
from pathlib import Path

import discord
from discord import app_commands
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).parent.parent
NOTIFY = str(REPO_ROOT / "tools" / "notify.py")
load_dotenv(REPO_ROOT / ".env")

TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")
CHANNEL_ID = int(os.environ.get("DISCORD_CHANNEL_ID", "0"))

intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

task_queue: deque[tuple[str, discord.Interaction]] = deque()
current_task: str | None = None
worker_running = False


def _notify(msg: str):
    subprocess.run(
        [sys.executable, NOTIFY, msg],
        cwd=REPO_ROOT,
        timeout=30,
    )


async def _deploy_via_script() -> str | None:
    deploy_script = REPO_ROOT / "tools" / "deploy.sh"
    print(f"[deploy] start: {deploy_script}", flush=True)
    proc = await asyncio.create_subprocess_exec(
        "bash", str(deploy_script),
        cwd=REPO_ROOT,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=360)
    except asyncio.TimeoutError:
        proc.kill()
        print(f"[deploy] timeout: {deploy_script}", flush=True)
        return "[デプロイタイムアウト]"
    output = stdout.decode() + stderr.decode()
    print(f"[deploy] exit={proc.returncode} script={deploy_script}\n{output[:500]}", flush=True)
    if proc.returncode != 0:
        return f"[デプロイ失敗] {output[:300]}"
    match = re.search(r"URL:\s*(https://\S+)", output)
    if match:
        return match.group(1)
    return "[デプロイ完了: URL取得失敗]"


async def run_task(content: str, channel: discord.TextChannel):
    global current_task
    current_task = content
    try:
        await channel.send(f"⚙️ タスク実行開始: {content[:100]}")
        proc = await asyncio.create_subprocess_exec(
            "claude", "--dangerously-skip-permissions", "-p", content,
            cwd=REPO_ROOT,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=7200)
        except asyncio.TimeoutError:
            proc.kill()
            await channel.send("⏰ タイムアウト(2時間)でタスクを中断しました")
            _notify(f"タスクタイムアウト: {content[:80]}")
            return

        result_text = stdout.decode(errors="replace")
        err_text = stderr.decode(errors="replace")

        if proc.returncode != 0:
            summary = f"❌ 失敗 (code {proc.returncode})\n{err_text[:500]}"
            await channel.send(summary[:2000])
            _notify(f"タスク失敗: {content[:60]}")
            return

        # Attempt GitHub Pages deploy
        deploy_result = await _deploy_via_script()
        if deploy_result:
            await channel.send(f"✅ 完了\n🌐 {deploy_result}")
        else:
            short = result_text[-800:].strip() if result_text else "(出力なし)"
            await channel.send(f"✅ 完了\n{short}"[:2000])

        _notify(f"タスク完了: {content[:60]}")

    except Exception as e:
        msg = f"⚠️ 例外: {e}"
        try:
            await channel.send(msg[:2000])
        except Exception:
            pass
        _notify(f"Botエラー: {e}")
    finally:
        current_task = None


async def worker_loop():
    global worker_running
    worker_running = True
    try:
        while True:
            if task_queue:
                content, interaction = task_queue.popleft()
                channel = client.get_channel(CHANNEL_ID)
                if channel:
                    await run_task(content, channel)
            await asyncio.sleep(1)
    except Exception as e:
        _notify(f"ワーカー例外: {e}")
        worker_running = False


@tree.command(name="task", description="Claudeにタスクを依頼")
@app_commands.describe(content="タスクの内容")
async def task_cmd(interaction: discord.Interaction, content: str):
    if current_task is not None:
        pos = len(task_queue) + 1
        task_queue.append((content, interaction))
        await interaction.response.send_message(
            f"📋 受け付けました（待ち {pos} 番目）: {content[:80]}"
        )
    else:
        task_queue.append((content, interaction))
        await interaction.response.send_message(f"📋 タスクを受け付けました: {content[:80]}")


@tree.command(name="status", description="実行状況とキュー件数を返す")
async def status_cmd(interaction: discord.Interaction):
    if current_task:
        msg = f"⚙️ 実行中: {current_task[:100]}\n📋 キュー: {len(task_queue)} 件"
    else:
        msg = f"💤 待機中\n📋 キュー: {len(task_queue)} 件"
    await interaction.response.send_message(msg)


@client.event
async def on_ready():
    await tree.sync()
    print(f"Bot ready: {client.user}")
    channel = client.get_channel(CHANNEL_ID)
    if channel:
        await channel.send("🤖 Botが起動しました。`/task <内容>` でタスクを依頼できます。")
    asyncio.ensure_future(worker_loop())


def main():
    if not TOKEN:
        print("ERROR: DISCORD_BOT_TOKEN not set", file=sys.stderr)
        sys.exit(1)
    client.run(TOKEN)


if __name__ == "__main__":
    main()
