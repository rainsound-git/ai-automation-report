#!/usr/bin/env python3
"""
Notion 更新レポート → LINE 通知スクリプト

環境変数:
  NOTION_TOKEN              - Notion インテグレーショントークン
  NOTION_DATABASE_ID        - 監視対象データベースID
  LINE_CHANNEL_ACCESS_TOKEN - LINE Messaging API チャネルアクセストークン
  LINE_TARGET_ID            - 送信先ユーザーID（Uxxxxxxxx...）
  REPORT_DAYS               - 何日分の更新を取得するか（省略時: 1）
  NOTIFY_ALWAYS             - 更新がなくても通知する場合は true
"""

import os
import sys
from datetime import datetime, timedelta, timezone, date
from zoneinfo import ZoneInfo

import requests
from notion_client import Client

JST = ZoneInfo("Asia/Tokyo")

NOTION_TOKEN              = os.environ.get("NOTION_TOKEN", "")
DATABASE_ID               = os.environ.get("NOTION_DATABASE_ID", "")
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_TARGET_ID            = os.environ.get("LINE_TARGET_ID", "")
REPORT_DAYS               = int(os.environ.get("REPORT_DAYS", "1"))


# ─── Notion データ取得 ────────────────────────────────────────────────────────

def get_notion_client() -> Client:
    if not NOTION_TOKEN:
        print("ERROR: NOTION_TOKEN が設定されていません", file=sys.stderr)
        sys.exit(1)
    return Client(auth=NOTION_TOKEN)


def get_title(page: dict) -> str:
    for prop in page.get("properties", {}).values():
        if prop["type"] == "title":
            texts = prop.get("title", [])
            if texts:
                return "".join(t.get("plain_text", "") for t in texts)
    return "（無題）"


_user_cache: dict[str, str] = {}

def get_user_name(notion: Client, user_id: str) -> str:
    if user_id in _user_cache:
        return _user_cache[user_id]
    try:
        user = notion.users.retrieve(user_id)
        name = user.get("name") or "不明"
    except Exception:
        name = "不明"
    _user_cache[user_id] = name
    return name


def determine_action(page: dict) -> str:
    created_str = page.get("created_time", "")
    edited_str  = page.get("last_edited_time", "")
    if not created_str or not edited_str:
        return "edit"
    created = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
    edited  = datetime.fromisoformat(edited_str.replace("Z", "+00:00"))
    if abs((edited - created).total_seconds()) < 120:
        return "add"
    return "edit"


def fetch_updates(notion: Client, days: int = 1) -> list[dict]:
    if not DATABASE_ID:
        print("ERROR: NOTION_DATABASE_ID が設定されていません", file=sys.stderr)
        sys.exit(1)

    since   = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    results = []
    cursor  = None

    while True:
        kwargs: dict = {
            "database_id": DATABASE_ID,
            "filter": {
                "timestamp": "last_edited_time",
                "last_edited_time": {"after": since},
            },
            "sorts": [{"timestamp": "last_edited_time", "direction": "descending"}],
            "page_size": 100,
        }
        if cursor:
            kwargs["start_cursor"] = cursor

        response = notion.databases.query(**kwargs)
        results.extend(response.get("results", []))

        if not response.get("has_more"):
            break
        cursor = response.get("next_cursor")

    return results


def format_page(notion: Client, page: dict) -> dict:
    edited_str = page.get("last_edited_time", "")
    edited     = datetime.fromisoformat(edited_str.replace("Z", "+00:00")).astimezone(JST)
    user_id    = page.get("last_edited_by", {}).get("id", "")

    return {
        "id":          page["id"],
        "title":       get_title(page),
        "url":         page.get("url", "#"),
        "action":      determine_action(page),
        "date":        edited.date(),
        "time":        edited.strftime("%H:%M"),
        "editor_name": get_user_name(notion, user_id) if user_id else "不明",
    }


# ─── LINE メッセージ構築 ──────────────────────────────────────────────────────

def build_line_message(pages: list[dict], report_date: datetime) -> str:
    WEEKDAY  = ["月", "火", "水", "木", "金", "土", "日"]
    wd       = WEEKDAY[report_date.weekday()]
    date_str = f"{report_date.year}年{report_date.month}月{report_date.day}日（{wd}）"

    count_add    = sum(1 for p in pages if p["action"] == "add")
    count_edit   = sum(1 for p in pages if p["action"] == "edit")
    count_delete = sum(1 for p in pages if p["action"] == "delete")
    total        = len(pages)

    msg  = f"╭{'─'*28}╮\n"
    msg += f"│  🗒  Notion 更新レポート\n"
    msg += f"│  {date_str}\n"
    msg += f"╰{'─'*28}╯\n"

    msg += "\n"
    msg += f"┌─ 本日のまとめ {'─'*14}┐\n"
    msg += f"│  📄 新規追加   {count_add:>3} 件\n"
    msg += f"│  ✏️   編集      {count_edit:>3} 件\n"
    msg += f"│  🗑️   削除      {count_delete:>3} 件\n"
    msg += f"│  {'─'*22}\n"
    msg += f"│  📊 合計       {total:>3} 件\n"
    msg += f"└{'─'*28}┘\n"

    if pages:
        msg += "\n▼ 更新一覧\n"
        ACTION_ICON = {"add": "📄", "edit": "✏️", "delete": "🗑️"}
        display = pages[:10]
        for i, p in enumerate(display):
            icon      = ACTION_ICON.get(p["action"], "📝")
            title     = p["title"][:22] + "…" if len(p["title"]) > 22 else p["title"]
            connector = "┗" if i == len(display) - 1 and len(pages) <= 10 else "┣"
            msg += f" {connector} {icon} {title}\n"
        if len(pages) > 10:
            msg += f" ┗ … 他 {len(pages) - 10} 件\n"

    return msg


# ─── LINE Messaging API 送信 ──────────────────────────────────────────────────

def send_line_message(message: str) -> None:
    if not LINE_CHANNEL_ACCESS_TOKEN:
        print("LINE_CHANNEL_ACCESS_TOKEN が未設定のため、LINE通知をスキップします")
        return
    if not LINE_TARGET_ID:
        print("LINE_TARGET_ID が未設定のため、LINE通知をスキップします")
        return

    resp = requests.post(
        "https://api.line.me/v2/bot/message/push",
        headers={
            "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
            "Content-Type":  "application/json",
        },
        json={
            "to":       LINE_TARGET_ID,
            "messages": [{"type": "text", "text": message}],
        },
        timeout=10,
    )
    if resp.status_code == 200:
        print("✅ LINE 通知を送信しました")
    else:
        print(f"⚠️  LINE 通知に失敗しました: {resp.status_code} {resp.text}", file=sys.stderr)


# ─── メイン ───────────────────────────────────────────────────────────────────

def main() -> None:
    now = datetime.now(JST)
    print(f"🚀 開始: {now.strftime('%Y-%m-%d %H:%M')} JST")

    notion = get_notion_client()

    print(f"📡 Notion から過去 {REPORT_DAYS} 日分の更新を取得中...")
    raw_pages = fetch_updates(notion, days=REPORT_DAYS)
    print(f"   {len(raw_pages)} 件取得")

    pages = [format_page(notion, p) for p in raw_pages]

    if pages or os.environ.get("NOTIFY_ALWAYS", "").lower() == "true":
        msg = build_line_message(pages, now)
        print("─── LINE メッセージプレビュー ───")
        print(msg)
        print("────────────────────────────────")
        send_line_message(msg)
    else:
        print("更新なし — LINE 通知をスキップ（NOTIFY_ALWAYS=true で常に送信可）")

    print("🎉 完了")


if __name__ == "__main__":
    main()
