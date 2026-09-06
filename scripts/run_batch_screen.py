# -*- coding: utf-8 -*-
"""
scripts/run_batch_screen.py
============================
data/universe.csv に列挙された全銘柄をyfinanceでスキャンし、
清原式ネットキャッシュ指標を計算してdata/results.csvに保存する。

GitHub Actionsのスケジュール実行（.github/workflows/daily_screen.yml）から
呼び出される想定。ローカルでも `python scripts/run_batch_screen.py` で実行可能。

全銘柄（東証約3,900銘柄）を対象にするため、逐次処理だと時間がかかりすぎる。
ThreadPoolExecutorで並列化しつつ、Yahoo!Finance側への配慮として
並列数は控えめ（デフォルト8）に設定している。
"""

import argparse
import datetime
import os
import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from screener_core import fetch_single_stock, recompute_net_cash, result_to_row  # noqa: E402

UNIVERSE_PATH = "data/universe.csv"
OUTPUT_PATH = "data/results.csv"
META_PATH = "data/last_updated.txt"
EDINET_FINANCIALS_PATH = "data/edinet_financials.csv"


def ticker_to_sec_code(ticker: str) -> str:
    """'7203.T' -> '72030'（EDINETのsecCodeは証券コード4桁+末尾0の5桁）"""
    code = ticker.split(".")[0]
    return f"{code}0"


def load_edinet_financials() -> dict:
    """
    data/edinet_financials.csv（週次バッチで作成）を読み込み、
    secCode(5桁) → {current_assets, total_liabilities, investment_securities} の辞書を返す。
    ファイルが無ければ空の辞書（＝EDINET連携なしでyfinanceのみで動作）。
    """
    if not os.path.exists(EDINET_FINANCIALS_PATH):
        return {}
    df = pd.read_csv(EDINET_FINANCIALS_PATH, dtype={"secCode": str})
    result = {}
    for _, row in df.iterrows():
        result[row["secCode"]] = {
            "current_assets": row.get("流動資産_EDINET"),
            "total_liabilities": row.get("負債合計_EDINET"),
            "investment_securities": row.get("投資有価証券_EDINET"),
        }
    return result


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument(
        "--liability-multiplier",
        type=float,
        default=1.1,
        help="負債倍率（デフォルト1.1）",
    )
    p.add_argument(
        "--securities-multiplier",
        type=float,
        default=0.7,
        help="投資有価証券の割引率（デフォルト0.7）",
    )
    p.add_argument(
        "--workers",
        type=int,
        default=3,
        help="並列スレッド数（デフォルト3。上げすぎるとYahoo側にレート制限される可能性）",
    )
    p.add_argument(
        "--request-delay",
        type=float,
        default=0.4,
        help="各リクエスト送信前の最小ウェイト秒数（デフォルト0.4秒、バースト回避用）",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        help="動作確認用に処理銘柄数を制限する（例: --limit 50）",
    )
    return p.parse_args()


def main():
    args = parse_args()

    if not os.path.exists(UNIVERSE_PATH):
        print(
            f"{UNIVERSE_PATH} が見つかりません。先に scripts/build_universe.py を実行してください。",
            file=sys.stderr,
        )
        sys.exit(1)

    universe = pd.read_csv(UNIVERSE_PATH)
    tickers = universe["ticker"].dropna().unique().tolist()
    if args.limit:
        tickers = tickers[: args.limit]

    # yfinanceのshortNameは英語（ローマ字）表記になるため、
    # JPX公式一覧の日本語銘柄名（name列）をticker→日本語名の辞書として用意し、
    # 後でCSV出力時に上書きする。
    jp_name_map = dict(zip(universe["ticker"], universe["name"]))

    edinet_map = load_edinet_financials()
    if edinet_map:
        print(f"EDINET財務データを読み込みました: {len(edinet_map)}銘柄分")
    else:
        print("EDINET財務データが見つかりません（yfinanceのみで計算します）")

    total = len(tickers)
    print(f"対象銘柄数: {total}")

    rows = []
    errors = []
    start_time = time.time()

    def _task(code):
        # 各リクエストの前に少し待つことで、全体としてのリクエスト頻度を抑え、
        # Yahoo!Finance側の一時的なIPブロックが起きにくくする。
        time.sleep(args.request_delay + random.uniform(0, 0.2))
        return fetch_single_stock(code, args.liability_multiplier, args.securities_multiplier)

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(_task, code): code for code in tickers}
        done_count = 0
        for future in as_completed(futures):
            code = futures[future]
            done_count += 1
            try:
                res = future.result()

                # EDINETデータがあれば、欠損しがちな3項目を上書きして再計算する
                sec_code = ticker_to_sec_code(code)
                edinet_vals = edinet_map.get(sec_code)
                if edinet_vals:
                    overridden = []
                    for field_name, attr_name, label in [
                        ("current_assets", "current_assets", "流動資産"),
                        ("total_liabilities", "total_liabilities", "負債合計"),
                        ("investment_securities", "investment_securities", "投資有価証券"),
                    ]:
                        val = edinet_vals.get(field_name)
                        if val is not None and not pd.isna(val):
                            setattr(res, attr_name, float(val))
                            overridden.append(label)
                            if label in res.missing_fields:
                                res.missing_fields.remove(label)
                    if overridden:
                        res = recompute_net_cash(
                            res, args.liability_multiplier, args.securities_multiplier
                        )
                        res.data_source = "EDINET"

                row = result_to_row(res)
                rows.append(row)
                if res.error:
                    errors.append(f"{code}: {res.error}")
            except Exception as e:
                errors.append(f"{code}: 予期しないエラー {e}")

            if done_count % 100 == 0 or done_count == total:
                elapsed = time.time() - start_time
                print(f"進捗: {done_count}/{total} ({elapsed:.0f}秒経過)")

    df = pd.DataFrame(rows)

    # JPX公式の日本語銘柄名で上書き（yfinance側のローマ字名より優先）
    if not df.empty and "銘柄コード" in df.columns:
        df["銘柄名"] = df["銘柄コード"].map(jp_name_map).fillna(df["銘柄名"])

    os.makedirs("data", exist_ok=True)
    df.to_csv(OUTPUT_PATH, index=False, encoding="utf-8-sig")

    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9)))
    with open(META_PATH, "w", encoding="utf-8") as f:
        f.write(now.strftime("%Y-%m-%d %H:%M:%S JST"))

    print(f"完了: {OUTPUT_PATH} に {len(df)} 件保存しました。")
    print(f"エラー件数: {len(errors)}")
    if errors:
        error_log_path = "data/errors.log"
        with open(error_log_path, "w", encoding="utf-8") as f:
            f.write("\n".join(errors))
        print(f"エラー詳細は {error_log_path} に保存しました。")


if __name__ == "__main__":
    main()
