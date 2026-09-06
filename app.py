# -*- coding: utf-8 -*-
"""
清原達郎流「厳密ネットキャッシュ比率」日本株スクリーナー
=====================================================
『わが投資術』(清原達郎 著) で紹介されているネットキャッシュ株の考え方に基づき、
東証上場銘柄の中から財務的に安全性が高く、割安に放置されている銘柄を抽出する
Streamlit ダッシュボードです。

データソースはすべて無料の yfinance ライブラリを使用しています。
yfinance は Yahoo! Finance の非公式ラッパーであり、取得できる項目や精度は
銘柄・タイミングによってばらつきがあります。そのため本アプリでは、
財務データが欠損している場合は該当銘柄をスキップ（またはNC扱い）する
フォールバック処理を実装しています。

実行方法:
    pip install -r requirements.txt
    streamlit run app.py
"""

import time
import traceback
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd
import streamlit as st
import yfinance as yf

# ----------------------------------------------------------------------------
# ページ設定
# ----------------------------------------------------------------------------
st.set_page_config(
    page_title="厳密ネットキャッシュ比率スクリーナー",
    page_icon="💰",
    layout="wide",
)

# ----------------------------------------------------------------------------
# デフォルトの日本株ユニバース
# ----------------------------------------------------------------------------
# yfinance には「東証全銘柄を取得する」ようなスクリーニングAPIが存在しないため、
# ユーザーがサイドバーで銘柄コードを自由に指定/追加できるようにしています。
# 以下はサンプルとして、時価総額が中〜小型でネットキャッシュ的な性質を
# 持ちやすい銘柄を含む東証プライム/スタンダードの一部銘柄コードです。
# 実運用では、証券会社のスクリーニング機能等でエクスポートしたコードリストを
# 「銘柄コードリスト」欄に貼り付けて置き換えてください。
DEFAULT_TICKERS = [
    "7203.T", "6758.T", "8306.T", "9432.T", "6501.T",
    "7267.T", "8058.T", "6367.T", "6981.T", "4502.T",
    "7974.T", "6902.T", "9984.T", "8031.T", "6273.T",
    "7911.T", "7912.T", "7936.T", "7599.T", "7638.T",
]

DEFAULT_TICKER_TEXT = ", ".join(DEFAULT_TICKERS)


# ----------------------------------------------------------------------------
# データクラス: 1銘柄分の計算結果
# ----------------------------------------------------------------------------
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


# ----------------------------------------------------------------------------
# ヘルパー関数群
# ----------------------------------------------------------------------------
def _safe_get_row(df: pd.DataFrame, candidates: list) -> Optional[pd.Series]:
    """
    balance_sheet の DataFrame から、複数の想定インデックス名候補のうち
    最初に一致した行を返す。yfinanceはバージョンや銘柄によって
    項目名の表記ゆれ（例: 'Total Current Assets' vs 'Current Assets'）があるため
    候補リストで吸収する。
    """
    if df is None or df.empty:
        return None
    for name in candidates:
        if name in df.index:
            return df.loc[name]
    return None


def _first_valid_value(row: Optional[pd.Series]) -> Optional[float]:
    """Series（列=決算期）の中から最初の有効な数値（最新期）を取得する。"""
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
    """
    貸借対照表（Balance Sheet）から必要な3項目を取得する。
    項目が取得できない場合は None を返し、呼び出し側で0円フォールバックする。
    """
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

    # --- 流動資産 ---
    row = _safe_get_row(bs, ["Total Current Assets", "Current Assets"])
    val = _first_valid_value(row)
    if val is None:
        result["missing_fields"].append("流動資産")
        val = 0.0
    result["current_assets"] = val

    # --- 負債合計 ---
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

    # --- 投資有価証券 ---
    # yfinanceでは 'Investments And Advances' / 'Long Term Investments' /
    # 'Other Investments' などブレがあるため候補を広めに取る。
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
        # 投資有価証券が取得できないのはよくあるケースなので、警告のみで0扱いにする
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

        # --- 基本情報（株価・時価総額・PER・PBR等） ---
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

        # 自己資本比率はyfinanceのinfoに直接の項目が無いため、BSから計算する
        # （総資産に対する自己資本＝純資産の比率）
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

        # --- 貸借対照表の3項目 ---
        bs_items = fetch_balance_sheet_items(t)
        res.current_assets = bs_items["current_assets"]
        res.total_liabilities = bs_items["total_liabilities"]
        res.investment_securities = bs_items["investment_securities"]
        res.missing_fields = bs_items["missing_fields"]

        # --- 必須データの検証 ---
        if res.price is None or res.shares_outstanding is None:
            res.error = "株価または発行済株式数を取得できませんでした"
            return res

        # --- 清原式 厳密ネットキャッシュの計算 ---
        # Net Cash = 流動資産 - (負債合計 × 負債倍率) + (投資有価証券 × 有価証券倍率)
        res.net_cash = (
            res.current_assets
            - (res.total_liabilities * liability_multiplier)
            + (res.investment_securities * securities_multiplier)
        )

        # 1株あたりネットキャッシュ
        res.net_cash_per_share = res.net_cash / res.shares_outstanding

        # ネットキャッシュ比率 = 1株ネットキャッシュ ÷ 現在株価
        if res.price:
            res.net_cash_ratio = res.net_cash_per_share / res.price
            # 乖離率(%) = (1株ネットキャッシュ - 株価) / 株価 × 100
            res.deviation_pct = (
                (res.net_cash_per_share - res.price) / res.price
            ) * 100.0

    except Exception as e:
        res.error = f"データ取得エラー: {e}"

    return res


@st.cache_data(show_spinner=False, ttl=3600)
def run_screen(
    tickers: tuple, liability_multiplier: float, securities_multiplier: float
) -> pd.DataFrame:
    """
    複数銘柄をループ処理してスクリーニング結果のDataFrameを返す。
    yfinanceのAPIレート制限を考慮し、銘柄ごとに軽いウェイトを入れる。
    st.cache_data によって同一条件・同一銘柄リストの再計算を1時間キャッシュする。
    """
    rows = []
    errors = []

    progress = st.progress(0.0, text="データ取得中...")
    total = len(tickers)

    for i, code in enumerate(tickers):
        code = code.strip()
        if not code:
            continue
        res = fetch_single_stock(code, liability_multiplier, securities_multiplier)
        if res.error:
            errors.append(f"{code}: {res.error}")
        else:
            rows.append(res)
        progress.progress((i + 1) / max(total, 1), text=f"データ取得中... ({i + 1}/{total}) {code}")
        time.sleep(0.05)  # 簡易的なレート制限対策

    progress.empty()

    if errors:
        with st.expander(f"⚠️ 取得できなかった銘柄 ({len(errors)}件)"):
            for e in errors:
                st.write("- " + e)

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(
        [
            {
                "銘柄コード": r.code,
                "銘柄名": r.name,
                "現在株価": r.price,
                "時価総額(億円)": (r.market_cap / 1e8) if r.market_cap else np.nan,
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
                "欠損項目": ", ".join(r.missing_fields) if r.missing_fields else "-",
            }
            for r in rows
        ]
    )
    return df


# ----------------------------------------------------------------------------
# サイドバー: フィルター & パラメータ設定
# ----------------------------------------------------------------------------
st.sidebar.header("⚙️ スクリーニング条件")

with st.sidebar.expander("📋 対象銘柄コード", expanded=True):
    ticker_text = st.text_area(
        "銘柄コード（カンマ区切り、東証は末尾.T）",
        value=DEFAULT_TICKER_TEXT,
        height=120,
        help="証券会社のスクリーナー等でエクスポードしたコード一覧を貼り付けてください。",
    )

st.sidebar.subheader("計算パラメータ（清原式の割引係数）")
liability_multiplier = st.sidebar.slider(
    "負債倍率（負債を厳しく評価する係数）",
    min_value=1.0,
    max_value=1.5,
    value=1.1,
    step=0.05,
    help="負債合計に乗じる係数。デフォルト1.1倍で負債リスクを保守的に見積もる。",
)
securities_multiplier = st.sidebar.slider(
    "投資有価証券の割引率（評価係数）",
    min_value=0.0,
    max_value=1.0,
    value=0.7,
    step=0.05,
    help="投資有価証券に乗じる係数。含み益課税・流動性リスクを考慮しデフォルト0.7倍。",
)

st.sidebar.subheader("フィルター条件")
min_net_cash_ratio = st.sidebar.slider(
    "ネットキャッシュ比率（下限、倍）",
    min_value=0.0,
    max_value=3.0,
    value=1.0,
    step=0.1,
)
max_market_cap = st.sidebar.number_input(
    "時価総額 上限（億円）",
    min_value=1,
    max_value=100000,
    value=500,
    step=10,
)
max_pbr = st.sidebar.slider(
    "PBR 上限（倍）", min_value=0.0, max_value=5.0, value=1.0, step=0.1
)
max_per = st.sidebar.slider(
    "PER 上限（倍）", min_value=0.0, max_value=50.0, value=10.0, step=0.5
)
min_equity_ratio = st.sidebar.slider(
    "自己資本比率 下限（%）", min_value=0.0, max_value=100.0, value=40.0, step=5.0
)

run_button = st.sidebar.button("🔍 スクリーニング実行", type="primary", use_container_width=True)

st.sidebar.caption(
    "※ データは yfinance (Yahoo!Finance) による無料取得のため、"
    "決算期のズレや項目欠損が発生する場合があります。"
    "投資判断は必ずご自身の一次情報確認のうえで行ってください。"
)

# ----------------------------------------------------------------------------
# メイン画面
# ----------------------------------------------------------------------------
st.title("💰 厳密ネットキャッシュ比率スクリーナー")
st.caption("清原達郎『わが投資術』のネットキャッシュ株の考え方に基づく日本株スクリーニングツール")

st.latex(
    r"Net\ Cash = 流動資産 - (負債合計 \times 負債倍率) + (投資有価証券 \times 有価証券倍率)"
)

if "screen_df" not in st.session_state:
    st.session_state["screen_df"] = pd.DataFrame()

if run_button:
    tickers = tuple(t.strip() for t in ticker_text.split(",") if t.strip())
    if not tickers:
        st.warning("銘柄コードを1つ以上入力してください。")
    else:
        with st.spinner("財務データを取得・計算しています..."):
            df = run_screen(tickers, liability_multiplier, securities_multiplier)
        st.session_state["screen_df"] = df

df_raw = st.session_state["screen_df"]

if df_raw.empty:
    st.info("サイドバーの「スクリーニング実行」ボタンを押してデータを取得してください。")
else:
    # --- フィルター適用 ---
    df_filtered = df_raw.copy()
    df_filtered = df_filtered[
        (df_filtered["ネットキャッシュ比率"] >= min_net_cash_ratio)
        & (df_filtered["時価総額(億円)"] <= max_market_cap)
        & (df_filtered["PBR"] <= max_pbr)
        & (df_filtered["PBR"] > 0)
        & (df_filtered["PER"] <= max_per)
        & (df_filtered["PER"] > 0)
        & (df_filtered["自己資本比率(%)"] >= min_equity_ratio)
    ]

    # ネットキャッシュ比率が高い順にソート（デフォルト）
    df_filtered = df_filtered.sort_values("ネットキャッシュ比率", ascending=False)

    # --- KPIカード ---
    st.subheader("📊 抽出結果サマリー")
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("抽出銘柄数", f"{len(df_filtered)} 銘柄")

    if not df_filtered.empty:
        top_row = df_filtered.iloc[0]
        col2.metric(
            "最高ネットキャッシュ比率",
            f"{top_row['ネットキャッシュ比率']:.2f}倍",
            help=f"{top_row['銘柄名']} ({top_row['銘柄コード']})",
        )
        col3.metric("平均PBR", f"{df_filtered['PBR'].mean():.2f}倍")
        col4.metric("平均乖離率", f"{df_filtered['乖離率(%)'].mean():.1f}%")
    else:
        col2.metric("最高ネットキャッシュ比率", "-")
        col3.metric("平均PBR", "-")
        col4.metric("平均乖離率", "-")

    st.divider()

    # --- データテーブル ---
    st.subheader("📋 スクリーニング結果一覧")

    display_cols = [
        "銘柄コード",
        "銘柄名",
        "現在株価",
        "時価総額(億円)",
        "PER",
        "PBR",
        "自己資本比率(%)",
        "1株ネットキャッシュ",
        "ネットキャッシュ比率",
        "乖離率(%)",
    ]

    def _highlight_ratio(val):
        if isinstance(val, (int, float)) and val > 1.0:
            return "background-color: #d4f7dc; font-weight: bold;"
        return ""

    if df_filtered.empty:
        st.warning("条件に合致する銘柄はありませんでした。フィルター条件を緩めてみてください。")
    else:
        styled = (
            df_filtered[display_cols]
            .style.format(
                {
                    "現在株価": "{:.1f}",
                    "時価総額(億円)": "{:.0f}",
                    "PER": "{:.1f}",
                    "PBR": "{:.2f}",
                    "自己資本比率(%)": "{:.1f}",
                    "1株ネットキャッシュ": "{:.1f}",
                    "ネットキャッシュ比率": "{:.2f}",
                    "乖離率(%)": "{:.1f}",
                }
            )
            .map(_highlight_ratio, subset=["ネットキャッシュ比率"])
        )
        st.dataframe(styled, use_container_width=True, height=450)

        # --- CSVダウンロード ---
        csv_bytes = df_filtered[display_cols].to_csv(index=False).encode("utf-8-sig")
        st.download_button(
            label="⬇️ CSVダウンロード",
            data=csv_bytes,
            file_name="netcash_screening_result.csv",
            mime="text/csv",
        )

    with st.expander("🔎 全取得銘柄（フィルター適用前）を見る"):
        st.dataframe(df_raw, use_container_width=True)

st.divider()
st.caption(
    "本アプリは教育・情報提供のみを目的としており、投資助言ではありません。"
    "データの正確性は保証されません。投資判断は自己責任で行ってください。"
)
