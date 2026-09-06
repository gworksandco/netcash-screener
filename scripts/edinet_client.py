# -*- coding: utf-8 -*-
"""
scripts/edinet_client.py
=========================
EDINET API v2（金融庁）との通信ロジック。

- 書類一覧API: 指定日に提出された書類のメタデータを取得する
  （有価証券報告書などを探すために使う）
- 書類取得API (type=5): 有価証券報告書のXBRLをCSV化したファイルをZIPで取得し、
  流動資産・負債合計・投資有価証券の3項目を抽出する

EDINET APIの利用にはAPIキーが必要（無料登録）。
環境変数 EDINET_API_KEY から読み込む。
"""

import io
import os
import zipfile
from typing import Optional

import pandas as pd
import requests

BASE_URL = "https://api.edinet-fsa.go.jp/api/v2"

# 有価証券報告書・訂正有価証券報告書の書類種別コード
TARGET_DOC_TYPE_CODES = {"120", "130"}

# 財務3項目に対応するXBRL要素ID（日本の会計基準タクソノミ jppfs_cor）の候補
# 銘柄・年度によりタグの揺れがあるため候補を複数用意する
TAG_CANDIDATES = {
    "current_assets": [
        "jppfs_cor:CurrentAssets",
    ],
    "total_liabilities": [
        "jppfs_cor:Liabilities",
    ],
    "investment_securities": [
        "jppfs_cor:InvestmentSecurities",
        "jppfs_cor:InvestmentSecuritiesCNS",  # 一部企業で使われる連結用タグ
    ],
}


def get_api_key() -> str:
    key = os.environ.get("EDINET_API_KEY", "")
    if not key:
        raise RuntimeError(
            "環境変数 EDINET_API_KEY が設定されていません。"
            "GitHub Secretsに登録し、ワークフローのenvで渡してください。"
        )
    return key


def fetch_documents_list(date_str: str, api_key: str) -> list:
    """
    指定日（YYYY-MM-DD）に提出された書類のメタデータ一覧を取得する。
    type=2 で「提出書類一覧及びメタデータ」を取得する。
    """
    url = f"{BASE_URL}/documents.json"
    params = {"date": date_str, "type": 2, "Subscription-Key": api_key}
    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    return data.get("results", []) or []


def download_document_csv_zip(doc_id: str, api_key: str) -> bytes:
    """
    書類取得API (type=5: XBRLをCSV化したもの) でZIPファイルをダウンロードする。
    """
    url = f"{BASE_URL}/documents/{doc_id}"
    params = {"type": 5, "Subscription-Key": api_key}
    resp = requests.get(url, params=params, timeout=60)
    resp.raise_for_status()
    return resp.content


def _read_edinet_csv(raw_bytes: bytes) -> Optional[pd.DataFrame]:
    """
    EDINETのXBRL-CSVはUTF-16・タブ区切り。
    """
    try:
        return pd.read_csv(
            io.BytesIO(raw_bytes), sep="\t", encoding="utf-16"
        )
    except Exception:
        try:
            return pd.read_csv(
                io.BytesIO(raw_bytes), sep="\t", encoding="utf-16le"
            )
        except Exception:
            return None


def extract_financials_from_zip(zip_bytes: bytes) -> dict:
    """
    ZIP内のCSVファイル群から、流動資産・負債合計・投資有価証券を抽出する。
    連結決算のデータを優先し、なければ個別決算の値を使う。

    戻り値の各項目はfloat（円）または None（取得できなかった場合）。
    """
    result = {
        "current_assets": None,
        "total_liabilities": None,
        "investment_securities": None,
    }

    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except Exception:
        return result

    csv_names = [
        n for n in zf.namelist()
        if n.upper().startswith("XBRL_TO_CSV") and n.lower().endswith(".csv")
    ]
    if not csv_names:
        # 稀にディレクトリ構成が異なる場合があるので、拡張子だけで拾う
        csv_names = [n for n in zf.namelist() if n.lower().endswith(".csv")]

    for name in csv_names:
        try:
            raw = zf.read(name)
        except Exception:
            continue

        df = _read_edinet_csv(raw)
        if df is None or df.empty:
            continue

        # 想定される列名（EDINETの仕様変更で変わる可能性あり）
        required_cols = {"要素ID", "値", "連結・個別"}
        if not required_cols.issubset(set(df.columns)):
            continue

        for key, tag_list in TAG_CANDIDATES.items():
            if result[key] is not None:
                continue  # 既に他ファイルから取得済み

            subset = df[df["要素ID"].isin(tag_list)]
            if subset.empty:
                continue

            # 連結を優先、なければ個別
            for pref in ["連結", "個別"]:
                pref_rows = subset[subset["連結・個別"] == pref]
                if not pref_rows.empty:
                    try:
                        val = float(pref_rows.iloc[0]["値"])
                        result[key] = val
                        break
                    except (TypeError, ValueError):
                        continue

    return result


def find_latest_filings(dates: list, api_key: str, doc_type_codes=None) -> dict:
    """
    複数日分の書類一覧を走査し、証券コード(secCode)ごとに最新の
    有価証券報告書のdocIDを集める。

    戻り値: { secCode(5桁文字列): {"docID":..., "docDescription":..., "submitDateTime":...} }
    """
    if doc_type_codes is None:
        doc_type_codes = TARGET_DOC_TYPE_CODES

    latest = {}
    for date_str in dates:
        try:
            docs = fetch_documents_list(date_str, api_key)
        except Exception as e:
            print(f"  {date_str}: 取得失敗 ({e})")
            continue

        for doc in docs:
            if doc.get("docTypeCode") not in doc_type_codes:
                continue
            sec_code = doc.get("secCode")
            if not sec_code:
                continue  # 非上場企業などsecCodeが無いものはスキップ

            submit_time = doc.get("submitDateTime", "")
            existing = latest.get(sec_code)
            if existing is None or submit_time > existing.get("submitDateTime", ""):
                latest[sec_code] = {
                    "docID": doc.get("docID"),
                    "docDescription": doc.get("docDescription"),
                    "submitDateTime": submit_time,
                    "filerName": doc.get("filerName"),
                }

    return latest
