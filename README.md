# 公開API ローカル名鑑 (`papis`)

[public-apis/public-apis](https://github.com/public-apis/public-apis) の 1,456 件
を SQLite + JSON にミラーし、ターミナルから検索・呼び出しできる単機能ツール。

- **依存ゼロ**: Python 3.9+ 標準ライブラリのみ。`pip install` 不要。
- **オフライン検索**: 一度 `build` すればネット不要で検索可能。
- **鍵保管庫**: API キーは macOS Keychain 内（プロジェクト外）。リポジトリには鍵を一切置かない。
- **自動更新**: GitHub Actions で毎週月曜にデータ再生成。

## ファイル

```
公開API/
├── papis.py                      # 単一ファイル CLI
├── data/
│   ├── apis.db                   # SQLite (apis + meta テーブル)
│   └── apis.json                 # 同内容の JSON ダンプ
├── .github/workflows/update.yml  # 週次自動更新ワークフロー
└── ~/.papis/index.json           # vault メタ (鍵本体は Keychain)
```

## クイックスタート

```bash
# 初回データ取得（または再生成）
python3 papis.py build

# カテゴリ別件数
python3 papis.py cats

# キーワード検索 (name / description / category 横断)
python3 papis.py search weather

# フィルタ: 認証不要 + HTTPS + CORS
python3 papis.py search --no-auth --https --cors

# カテゴリ完全一致
python3 papis.py search --category Cryptocurrency --limit 10

# 単一API詳細
python3 papis.py show "Cat Facts"
```

## API 呼び出し

### 認証不要 API
```bash
python3 papis.py call "https://catfact.ninja/fact"
```

### 認証必要 API（鍵を Keychain に保管 → 自動付与）

```bash
# 鍵を保管 (キーは対話入力 or --key)
python3 papis.py vault set openweather \
  --auth-style query --auth-param appid

# 呼び出し時に --name で vault エントリを指定
python3 papis.py call \
  "https://api.openweathermap.org/data/2.5/weather?q=Tokyo" \
  --name openweather
```

`--auth-style` の値:

| style    | 付与方法                               | 用途例                   |
|----------|----------------------------------------|--------------------------|
| `header` | `--auth-param` のヘッダ名で付与        | `X-API-Key: ...`         |
| `query`  | URL クエリに `<param>=<key>` を追加    | `?api_key=...`           |
| `bearer` | `Authorization: Bearer <key>`          | OAuth Bearer Token       |
| `none`   | 付与しない（メタのみ保持）             | -                        |

### 鍵管理

```bash
python3 papis.py vault list           # 保管中エントリ一覧（鍵本体は出ない）
python3 papis.py vault get NAME       # 鍵を表示（debug 用、慎重に）
python3 papis.py vault rm NAME        # 削除
```

鍵本体は macOS Keychain（service=`papis`）。`~/.papis/index.json` にはスタイルのメタのみ保存（mode 0600）。

## データ構造

### SQLite (`data/apis.db`)

```sql
CREATE TABLE apis (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  url TEXT NOT NULL,
  description TEXT NOT NULL,
  category TEXT NOT NULL,
  auth TEXT NOT NULL,        -- 'No' | 'apiKey' | 'OAuth' | 'X-Mashape-Key' | 'User-Agent'
  https TEXT NOT NULL,       -- 'Yes' | 'No' | 'Unknown'
  cors TEXT NOT NULL         -- 'Yes' | 'No' | 'Unknown'
);
CREATE INDEX idx_apis_category ON apis(category);
CREATE INDEX idx_apis_name ON apis(name);

CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
-- meta: ('updated_at', '2026-05-08T08:50:00Z'), ('count', '1456')
```

### JSON (`data/apis.json`)

```json
{
  "updated_at": "2026-05-08T08:50:00Z",
  "count": 1456,
  "apis": [
    {
      "name": "Cat Facts",
      "url": "https://alexwohlbruck.github.io/cat-facts/",
      "description": "Daily cat facts",
      "category": "Animals",
      "auth": "No",
      "https": "Yes",
      "cors": "No"
    }
  ]
}
```

日付形式は ISO 8601 UTC (`YYYY-MM-DDTHH:MM:SSZ`)。

## 自動更新

`.github/workflows/update.yml` が毎週月曜 06:00 UTC に上流 README を取得し、`data/`
に差分があれば自動で commit & push する。手動実行は GitHub Actions 画面の
"Run workflow"（`workflow_dispatch`）から。

## 制限事項

- `vault` は macOS Keychain 専用（`security` CLI 依存）。Linux/Windows では別実装が必要。
- 上流 README のテーブル形式が変わると `parse_readme` の regex 調整が必要。
  parse 0 件で `build` は明示的に失敗するので静かには壊れない。
- 各 API ごとの「実際のエンドポイント URL / 必須パラメータ」は upstream に無い。
  `show` で得られる URL は公式ドキュメントへのリンクであり、`call` では各自で実 URL を渡す必要がある。
- レート制限・利用規約は各 API 公式に従うこと。

## メンテナンス

- 上流 README の構造変更で取得件数が大きくブレた場合は `papis.py` の `ROW` regex を更新。
- 期待件数: `### Animals` 以降のテーブル行数（現状 1456）と完全一致するのが正常。

## ライセンス

公的データセットのミラーであり、本リポジトリ独自のコードは MIT ライセンスを想定。
個別 API の利用条件は各サービス公式の規約に従うこと。
