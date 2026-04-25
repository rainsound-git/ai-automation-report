#!/usr/bin/env python3
"""
Notion 更新レポート生成 + LINE Notify 通知スクリプト

環境変数:
  NOTION_TOKEN         - Notion インテグレーショントークン
  NOTION_DATABASE_ID   - 監視対象データベースID
  LINE_NOTIFY_TOKEN    - LINE Notify トークン
  REPORT_DAYS          - 何日分の更新を取得するか（省略時: 1）
"""

import os
import sys
import html as html_escape_lib
from datetime import datetime, timedelta, timezone, date
from zoneinfo import ZoneInfo
from urllib.parse import quote as url_quote

import requests
from notion_client import Client

JST = ZoneInfo("Asia/Tokyo")

NOTION_TOKEN = os.environ.get("NOTION_TOKEN", "")
DATABASE_ID = os.environ.get("NOTION_DATABASE_ID", "")
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_TARGET_ID = os.environ.get("LINE_TARGET_ID", "")
REPORT_DAYS = int(os.environ.get("REPORT_DAYS", "1"))

OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "notion1", "index.html")


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
    edited_str = page.get("last_edited_time", "")
    if not created_str or not edited_str:
        return "edit"
    created = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
    edited = datetime.fromisoformat(edited_str.replace("Z", "+00:00"))
    if abs((edited - created).total_seconds()) < 120:
        return "add"
    return "edit"


def fetch_updates(notion: Client, days: int = 1) -> list[dict]:
    if not DATABASE_ID:
        print("ERROR: NOTION_DATABASE_ID が設定されていません", file=sys.stderr)
        sys.exit(1)

    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    results: list[dict] = []
    cursor = None

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
    edited = datetime.fromisoformat(edited_str.replace("Z", "+00:00")).astimezone(JST)
    user_id = page.get("last_edited_by", {}).get("id", "")

    return {
        "id": page["id"],
        "title": get_title(page),
        "url": page.get("url", "#"),
        "action": determine_action(page),
        "date": edited.date(),
        "time": edited.strftime("%H:%M"),
        "editor_name": get_user_name(notion, user_id) if user_id else "不明",
    }


def group_by_date(pages: list[dict]) -> dict[date, list[dict]]:
    groups: dict[date, list[dict]] = {}
    for page in pages:
        key = page["date"]
        groups.setdefault(key, []).append(page)
    return dict(sorted(groups.items(), reverse=True))


# ─── HTML 生成 ────────────────────────────────────────────────────────────────

ACTION_CFG = {
    "edit": {
        "badge": "badge-edit",
        "accent": "accent-indigo",
        "icon_wrap": "icon-wrap-indigo",
        "icon": "pencil",
        "color": "#4f46e5",
        "avatar_bg": "4f46e5",
        "label": "編集",
    },
    "add": {
        "badge": "badge-add",
        "accent": "accent-emerald",
        "icon_wrap": "icon-wrap-emerald",
        "icon": "file-plus",
        "color": "#059669",
        "avatar_bg": "059669",
        "label": "新規追加",
    },
    "delete": {
        "badge": "badge-delete",
        "accent": "accent-rose",
        "icon_wrap": "icon-wrap-rose",
        "icon": "trash-2",
        "color": "#e11d48",
        "avatar_bg": "e11d48",
        "label": "削除",
    },
}


def e(text: str) -> str:
    """HTML エスケープ"""
    return html_escape_lib.escape(str(text))


def date_label(d: date) -> str:
    today = datetime.now(JST).date()
    if d == today:
        return f"今日 — {d.year}年{d.month}月{d.day}日"
    elif d == today - timedelta(days=1):
        return f"昨日 — {d.year}年{d.month}月{d.day}日"
    else:
        return f"{d.year}年{d.month}月{d.day}日"


def render_card(page: dict, delay: int) -> str:
    cfg = ACTION_CFG.get(page["action"], ACTION_CFG["edit"])
    avatar_url = (
        f"https://ui-avatars.com/api/?name={url_quote(page['editor_name'])}"
        f"&size=36&background={cfg['avatar_bg']}&color=fff&bold=true&font-size=0.45"
    )
    delay_cls = f"delay-{min(delay, 5)}"
    is_delete = page["action"] == "delete"
    title_cls = "text-slate-400 line-through decoration-slate-300" if is_delete else "text-slate-900"
    card_opacity = ' style="opacity:0.58"' if is_delete else ""

    return f"""
      <div class="card animate-card {delay_cls}"{card_opacity}>
        <div class="card-accent {cfg['accent']}"></div>
        <div class="p-6 pl-7 flex items-start justify-between gap-5">
          <div class="flex items-start gap-4 flex-1 min-w-0">
            <div class="icon-wrap {cfg['icon_wrap']} mt-0.5">
              <i data-lucide="{cfg['icon']}" style="width:18px;height:18px;color:{cfg['color']}"></i>
            </div>
            <div class="flex-1 min-w-0">
              <div class="flex flex-wrap items-center gap-2 mb-2">
                <span class="badge {cfg['badge']}">
                  <i data-lucide="{cfg['icon']}" class="w-3 h-3"></i> {cfg['label']}
                </span>
              </div>
              <h3 class="font-bold {title_cls} text-[15px] leading-snug tracking-tight">{e(page['title'])}</h3>
            </div>
          </div>
          <div class="shrink-0 flex flex-col items-end gap-2.5">
            <span class="time-badge">{e(page['time'])}</span>
            <div class="flex items-center gap-2">
              <img src="{avatar_url}" class="w-9 h-9 rounded-full"
                style="box-shadow:0 0 0 2px #fff,0 0 0 4px rgba(99,102,241,0.3)"
                alt="{e(page['editor_name'])}" />
              <span class="text-xs text-slate-700 font-bold">{e(page['editor_name'])}</span>
            </div>
            <a href="{e(page['url'])}" target="_blank" rel="noopener" class="notion-link">
              Notionで開く <i data-lucide="arrow-up-right" class="w-3 h-3"></i>
            </a>
          </div>
        </div>
      </div>"""


def render_date_divider(d: date) -> str:
    return f"""
      <div class="flex items-center gap-2.5 pt-4 mb-2">
        <div class="date-dot"></div>
        <span class="text-[10.5px] font-bold text-slate-400 tracking-[0.18em] uppercase whitespace-nowrap">{date_label(d)}</span>
        <div class="date-line"></div>
      </div>"""


def render_html(pages: list[dict], report_date: datetime) -> str:
    grouped = group_by_date(pages)

    total = len(pages)
    count_add = sum(1 for p in pages if p["action"] == "add")
    count_edit = sum(1 for p in pages if p["action"] == "edit")
    count_delete = sum(1 for p in pages if p["action"] == "delete")

    day_label = f"{REPORT_DAYS}日" if REPORT_DAYS > 1 else "今日"
    report_date_str = f"{report_date.year}年{report_date.month}月{report_date.day}日"

    # タイムラインHTMLを構築
    timeline_html = ""
    delay = 0
    for d, day_pages in grouped.items():
        timeline_html += render_date_divider(d)
        for page in day_pages:
            delay += 1
            timeline_html += render_card(page, delay)

    if not timeline_html:
        timeline_html = """
      <div class="card p-10 text-center">
        <i data-lucide="check-circle" class="w-12 h-12 text-emerald-400 mx-auto mb-4"></i>
        <p class="text-slate-500 font-semibold">本日の更新はありませんでした</p>
      </div>"""

    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Notion 更新レポート — {report_date_str}</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <script src="https://unpkg.com/lucide@latest/dist/umd/lucide.js"></script>
  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:ital,wght@0,400;0,500;0,600;0,700;0,800;1,400&family=Noto+Sans+JP:wght@400;500;700&display=swap" rel="stylesheet" />
  <style>
    * {{ box-sizing: border-box; }}
    body {{
      font-family: 'Plus Jakarta Sans', 'Noto Sans JP', sans-serif;
      -webkit-font-smoothing: antialiased;
      background-color: #eaecf5;
      background-image:
        radial-gradient(ellipse 90% 50% at 15% 0%,   rgba(99,102,241,0.10) 0%, transparent 55%),
        radial-gradient(ellipse 60% 40% at 85% 100%, rgba(16,185,129,0.08) 0%, transparent 55%),
        radial-gradient(circle, #c4c7d8 1px, transparent 1px);
      background-size: auto, auto, 22px 22px;
      min-height: 100vh;
    }}
    .header-bg {{
      background: linear-gradient(150deg, #040919 0%, #0b1432 35%, #111e55 65%, #0e0b35 100%);
      position: relative; overflow: hidden;
    }}
    .header-bg::before {{
      content: '';
      position: absolute; inset: 0;
      background:
        radial-gradient(ellipse 55% 120% at 90% 40%,  rgba(99,102,241,0.30) 0%, transparent 60%),
        radial-gradient(ellipse 35% 70%  at  5% 90%,  rgba(16,185,129,0.18) 0%, transparent 55%),
        radial-gradient(ellipse 40% 60%  at 50% -20%, rgba(167,139,250,0.12) 0%, transparent 60%);
      pointer-events: none;
    }}
    .header-bg::after {{
      content: '';
      position: absolute; inset: 0;
      background-image:
        linear-gradient(rgba(255,255,255,0.03) 1px, transparent 1px),
        linear-gradient(90deg, rgba(255,255,255,0.03) 1px, transparent 1px);
      background-size: 40px 40px;
      pointer-events: none;
    }}
    .header-bottom-line {{
      height: 1px;
      background: linear-gradient(90deg, transparent 0%, rgba(99,102,241,0.5) 25%, rgba(167,139,250,0.6) 50%, rgba(52,211,153,0.4) 75%, transparent 100%);
    }}
    .header-content {{ position: relative; z-index: 2; }}
    .logo-ring {{
      background: linear-gradient(135deg, rgba(129,140,248,0.25) 0%, rgba(52,211,153,0.15) 100%);
      border: 1px solid rgba(255,255,255,0.14);
      box-shadow: 0 0 24px rgba(99,102,241,0.35), inset 0 1px 0 rgba(255,255,255,0.18);
    }}
    .stat-card {{
      background: rgba(255,255,255,0.05);
      border: 1px solid rgba(255,255,255,0.10);
      backdrop-filter: blur(18px);
      border-radius: 18px;
      transition: background 0.2s, transform 0.2s;
    }}
    .stat-card:hover {{ background: rgba(255,255,255,0.08); transform: translateY(-1px); }}
    .stat-card-blue  {{ box-shadow: inset 0 1px 0 rgba(129,140,248,0.25), 0 4px 20px rgba(99,102,241,0.12); }}
    .stat-card-green {{ box-shadow: inset 0 1px 0 rgba(52,211,153,0.25),  0 4px 20px rgba(16,185,129,0.10); }}
    .stat-card-rose  {{ box-shadow: inset 0 1px 0 rgba(251,113,133,0.25), 0 4px 20px rgba(244,63,94,0.10); }}
    @keyframes shimmer {{
      0%   {{ background-position: -200% center; }}
      100% {{ background-position:  200% center; }}
    }}
    .stat-num {{
      background: linear-gradient(90deg, #fff 25%, rgba(196,212,255,0.9) 50%, #fff 75%);
      background-size: 200% auto;
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
      background-clip: text;
      animation: shimmer 3.5s linear infinite;
    }}
    .card {{
      background: #ffffff;
      border-radius: 22px;
      position: relative;
      overflow: hidden;
      box-shadow:
        0 0 0 1px rgba(0,0,0,0.04),
        0 2px 4px  rgba(0,0,0,0.03),
        0 8px 24px rgba(0,0,0,0.06),
        0 20px 48px rgba(0,0,0,0.04);
      transition: transform 0.28s cubic-bezier(0.34,1.56,0.64,1), box-shadow 0.28s ease;
    }}
    .card::after {{
      content: '';
      position: absolute; top: 0; left: 0; right: 0; height: 1px;
      background: linear-gradient(90deg, transparent 0%, rgba(255,255,255,0.9) 50%, transparent 100%);
      pointer-events: none;
    }}
    .card:hover {{
      transform: translateY(-4px) scale(1.003);
      box-shadow: 0 0 0 1px rgba(0,0,0,0.05), 0 4px 8px rgba(0,0,0,0.05), 0 16px 40px rgba(0,0,0,0.10), 0 32px 64px rgba(0,0,0,0.06);
    }}
    .card-accent {{ position: absolute; top: 0; left: 0; bottom: 0; width: 5px; border-radius: 22px 0 0 22px; }}
    .accent-indigo  {{ background: linear-gradient(180deg, #6366f1 0%, #818cf8 60%, #a5b4fc 100%); }}
    .accent-emerald {{ background: linear-gradient(180deg, #059669 0%, #10b981 60%, #34d399 100%); }}
    .accent-amber   {{ background: linear-gradient(180deg, #d97706 0%, #f59e0b 60%, #fcd34d 100%); }}
    .accent-rose    {{ background: linear-gradient(180deg, #e11d48 0%, #f43f5e 60%, #fb7185 100%); }}
    .icon-wrap {{ width: 40px; height: 40px; border-radius: 14px; display: flex; align-items: center; justify-content: center; flex-shrink: 0; box-shadow: 0 2px 8px rgba(0,0,0,0.06); }}
    .icon-wrap-indigo  {{ background: linear-gradient(135deg, #eef2ff, #e0e7ff); border: 1px solid rgba(99,102,241,0.18); }}
    .icon-wrap-emerald {{ background: linear-gradient(135deg, #ecfdf5, #d1fae5); border: 1px solid rgba(16,185,129,0.18); }}
    .icon-wrap-amber   {{ background: linear-gradient(135deg, #fffbeb, #fef3c7); border: 1px solid rgba(245,158,11,0.18); }}
    .icon-wrap-rose    {{ background: linear-gradient(135deg, #fff1f2, #ffe4e6); border: 1px solid rgba(244,63,94,0.18); }}
    .badge {{ display: inline-flex; align-items: center; gap: 4px; font-size: 10.5px; font-weight: 700; padding: 3px 10px; border-radius: 999px; letter-spacing: 0.03em; }}
    .badge-edit    {{ background: linear-gradient(135deg,#eef2ff,#e0e7ff); color:#4338ca; border:1px solid rgba(99,102,241,0.22); }}
    .badge-add     {{ background: linear-gradient(135deg,#ecfdf5,#d1fae5); color:#047857; border:1px solid rgba(16,185,129,0.22); }}
    .badge-delete  {{ background: linear-gradient(135deg,#fff1f2,#ffe4e6); color:#be123c; border:1px solid rgba(244,63,94,0.22); }}
    .badge-comment {{ background: linear-gradient(135deg,#fffbeb,#fef3c7); color:#92400e; border:1px solid rgba(245,158,11,0.22); }}
    .notion-link {{
      display: inline-flex; align-items: center; gap: 5px;
      background: linear-gradient(135deg, #f5f3ff, #ede9fe);
      border: 1px solid rgba(139,92,246,0.22);
      color: #4f46e5; font-size: 11px; font-weight: 700;
      padding: 5px 13px; border-radius: 999px;
      text-decoration: none; letter-spacing: 0.02em;
      box-shadow: 0 1px 4px rgba(99,102,241,0.12);
      transition: all 0.18s ease;
    }}
    .notion-link:hover {{
      background: linear-gradient(135deg, #ede9fe, #ddd6fe); color: #3730a3;
      box-shadow: 0 3px 12px rgba(99,102,241,0.28); transform: translateY(-1px);
    }}
    .time-badge {{
      background: linear-gradient(135deg, #f8fafc, #f1f5f9);
      border: 1px solid rgba(226,232,240,0.9); color: #94a3b8;
      font-size: 11px; font-weight: 700; padding: 4px 11px; border-radius: 999px; letter-spacing: 0.04em;
    }}
    .date-dot {{ width: 7px; height: 7px; border-radius: 50%; background: linear-gradient(135deg, #a5b4fc, #818cf8); box-shadow: 0 0 6px rgba(99,102,241,0.4); flex-shrink: 0; }}
    .date-line {{ flex: 1; height: 1px; background: linear-gradient(90deg, rgba(148,163,184,0.35) 0%, rgba(148,163,184,0.15) 100%); }}
    @keyframes fadeSlideUp {{ from {{ opacity: 0; transform: translateY(16px); }} to {{ opacity: 1; transform: translateY(0); }} }}
    .animate-card {{ animation: fadeSlideUp 0.45s cubic-bezier(0.22,1,0.36,1) both; }}
    .delay-1 {{ animation-delay: 0.05s; }}
    .delay-2 {{ animation-delay: 0.12s; }}
    .delay-3 {{ animation-delay: 0.19s; }}
    .delay-4 {{ animation-delay: 0.26s; }}
    .delay-5 {{ animation-delay: 0.33s; }}
    @keyframes pulse-dot {{ 0%, 100% {{ opacity: 1; box-shadow: 0 0 0 0 rgba(52,211,153,0.5); }} 50% {{ opacity: 0.75; box-shadow: 0 0 0 4px rgba(52,211,153,0); }} }}
    .live-dot {{ animation: pulse-dot 2s ease-in-out infinite; }}
  </style>
</head>
<body>

  <header class="header-bg sticky top-0 z-20">
    <div class="header-content max-w-3xl mx-auto px-6">
      <div class="flex items-center justify-between pt-7 pb-5">
        <div class="flex items-center gap-4">
          <div class="logo-ring w-11 h-11 rounded-2xl flex items-center justify-center">
            <i data-lucide="layout-dashboard" class="w-5 h-5 text-indigo-200"></i>
          </div>
          <div>
            <p class="text-indigo-300/60 text-[10px] font-bold tracking-[0.22em] uppercase mb-0.5">Student Council</p>
            <h1 class="text-white text-xl font-extrabold leading-tight tracking-tight">Notion 更新レポート</h1>
          </div>
        </div>
        <div class="flex items-center gap-3">
          <div class="flex items-center gap-1.5 text-white/45 text-xs font-medium">
            <i data-lucide="calendar" class="w-3.5 h-3.5"></i>
            <span>{report_date_str}</span>
          </div>
          <div class="w-px h-4 bg-white/12 rounded"></div>
          <div class="flex items-center gap-2 bg-emerald-500/12 border border-emerald-400/22 text-emerald-300 text-xs font-bold px-3.5 py-2 rounded-full">
            <span class="live-dot w-2 h-2 bg-emerald-400 rounded-full block"></span>
            自動更新中
          </div>
        </div>
      </div>

      <div class="grid grid-cols-3 gap-3 pb-7 border-t border-white/[0.07] pt-5">
        <div class="stat-card stat-card-blue px-4 py-4">
          <div class="flex items-center gap-2 mb-2.5">
            <div class="w-6 h-6 rounded-lg bg-indigo-500/20 flex items-center justify-center">
              <i data-lucide="file-text" class="w-3.5 h-3.5 text-indigo-300"></i>
            </div>
            <span class="text-white/40 text-[11px] font-semibold tracking-wide">更新ページ</span>
          </div>
          <div class="flex items-baseline gap-1.5">
            <span class="stat-num text-3xl font-extrabold leading-none tabular-nums">{total}</span>
            <span class="text-white/25 text-xs font-medium">件 / {day_label}</span>
          </div>
        </div>
        <div class="stat-card stat-card-green px-4 py-4">
          <div class="flex items-center gap-2 mb-2.5">
            <div class="w-6 h-6 rounded-lg bg-emerald-500/20 flex items-center justify-center">
              <i data-lucide="plus-circle" class="w-3.5 h-3.5 text-emerald-300"></i>
            </div>
            <span class="text-white/40 text-[11px] font-semibold tracking-wide">新規追加</span>
          </div>
          <div class="flex items-baseline gap-1.5">
            <span class="stat-num text-3xl font-extrabold leading-none tabular-nums">{count_add}</span>
            <span class="text-white/25 text-xs font-medium">件 / {day_label}</span>
          </div>
        </div>
        <div class="stat-card stat-card-rose px-4 py-4">
          <div class="flex items-center gap-2 mb-2.5">
            <div class="w-6 h-6 rounded-lg bg-rose-500/20 flex items-center justify-center">
              <i data-lucide="pencil" class="w-3.5 h-3.5 text-rose-300"></i>
            </div>
            <span class="text-white/40 text-[11px] font-semibold tracking-wide">編集</span>
          </div>
          <div class="flex items-baseline gap-1.5">
            <span class="stat-num text-3xl font-extrabold leading-none tabular-nums">{count_edit}</span>
            <span class="text-white/25 text-xs font-medium">件 / {day_label}</span>
          </div>
        </div>
      </div>
    </div>
    <div class="header-bottom-line"></div>
  </header>

  <main class="max-w-3xl mx-auto px-6 py-9">
    <div class="space-y-3">
      {timeline_html}
    </div>

    <footer class="mt-14 pt-5 flex items-center justify-between">
      <div class="h-px flex-1 bg-gradient-to-r from-slate-200/80 to-transparent mr-6 rounded"></div>
      <div class="flex items-center gap-5">
        <div class="flex items-center gap-2 text-slate-400 text-xs font-semibold">
          <div class="w-5 h-5 rounded-md bg-indigo-50 border border-indigo-100 flex items-center justify-center">
            <i data-lucide="zap" class="w-3 h-3 text-indigo-400"></i>
          </div>
          <span>Notion API で自動生成 — {report_date_str}</span>
        </div>
      </div>
    </footer>
  </main>

  <script>lucide.createIcons();</script>
</body>
</html>"""


# ─── LINE Messaging API 送信 ──────────────────────────────────────────────────

def build_line_message(pages: list[dict], report_date: datetime) -> str:
    WEEKDAY = ["月", "火", "水", "木", "金", "土", "日"]
    wd = WEEKDAY[report_date.weekday()]
    date_str = f"{report_date.year}年{report_date.month}月{report_date.day}日（{wd}）"

    count_add    = sum(1 for p in pages if p["action"] == "add")
    count_edit   = sum(1 for p in pages if p["action"] == "edit")
    count_delete = sum(1 for p in pages if p["action"] == "delete")
    total        = len(pages)

    # ── ヘッダー ──────────────────────────────
    msg = f"╭{'─'*28}╮\n"
    msg += f"│  🗒  Notion 更新レポート\n"
    msg += f"│  {date_str}\n"
    msg += f"╰{'─'*28}╯\n"

    # ── サマリー ──────────────────────────────
    msg += "\n"
    msg += f"┌─ 本日のまとめ {'─'*14}┐\n"
    msg += f"│  📄 新規追加   {count_add:>3} 件\n"
    msg += f"│  ✏️   編集      {count_edit:>3} 件\n"
    msg += f"│  🗑️   削除      {count_delete:>3} 件\n"
    msg += f"│  {'─'*22}\n"
    msg += f"│  📊 合計       {total:>3} 件\n"
    msg += f"└{'─'*28}┘\n"

    # ── 更新一覧 ──────────────────────────────
    if pages:
        msg += "\n▼ 更新一覧\n"
        ACTION_ICON = {"add": "📄", "edit": "✏️", "delete": "🗑️"}
        display = pages[:10]
        for i, p in enumerate(display):
            icon  = ACTION_ICON.get(p["action"], "📝")
            title = p["title"][:22] + "…" if len(p["title"]) > 22 else p["title"]
            connector = "┗" if i == len(display) - 1 and len(pages) <= 10 else "┣"
            msg += f" {connector} {icon} {title}\n"
        if len(pages) > 10:
            msg += f" ┗ … 他 {len(pages) - 10} 件\n"

    # ── フッター ──────────────────────────────
    report_url = os.environ.get("REPORT_URL", "")
    if report_url:
        msg += f"\n👉 詳細レポート\n{report_url}"

    return msg


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
            "Content-Type": "application/json",
        },
        json={
            "to": LINE_TARGET_ID,
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
    print(f"🚀 レポート生成開始: {now.strftime('%Y-%m-%d %H:%M')} JST")

    notion = get_notion_client()

    print(f"📡 Notion データベースから過去 {REPORT_DAYS} 日分の更新を取得中...")
    raw_pages = fetch_updates(notion, days=REPORT_DAYS)
    print(f"   {len(raw_pages)} 件取得")

    pages = [format_page(notion, p) for p in raw_pages]

    html = render_html(pages, now)
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"✅ HTML 生成完了: {OUTPUT_PATH}")

    if pages or os.environ.get("NOTIFY_ALWAYS", "").lower() == "true":
        msg = build_line_message(pages, now)
        send_line_message(msg)
    else:
        print("更新なし — LINE 通知をスキップ（NOTIFY_ALWAYS=true で常に送信可）")

    print("🎉 完了")


if __name__ == "__main__":
    main()
