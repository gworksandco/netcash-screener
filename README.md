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

## 今後の拡張予定（段階的実装）

1. ~~現行版（少数銘柄・yfinanceライブ取得）をデプロイし、動作確認~~ 完了
2. ~~東証全銘柄対応（GitHub Actionsによる夜間バッチ処理）~~ 完了
3. **EDINET API連携**：yfinanceのbalance_sheet項目欠損（特に「投資有価証券」）を、
   金融庁が提供する有価証券報告書のXBRLデータで補完・上書きする
   - EDINET APIはAPIキー登録が必要な仕様のため、キーはGitHub Actionsの
     Secrets（リポジトリのSettings > Secrets and variables > Actions）で管理し、
     コードにもリポジトリにも直接書き込まない

## 注意事項

- データソースは無料の `yfinance`（Yahoo! Finance非公式ラッパー）と
  JPX公式の銘柄一覧のため、項目名の表記ゆれや決算期のズレにより
  一部データが欠損する場合があります。欠損した項目は0円として扱い、
  「欠損項目」列で明示されます。
- 全銘柄スキャンは数十分〜数時間かかる場合があり、Yahoo!Finance側の
  レート制限により一部銘柄でエラーになることがあります
  （`data/errors.log` に記録されます）。
- 本アプリは教育・情報提供目的であり、投資助言ではありません。
