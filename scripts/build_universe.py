# -*- coding: utf-8 -*-
"""
scripts/build_universe.py
==========================
JPX（日本取引所グループ）が無料公開している「東証上場銘柄一覧」(data_j.xls) を
ダウンロードし、全銘柄のyfinance用コード（例: 7203.T）一覧を作成する。

出力: data/universe.csv （列: コード, 銘柄名, 市場区分, 33業種区分, ticker）

JPXの当該ファイルは通常「その他統計資料 > 東証上場銘柄一覧」ページで
毎月更新されている無料の公式データです。
"""

import io
import sys

import pandas as pd
import requests

JPX_URL = (
    "https://www.jpx.co.jp/markets/statistics-equities/misc/"
    "tvdivq0000001vg2-att/data_j.xls"
)

OUTPUT_PATH = "data/universe.csv"

# 対象外にしたい市場・商品区分（ETF・REIT・優先出資証券など、通常株式でないもの）
EXCLUDE_MARKET_KEYWORDS = ["ETF", "REIT", "出資証券", "優先出資"]


def main():
    print(f"JPXから銘柄一覧を取得中: {JPX_URL}")
    try:
        resp = requests.get(JPX_URL, timeout=30)
        resp.raise_for_status()
    except Exception as e:
        print(f"ダウンロードに失敗しました: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        #data_j.xlsx は旧形式のExcel(.xls)。xlrdライブラリが必要。
        df = pd.read_excel(io.BytesIO(resp.content))
    except Exception as e:
        print(f"Excelの読み込みに失敗しました: {e}", file=sys.stderr)
        sys.exit(1)

    # 想定される列名（JPXの仕様変更で列名が変わる場合はここを調整）
    expected_cols = ["コード", "銘柄名", "市場・商品区分", "33業種区分"]
    missing = [c for c in expected_cols if c not in df.columns]
    if missing:
        print(f"警告: 想定した列が見つかりません: {missing}", file=sys.stderr)
        print(f"実際の列: {list(df.columns)}", file=sys.stderr)

    df = df.rename(
        columns={
            "コード": "code",
            "銘柄名": "name",
            "市場・商品区分": "market",
            "33業種区分": "sector",
        }
    )

    # ETF/REIT等を除外（通常株式のみ対象）
    if "market" in df.columns:
        mask = ~df["market"].astype(str).str.contains(
            "|".join(EXCLUDE_MARKET_KEYWORDS), na=False
        )
        df = df[mask]

    # コードを4桁文字列に正規化し、ticker列（yfinance用）を作成
    df["code"] = df["code"].astype(str).str.strip()
    df = df[df["code"].str.match(r"^\d{4}$", na=False)]  # 4桁数字コードのみ
    df["ticker"] = df["code"] + ".T"

    df = df.drop_duplicates(subset=["ticker"]).reset_index(drop=True)

    import os

    os.makedirs("data", exist_ok=True)
    df.to_csv(OUTPUT_PATH, index=False, encoding="utf-8-sig")
    print(f"銘柄ユニバースを保存しました: {OUTPUT_PATH} ({len(df)}銘柄)")


if __name__ == "__main__":
    main()
