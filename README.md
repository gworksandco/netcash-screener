# 厳密ネットキャッシュ比率スクリーナー（東証全銘柄対応版）

清原達郎氏『わが投資術』のネットキャッシュ株の考え方に基づく日本株スクリーニングアプリです。
東証上場**全銘柄**（約3,900銘柄）を対象に、毎日自動でスキャンします。

## 仕組み

維持費0円のまま全銘柄をスキャンするため、以下の2段構成にしています。

1. **バッチ処理（GitHub Actions・毎日自動実行）**
   - `scripts/build_universe.py`：JPX公式の「東証上場銘柄一覧」から全銘柄コードを取得
   - `scripts/run_batch_screen.py`：全銘柄をyfinanceでスキャンし `data/results.csv` に保存
   - 結果はリポジトリに自動コミットされる
2. **Streamlitアプリ（閲覧用）**
   - `app.py` は `data/results.csv` を読み込んで表示するだけ
   - yfinanceを直接呼ばないため、表示は一瞬でレート制限の心配もない

## セットアップ（ローカルで動作確認する場合）

```bash
# アプリのみ動かす場合
pip install -r requirements.txt
streamlit run app.py

# バッチ処理を手動実行する場合
pip install -r scripts/requirements.txt
python scripts/build_universe.py
python scripts/run_batch_screen.py --limit 50   # まず50銘柄で動作確認
python scripts/run_batch_screen.py              # 全銘柄スキャン（数十分〜数時間かかる場合あり）
```

## デプロイ（GitHub + Streamlit Community Cloud、維持費0円）

1. 本リポジトリの内容をGitHubのパブリックリポジトリにpush
2. [share.streamlit.io](https://share.streamlit.io) にGitHubアカウントでサインイン
3. "New app" でリポジトリ・ブランチ・メインファイル（`app.py`）を指定してデプロイ
4. GitHub Actionsが毎日自動でデータを更新し、アプリはそれを表示するだけ

GitHub Actionsは `.github/workflows/daily_screen.yml` で定義されており、
毎日日本時間6:00頃（東証取引時間外）に自動実行されます。
Actionsタブから「Run workflow」で手動実行も可能です。
パブリックリポジトリであればActionsの実行時間は無料枠内に収まります。

## 計算ロジック

```
Net Cash = 流動資産 - (負債合計 × 負債倍率) + (投資有価証券 × 有価証券倍率)
1株ネットキャッシュ = Net Cash ÷ 発行済株式数
ネットキャッシュ比率 = 1株ネットキャッシュ ÷ 現在株価
乖離率(%) = (1株ネットキャッシュ - 現在株価) ÷ 現在株価 × 100
```

デフォルトは負債倍率1.1、有価証券倍率0.7（`scripts/run_batch_screen.py`の
引数で変更可能）。

## EDINET連携（財務データの精度向上）

yfinanceのbalance_sheet項目（特に「投資有価証券」）は欠損が多いため、
金融庁が提供するEDINET APIから有価証券報告書のデータを取得し、
より正確な数値で上書きする仕組みを追加しています。

- `scripts/edinet_client.py`：EDINET APIとの通信・XBRL-CSV解析
- `scripts/update_edinet_financials.py`：有価証券報告書を検索し、
  流動資産・負債合計・投資有価証券を抽出して`data/edinet_financials.csv`に保存
- `.github/workflows/weekly_edinet_update.yml`：週次で自動実行
  （EDINETの財務データは決算期にしか変わらないため、日次ではなく週次にしている）

`scripts/run_batch_screen.py`は、`data/edinet_financials.csv`に対応する
証券コードのデータがあれば、yfinance側の数値をEDINET側で上書きしてから
ネットキャッシュを再計算する。結果テーブルの「データ元」列で、
EDINETのデータが使われたか（`EDINET`）、yfinanceのみか（`yfinance`）を確認できる。

### セットアップ（EDINET APIキーの登録）

1. https://api.edinet-fsa.go.jp/api/auth/index.aspx?mode=1 で無料アカウントを作成し、
   APIキーを発行する
2. GitHubリポジトリの Settings → Secrets and variables → Actions →
   「New repository secret」で、名前 `EDINET_API_KEY`・値に取得したキーを登録する
3. Actionsタブから「Weekly EDINET Financial Update」を手動実行すると、
   初回は過去450日分の書類を走査するため時間がかかる
   （2回目以降は直近10日分だけの差分更新になり高速）

## 今後の拡張予定（段階的実装）

1. ~~現行版（少数銘柄・yfinanceライブ取得）をデプロイし、動作確認~~ 完了
2. ~~東証全銘柄対応（GitHub Actionsによる夜間バッチ処理）~~ 完了
3. ~~EDINET API連携~~ 完了

## 注意事項

- データソースは無料の `yfinance`（Yahoo! Finance非公式ラッパー）と
  JPX公式の銘柄一覧のため、項目名の表記ゆれや決算期のズレにより
  一部データが欠損する場合があります。欠損した項目は0円として扱い、
  「欠損項目」列で明示されます。
- 全銘柄スキャンは数十分〜数時間かかる場合があり、Yahoo!Finance側の
  レート制限により一部銘柄でエラーになることがあります
  （`data/errors.log` に記録されます）。
- EDINETのXBRLタグ付けは企業によって微妙に異なる場合があり
  （特に「投資有価証券」は連結・個別の扱いにばらつきがある）、
  EDINET連携後も一部銘柄で欠損が残る可能性があります。
- 本アプリは教育・情報提供目的であり、投資助言ではありません。
