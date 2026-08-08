#!/usr/bin/env python3
"""
12星座 今日の運勢 自動生成 → Discord Webhook送信スクリプト

Gemini APIを使い、12星座それぞれの「今日の運勢（ランキング・ひとことアドバイス）」を
生成し、まとめてDiscord Webhookへ送信する。

安全設計:
  - Webhook URLはハードコードせず、環境変数 DISCORD_WEBHOOK_URL または
    --webhook-url から取得する（ソースコード・リポジトリに平文で残さない）。
  - GEMINI_API_KEY も同様に .env / 環境変数から取得する（.env.example 参照）。
  - --dry-run 指定時（または環境変数 DRY_RUN=true/1/yes）は実際の送信を行わず、
    生成済みの送信予定内容をログ出力するだけに留める（Gemini呼び出しは行う）。

使い方:
  python fortune_and_send.py --dry-run                # 生成結果の確認のみ（送信なし）
  DISCORD_WEBHOOK_URL=... python fortune_and_send.py  # 実送信
"""

from __future__ import annotations

import argparse
import datetime
import os
import sys
import textwrap
from pathlib import Path

try:
    import requests
except ImportError:
    print(
        "[ERROR] requests がインストールされていません。"
        "`pip install -r requirements.txt` を実行してください。",
        file=sys.stderr,
    )
    sys.exit(1)

try:
    from dotenv import load_dotenv
except ImportError:
    print(
        "[ERROR] python-dotenv がインストールされていません。"
        "`pip install -r requirements.txt` を実行してください。",
        file=sys.stderr,
    )
    sys.exit(1)

_BASE_DIR = Path(__file__).resolve().parent
load_dotenv(_BASE_DIR / ".env")

DISCORD_MESSAGE_LIMIT = 2000  # Discordの1メッセージあたりの文字数上限
JST = datetime.timezone(datetime.timedelta(hours=9))

ZODIAC_SIGNS = [
    "牡羊座", "牡牛座", "双子座", "蟹座", "獅子座", "乙女座",
    "天秤座", "蠍座", "射手座", "山羊座", "水瓶座", "魚座",
]

SYSTEM_PROMPT = f"""\
あなたは占いメディアのライターです。以下の12星座すべてについて、
「今日の運勢」を1位〜12位のランキング形式で生成してください。

【対象の12星座】
{"、".join(ZODIAC_SIGNS)}

【出力ルール】
- 各星座につき、順位・星座名・運勢の一言コメント（20〜40字程度）・
  ラッキーアイテム or ラッキーカラーを1つ、を1行で
- 全体を「◯位 星座名：コメント（ラッキー◯◯：〜）」のような読みやすい形式で
  1位から12位まで12行、順番に出力する
- 内容は前向きで読み手を励ますトーン（不安を煽らない）
- 12星座すべてを必ず1回ずつ含めること（重複・欠落禁止）
- 出力は本文のみ。前置き・説明・コードブロックは一切付けない
- 冒頭に「【{{DATE}}の12星座占い】」という見出し行を1行入れる
"""


def generate_fortune() -> str:
    """Gemini APIで12星座の今日の運勢を生成する。"""
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        raise RuntimeError(
            "google-genai がインストールされていません。"
            "`pip install -r requirements.txt` を実行してください。"
        )

    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY が未設定です。.env に設定してください（.env.example 参照）。"
        )
    model_name = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")

    today = datetime.datetime.now(JST).strftime("%Y年%m月%d日")
    system_prompt = SYSTEM_PROMPT.replace("{DATE}", today)
    contents = f"{today}の12星座占いランキングを生成してください。"

    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model=model_name,
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
            max_output_tokens=2048,
        ),
    )
    if not response.text:
        raise RuntimeError("Geminiからの応答が空でした。")
    return response.text.strip()


def chunk_text(text: str, limit: int = DISCORD_MESSAGE_LIMIT) -> list[str]:
    """Discordの文字数上限に収まるようテキストを分割する。"""
    chunks: list[str] = []
    for paragraph in text.split("\n\n"):
        wrapped = textwrap.wrap(
            paragraph,
            width=limit - 10,
            break_long_words=True,
            replace_whitespace=False,
        ) or [""]
        for w in wrapped:
            if chunks and len(chunks[-1]) + len(w) + 1 <= limit:
                chunks[-1] += "\n" + w
            else:
                chunks.append(w)
    return chunks


def send_to_discord(chunks: list[str], webhook_url: str, dry_run: bool) -> None:
    """Discord Webhookへチャンクごとに送信する。dry_run時は送信せずログのみ出力。"""
    total = len(chunks)
    for idx, chunk in enumerate(chunks, start=1):
        if dry_run:
            preview = chunk[:200] + ("..." if len(chunk) > 200 else "")
            print(f"[DRY-RUN] ({idx}/{total}) 送信予定メッセージ ({len(chunk)}文字):")
            print(preview)
            print("-" * 40)
            continue

        response = requests.post(webhook_url, json={"content": chunk}, timeout=15)
        if response.status_code not in (200, 204):
            raise RuntimeError(
                f"Discordへの送信に失敗しました ({idx}/{total}): "
                f"status={response.status_code} body={response.text}"
            )
        print(f"[SENT] ({idx}/{total}) {len(chunk)}文字を送信しました。")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--webhook-url",
        type=str,
        default=os.environ.get("DISCORD_WEBHOOK_URL", ""),
        help="Discord Webhook URL（未指定時は環境変数 DISCORD_WEBHOOK_URL を使用）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=os.environ.get("DRY_RUN", "").lower() in ("1", "true", "yes"),
        help="実際には送信せず、生成された運勢内容をログ出力のみ行う",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if not args.dry_run and not args.webhook_url:
        print(
            "[ERROR] Webhook URLが指定されていません。"
            "--dry-run を付けるか、環境変数 DISCORD_WEBHOOK_URL を設定してください。",
            file=sys.stderr,
        )
        return 1

    print("[INFO] Gemini APIで12星座の今日の運勢を生成中...")
    try:
        fortune_text = generate_fortune()
    except RuntimeError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        return 1
    print(f"[INFO] {len(fortune_text)}文字の運勢を生成しました。")

    chunks = chunk_text(fortune_text)
    if args.dry_run:
        print("[INFO] dry-runモードのため、実際の送信は行いません。")

    try:
        send_to_discord(chunks, args.webhook_url, args.dry_run)
    except RuntimeError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        return 1

    print("[DONE] 処理が完了しました。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
