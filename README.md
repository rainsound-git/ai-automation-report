# Notion 更新レポート → LINE 自動通知

Notion データベースの更新を毎日自動で取得し、美しいHTMLレポートを生成してLINEで通知するシステムです。

## 動作の流れ

```
毎朝 8:00 JST
    ↓
GitHub Actions 起動
    ↓
generate_report.py 実行
    ↓
Notion API → 過去24時間の更新を取得
    ↓
notion1/index.html を自動生成（GitHub Pages で公開）
    ↓
LINE Notify → スマホに通知
```

---

## セットアップ手順

### 1. Notion インテグレーションを作成

1. https://www.notion.so/my-integrations を開く
2. 「新しいインテグレーション」をクリック
3. 名前（例: `レポートBot`）を入力して作成
4. 表示された **Internal Integration Token** をコピー（`secret_xxx...`）
5. 監視したいデータベースを Notion で開き、右上「…」→「コネクト先」からインテグレーションを追加

### 2. データベース ID を確認

監視するデータベースを Notion で開き、URL を確認：

```
https://www.notion.so/xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx?v=...
                      ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
                      この32文字がデータベースID
```

### 3. LINE Notify トークンを取得

1. https://notify-bot.line.me/my/ を開く（LINEアカウントでログイン）
2. 「トークンを発行する」をクリック
3. トークン名を入力し、通知を送るトーク（自分・グループ）を選択
4. 発行されたトークンをコピー

### 4. GitHub Secrets に登録

リポジトリの **Settings → Secrets and variables → Actions** で以下を追加：

| シークレット名 | 値 |
|---|---|
| `NOTION_TOKEN` | `secret_xxx...`（手順1で取得） |
| `NOTION_DATABASE_ID` | 32文字のID（手順2で確認） |
| `LINE_NOTIFY_TOKEN` | LINE Notify トークン（手順3で取得） |
| `REPORT_URL` | `https://yourname.github.io/ai-automation-report/notion1/` |

### 5. GitHub Pages を有効化

1. リポジトリの **Settings → Pages**
2. Source: **Deploy from a branch**
3. Branch: `main` / Folder: `/ (root)`
4. 保存すると `https://yourname.github.io/ai-automation-report/` で公開される

### 6. 動作確認（手動実行）

GitHub の **Actions タブ → 「Notion 日次レポート & LINE 通知」→ 「Run workflow」** で即時実行できます。

---

## ローカルで試す

```bash
# 依存パッケージをインストール
pip install -r requirements.txt

# .env ファイルを作成
cp .env.example .env
# .env を編集して実際の値を入力

# 実行（python-dotenv を使って .env を読み込む場合）
python -c "from dotenv import load_dotenv; load_dotenv()" && python generate_report.py

# または直接環境変数を指定
$env:NOTION_TOKEN="secret_xxx"; $env:NOTION_DATABASE_ID="xxx"; $env:LINE_NOTIFY_TOKEN="xxx"; python generate_report.py
```

---

## ファイル構成

```
ai-automation-report/
├── generate_report.py          # メインスクリプト
├── requirements.txt            # Python 依存パッケージ
├── .env.example                # 環境変数テンプレート
├── .github/
│   └── workflows/
│       └── daily-report.yml   # GitHub Actions スケジューラ
└── notion1/
    └── index.html             # 自動生成されるレポート（GitHub Pages で公開）
```

---

## よくある質問

**Q: 通知時刻を変えたい**
`daily-report.yml` の `cron: "0 23 * * *"` を変更します（UTC基準）。
例: 毎朝7時JST → `"0 22 * * *"`

**Q: 複数のデータベースを監視したい**
`NOTION_DATABASE_ID` をカンマ区切りにする対応を `generate_report.py` に追加できます。

**Q: 更新がなくても通知したい**
`NOTIFY_ALWAYS=true` を環境変数に設定するか、ワークフロー手動実行時に `notify_always: true` を入力します。
