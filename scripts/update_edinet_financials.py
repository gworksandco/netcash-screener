# -*- coding: utf-8 -*-
"""
scripts/update_edinet_financials.py
=====================================
EDINETから有価証券報告書を探し、流動資産・負債合計・投資有価証券を抽出して
data/edinet_financials.csv に保存する。週次のGitHub Actionsから呼び出される想定。

処理の流れ:
1. data/edinet_doc_cache.csv（銘柄コード→最新の有報docID）が無ければ、
   過去 --backfill-days 日分（デフォルト450日）を走査してゼロから構築する。
   既にあれば、直近 --recent-days 日分（デフォルト10日）だけ再走査して
   新しい提出（訂正報告書等）を反映する。
2. キャッシュに載っている各secCodeについて、docIDが前回から変わっていれば
   XBRL-CSV（type=5）をダウンロードして財務3項目を抽出し、
   data/edinet_financials.csv を更新する。

EDINETの財務データは決算期にしか更新されないため、この処理は
毎日ではなく週次で実行する想定（.github/workflows/weekly_edinet_update.yml）。
"""

import argparse
import datetime
import os
import sys
import time

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from edinet_client import (  # noqa: E402
    download_document_csv_zip,
    extract_financials_from_zip,
    find_latest_filings,
    get_api_key,
)

DOC_CACHE_PATH = "data/edinet_doc_cache.csv"
FINANCIALS_PATH = "data/edinet_financials.csv"


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--backfill-days", type=int, default=450,
                    help="初回構築時に遡る日数（デフォルト450日、通期分をカバー）")
    p.add_argument("--recent-days", type=int, default=10,
                    help="2回目以降、直近何日分を再走査するか（デフォルト10日）")
    p.add_argument("--sleep", type=float, default=0.3,
                    help="APIリクエスト間のウェイト秒数（デフォルト0.3秒）")
    p.add_argument("--limit", type=int, default=None,
                    help="動作確認用に処理銘柄数を制限する")
    return p.parse_args()


def date_range(days_back: int) -> list:
    today = datetime.date.today()
    return [
        (today - datetime.timedelta(days=i)).strftime("%Y-%m-%d")
        for i in range(days_back)
    ]


def load_doc_cache() -> pd.DataFrame:
    if os.path.exists(DOC_CACHE_PATH):
        return pd.read_csv(DOC_CACHE_PATH, dtype={"secCode": str})
    return pd.DataFrame(columns=["secCode", "docID", "docDescription", "submitDateTime", "filerName"])


def load_financials_cache() -> pd.DataFrame:
    if os.path.exists(FINANCIALS_PATH):
        return pd.read_csv(FINANCIALS_PATH, dtype={"secCode": str})
    return pd.DataFrame(columns=[
        "secCode", "docID", "流動資産_EDINET", "負債合計_EDINET",
        "投資有価証券_EDINET", "更新日",
    ])


def main():
    args = parse_args()
    api_key = get_api_key()

    doc_cache_df = load_doc_cache()
    is_first_run = doc_cache_df.empty

    days_back = args.backfill_days if is_first_run else args.recent_days
    print(f"{'初回構築' if is_first_run else '差分更新'}: 過去{days_back}日分の書類一覧を走査します")

    dates = date_range(days_back)
    latest = find_latest_filings(dates, api_key)
    print(f"見つかった有価証券報告書（銘柄数）: {len(latest)}")

    # 既存キャッシュとマージ（新しく見つかったものだけ更新）
    existing_map = {
        row["secCode"]: row.to_dict() for _, row in doc_cache_df.iterrows()
    }
    for sec_code, info in latest.items():
        existing = existing_map.get(sec_code)
        if existing is None or info["submitDateTime"] > str(existing.get("submitDateTime", "")):
            existing_map[sec_code] = {"secCode": sec_code, **info}

    doc_cache_df = pd.DataFrame(list(existing_map.values()))
    os.makedirs("data", exist_ok=True)
    doc_cache_df.to_csv(DOC_CACHE_PATH, index=False, encoding="utf-8-sig")
    print(f"書類キャッシュを保存しました: {DOC_CACHE_PATH} ({len(doc_cache_df)}銘柄)")

    # --- 財務データの抽出（docIDが変わった銘柄のみ再ダウンロード） ---
    financials_df = load_financials_cache()
    financials_map = {
        row["secCode"]: row.to_dict() for _, row in financials_df.iterrows()
    }

    targets = doc_cache_df.to_dict("records")
    if args.limit:
        targets = targets[: args.limit]

    updated_count = 0
    error_count = 0
    today_str = datetime.date.today().strftime("%Y-%m-%d")

    for i, row in enumerate(targets):
        sec_code = row["secCode"]
        doc_id = row["docID"]

        cached = financials_map.get(sec_code)
        if cached is not None and str(cached.get("docID")) == str(doc_id):
            continue  # 前回と同じ書類なら再取得不要

        try:
            zip_bytes = download_document_csv_zip(doc_id, api_key)
            figures = extract_financials_from_zip(zip_bytes)
            financials_map[sec_code] = {
                "secCode": sec_code,
                "docID": doc_id,
                "流動資産_EDINET": figures["current_assets"],
                "負債合計_EDINET": figures["total_liabilities"],
                "投資有価証券_EDINET": figures["investment_securities"],
                "更新日": today_str,
            }
            updated_count += 1
        except Exception as e:
            error_count += 1
            print(f"  {sec_code} ({doc_id}): エラー {e}")

        time.sleep(args.sleep)

        if (i + 1) % 100 == 0:
            print(f"進捗: {i + 1}/{len(targets)}")

    financials_df = pd.DataFrame(list(financials_map.values()))
    financials_df.to_csv(FINANCIALS_PATH, index=False, encoding="utf-8-sig")
    print(f"財務データを保存しました: {FINANCIALS_PATH} ({len(financials_df)}銘柄)")
    print(f"今回新規/更新: {updated_count}件, エラー: {error_count}件")


if __name__ == "__main__":
    main()
