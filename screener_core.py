# -*- coding: utf-8 -*-
"""
screener_core.py
=================
清原達郎流「厳密ネットキャッシュ比率」の計算ロジックを共通化したモジュール。
scripts/run_batch_screen.py から呼び出される。

このモジュール単体ではStreamlitに依存しないため、GitHub Actions上の
バッチ処理からも、ローカルでの動作確認からも同じロジックを使い回せる。
"""

from dataclasses import dataclass, field
from typing import Optional

import pandas as pd
import yfinance as yf


@dataclass
class ScreenResult:
    code: str
    name: str = ""
    price: Optional[float] = None
    market_cap: Optional[float] = None  # 円
    per: Optional[float] = None
    pbr: Optional[float] = None
    equity_ratio: Optional[float] = None  # %
    current_assets: Optional[float] = None
    total_liabilities: Optional[float] = None
    investment_securities: Optional[float] = None
    shares_outstanding: Optional[float] = None
    net_cash: Optional[float] = None
    net_cash_per_share: Optional[float] = None
    net_cash_ratio: Optional[float] = None  # 倍
    deviation_pct: Optional[float] = None  # %
    error: str = ""
    missing_fields: list = field(default_factory=list)


def _safe_get_row(df: pd.DataFrame, candidates: list) -> Optional[pd.Series]:
    """balance_sheet DataFrameから、候補名のうち最初に一致した行を返す。"""
    if df is None or df.empty:
        return None
    for name in candidates:
        if name in df.index:
            return df.loc[name]
    return None


def _first_valid_value(row: Optional[pd.Series]) -> Optional[float]:
    """Seriesの中から最初の有効な数値（最新期）を取得する。"""
    if row is None:
        return None
    for val in row.values:
        if val is not None and not pd.isna(val):
            try:
                return float(val)
            except (TypeError, ValueError):
                continue
    return None


def fetch_balance_sheet_items(ticker_obj: yf.Ticker) -> dict:
    """貸借対照表から流動資産・負債合計・投資有価証券の3項目を取得する。"""
    result = {
        "current_assets": None,
        "total_liabilities": None,
        "investment_securities": None,
        "missing_fields": [],
    }

    try:
        bs = ticker_obj.balance_sheet
    except Exception:
        bs = None

    row = _safe_get_row(bs, ["Total Current Assets", "Current Assets"])
    val = _first_valid_value(row)
    if val is None:
        result["missing_fields"].append("流動資産")
        val = 0.0
    result["current_assets"] = val

    row = _safe_get_row(
        bs,
        [
            "Total Liabilities Net Minority Interest",
            "Total Liab",
            "Total Liabilities",
        ],
    )
    val = _first_valid_value(row)
    if val is None:
        result["missing_fields"].append("負債合計")
        val = 0.0
    result["total_liabilities"] = val

    row = _safe_get_row(
        bs,
        [
            "Investments And Advances",
            "Long Term Investments",
            "Investmentin Financial Assets",
            "Other Investments",
        ],
    )
    val = _first_valid_value(row)
    if val is None:
        result["missing_fields"].append("投資有価証券")
        val = 0.0
    result["investment_securities"] = val

    return result


def fetch_single_stock(
    code: str, liability_multiplier: float, securities_multiplier: float
) -> ScreenResult:
    """1銘柄分のデータを取得し、ネットキャッシュ指標を計算する。"""
    res = ScreenResult(code=code)
    try:
        t = yf.Ticker(code)

        try:
            info = t.info
        except Exception:
            info = {}

        res.name = info.get("shortName") or info.get("longName") or code
        res.price = info.get("currentPrice") or info.get("regularMarketPrice")
        res.market_cap = info.get("marketCap")
        res.per = info.get("trailingPE")
        res.pbr = info.get("priceToBook")
        res.shares_outstanding = info.get("sharesOutstanding")

        try:
            bs = t.balance_sheet
            equity_row = _safe_get_row(
                bs,
                [
                    "Total Equity Gross Minority Interest",
                    "Stockholders Equity",
                    "Total Stockholder Equity",
                ],
            )
            assets_row = _safe_get_row(bs, ["Total Assets"])
            equity_val = _first_valid_value(equity_row)
            assets_val = _first_valid_value(assets_row)
            if equity_val is not None and assets_val:
                res.equity_ratio = (equity_val / assets_val) * 100.0
        except Exception:
            pass

        bs_items = fetch_balance_sheet_items(t)
        res.current_assets = bs_items["current_assets"]
        res.total_liabilities = bs_items["total_liabilities"]
        res.investment_securities = bs_items["investment_securities"]
        res.missing_fields = bs_items["missing_fields"]

        if res.price is None or res.shares_outstanding is None:
            res.error = "株価または発行済株式数を取得できませんでした"
            return res

        # 清原式 厳密ネットキャッシュ
        res.net_cash = (
            res.current_assets
            - (res.total_liabilities * liability_multiplier)
            + (res.investment_securities * securities_multiplier)
        )
        res.net_cash_per_share = res.net_cash / res.shares_outstanding

        if res.price:
            res.net_cash_ratio = res.net_cash_per_share / res.price
            res.deviation_pct = (
                (res.net_cash_per_share - res.price) / res.price
            ) * 100.0

    except Exception as e:
        res.error = f"データ取得エラー: {e}"

    return res


def result_to_row(r: ScreenResult) -> dict:
    """ScreenResultをCSV出力用のdictに変換する。"""
    return {
        "銘柄コード": r.code,
        "銘柄名": r.name,
        "現在株価": r.price,
        "時価総額(億円)": (r.market_cap / 1e8) if r.market_cap else None,
        "PER": r.per,
        "PBR": r.pbr,
        "自己資本比率(%)": r.equity_ratio,
        "流動資産": r.current_assets,
        "負債合計": r.total_liabilities,
        "投資有価証券": r.investment_securities,
        "発行済株式数": r.shares_outstanding,
        "Netキャッシュ": r.net_cash,
        "1株ネットキャッシュ": r.net_cash_per_share,
        "ネットキャッシュ比率": r.net_cash_ratio,
        "乖離率(%)": r.deviation_pct,
        "欠損項目": ", ".join(r.missing_fields) if r.missing_fields else "",
        "エラー": r.error,
    }
