# -*- coding: utf-8 -*-
"""
app.py
======
清原達郎流「厳密ネットキャッシュ比率」日本株スクリーナー（東証全銘柄対応版）

このアプリ自体はyfinanceを呼び出さない。GitHub Actionsが毎日夜間に
scripts/run_batch_screen.py を実行して data/results.csv を更新しており、
このapp.pyはその結果を読み込んでフィルタリング・表示するだけの軽量な
ビューアーとして動作する。

これにより:
- Streamlit Community Cloudの無料枠でもレート制限や実行時間超過を気にせず
  東証全銘柄（約3,900銘柄）を対象にできる
- 表示が一瞬で終わる
"""

import os

import pandas as pd
import streamlit as st

st.set_page_config(
    page_title="ネットキャッシュ比率スクリーナー",
    page_icon="💰",
    layout="wide",
)

RESULTS_PATH = "data/results.csv"
META_PATH = "data/last_updated.txt"


@st.cache_data(ttl=600)
def load_results() -> pd.DataFrame:
    if not os.path.exists(RESULTS_PATH):
        return pd.DataFrame()
    df = pd.read_csv(RESULTS_PATH)
    # 数値列を明示的にfloat化（CSV読み込み時の型ゆれ対策）
    numeric_cols = [
        "現在株価", "時価総額(億円)", "PER", "PBR", "自己資本比率(%)",
        "1株ネットキャッシュ", "ネットキャッシュ比率", "乖離率(%)",
    ]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    if "データ元" not in df.columns:
        df["データ元"] = "yfinance"
    if "業種" not in df.columns:
        df["業種"] = ""
    return df


def load_last_updated() -> str:
    if os.path.exists(META_PATH):
        with open(META_PATH, "r", encoding="utf-8") as f:
            return f.read().strip()
    return "未取得"


# ----------------------------------------------------------------------------
# サイドバー: フィルター条件
# ----------------------------------------------------------------------------
st.sidebar.header("⚙️ フィルター条件")

min_net_cash_ratio = st.sidebar.slider(
    "ネットキャッシュ比率（下限、倍）",
    min_value=0.0, max_value=3.0, value=1.0, step=0.1,
)
max_market_cap = st.sidebar.number_input(
    "時価総額 上限（億円）", min_value=1, max_value=1_000_000, value=500, step=10,
)
max_pbr = st.sidebar.slider("PBR 上限（倍）", min_value=0.0, max_value=5.0, value=1.0, step=0.1)
max_per = st.sidebar.slider("PER 上限（倍）", min_value=0.0, max_value=50.0, value=10.0, step=0.5)
min_equity_ratio = st.sidebar.slider(
    "自己資本比率 下限（%）", min_value=0.0, max_value=100.0, value=40.0, step=5.0
)

st.sidebar.divider()
st.sidebar.caption(
    "データはGitHub Actionsにより毎日夜間（東証取引時間外）に自動更新されます。"
    "このアプリ自体は事前計算済みのCSVを読み込むだけなので、フィルターの変更は即座に反映されます。"
)

# ----------------------------------------------------------------------------
# メイン画面
# ----------------------------------------------------------------------------
st.title("💰 ネットキャッシュ比率スクリーナー")
st.caption("清原達郎『わが投資術』のネットキャッシュ株の考え方に基づく東証全銘柄スクリーニング")

last_updated = load_last_updated()
st.info(f"📅 データ最終更新: {last_updated}（毎日自動更新）")

st.latex(r"Net\ Cash = 流動資産 - (負債合計 \times 1.1) + (投資有価証券 \times 0.7)")

df_raw = load_results()

if df_raw.empty:
    st.warning(
        "まだスキャン結果がありません。GitHub Actionsの初回実行が完了するまでお待ちください"
        "（Actionsタブから手動実行 'workflow_dispatch' も可能です）。"
    )
else:
    # エラーが記録されている行は除外
    if "エラー" in df_raw.columns:
        df_valid = df_raw[df_raw["エラー"].isna() | (df_raw["エラー"] == "")]
    else:
        df_valid = df_raw

    df_filtered = df_valid[
        (df_valid["ネットキャッシュ比率"] >= min_net_cash_ratio)
        & (df_valid["時価総額(億円)"] <= max_market_cap)
        & (df_valid["PBR"] <= max_pbr)
        & (df_valid["PBR"] > 0)
        & (df_valid["PER"] <= max_per)
        & (df_valid["PER"] > 0)
        & (df_valid["自己資本比率(%)"] >= min_equity_ratio)
    ].sort_values("ネットキャッシュ比率", ascending=False).reset_index(drop=True)
    df_filtered.index = df_filtered.index + 1

    # --- KPIカード ---
    st.subheader("📊 抽出結果サマリー")
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("スキャン対象銘柄数", f"{len(df_valid):,} 銘柄")
    col2.metric("抽出銘柄数", f"{len(df_filtered)} 銘柄")

    if not df_filtered.empty:
        top_row = df_filtered.iloc[0]
        col3.metric(
            "最高ネットキャッシュ比率",
            f"{top_row['ネットキャッシュ比率']:.2f}倍",
            help=f"{top_row['銘柄名']} ({top_row['銘柄コード']})",
        )
        col4.metric("平均PBR", f"{df_filtered['PBR'].mean():.2f}倍")
    else:
        col3.metric("最高ネットキャッシュ比率", "-")
        col4.metric("平均PBR", "-")

    st.divider()
    st.subheader("📋 スクリーニング結果一覧")

    display_cols = [
        "銘柄コード", "銘柄名", "業種", "現在株価", "時価総額(億円)", "PER", "PBR",
        "自己資本比率(%)", "1株ネットキャッシュ", "ネットキャッシュ比率", "乖離率(%)",
        "データ元",
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
                    "現在株価": "{:.1f}", "時価総額(億円)": "{:.0f}", "PER": "{:.1f}",
                    "PBR": "{:.2f}", "自己資本比率(%)": "{:.1f}",
                    "1株ネットキャッシュ": "{:.1f}", "ネットキャッシュ比率": "{:.2f}",
                    "乖離率(%)": "{:.1f}",
                }
            )
            .map(_highlight_ratio, subset=["ネットキャッシュ比率"])
        )
        st.dataframe(
            styled,
            use_container_width=True,
            height=450,
            column_config={
                "銘柄コード": st.column_config.TextColumn("銘柄コード", pinned=True),
                "銘柄名": st.column_config.TextColumn("銘柄名", pinned=True),
            },
        )

        csv_bytes = df_filtered[display_cols].to_csv(index=False).encode("utf-8-sig")
        st.download_button(
            label="⬇️ CSVダウンロード",
            data=csv_bytes,
            file_name="netcash_screening_result.csv",
            mime="text/csv",
        )

    with st.expander(f"🔎 全スキャン銘柄（{len(df_raw)}件、フィルター適用前）を見る"):
        st.dataframe(df_raw, use_container_width=True)

st.divider()

# --- 広告・アフィリエイトリンク（PR） ---
# テキストリンク用: {"label": 表示名, "url": リンク先}
AD_LINKS = [
    # {"label": "会社四季報オンライン", "url": "https://example.com/your-affiliate-link-1"},
]

# バナー画像広告用（A8.net等が発行するHTMLタグをそのまま貼り付ける）
AD_BANNERS_HTML = [
    """
<a href="https://px.a8.net/svt/ejp?a8mat=4BC6B3+ATDIEQ+4H2M+6BEQ9" rel="nofollow">
<img border="0" width="300" height="250" alt="" src="https://www29.a8.net/svt/bgt?aid=260908527654&wid=001&eno=01&mid=s00000020875001061000&mc=1"></a>
<img border="0" width="1" height="1" src="https://www10.a8.net/0.gif?a8mat=4BC6B3+ATDIEQ+4H2M+6BEQ9" alt="">
    """,
    """
<a href="https://px.a8.net/svt/ejp?a8mat=4BC6B5+G3ASDU+5V1I+HVNAP" rel="nofollow">
<img border="0" width="300" height="250" alt="" src="https://www29.a8.net/svt/bgt?aid=260908529973&wid=001&eno=01&mid=s00000027351003003000&mc=1"></a>
<img border="0" width="1" height="1" src="https://www17.a8.net/0.gif?a8mat=4BC6B5+G3ASDU+5V1I+HVNAP" alt="">
    """,
]

if AD_LINKS or AD_BANNERS_HTML:
    st.caption("PR")
    for ad in AD_LINKS:
        st.markdown(f"[{ad['label']}]({ad['url']})")

    st.markdown(
        "配当金が入ったら試したい、九州の“幻”のグルメ　"
        "大手サイトに出回らない本物の産直食材（マグロ・馬刺し・地酒など）が"
        "全品送料無料で手に入ります。銘柄分析の息抜きや、自分へのご褒美に。"
    )
    st.markdown(AD_BANNERS_HTML[0], unsafe_allow_html=True)

    st.markdown(
        "含み益が出たら一献、希少ウイスキー・古酒の専門店「リンクサス酒販」　"
        "市場でなかなか出会えない希少なウイスキー・古酒を取り扱っています。"
        "利益確定を祝う一本を探してみては。"
    )
    st.markdown(AD_BANNERS_HTML[1], unsafe_allow_html=True)

with st.expander("📄 免責事項・プライバシーポリシー"):
    st.markdown(
        """
**1. 投資情報の提供について（免責事項）**

本アプリが提供する財務指標、株価データ、計算結果（ネットキャッシュ比率等）は、公的データおよび外部API（EDINET、yfinance、JPX等）に基づき自動処理された情報です。データの正確性や最新性を保証するものではありません。

本アプリの情報は投資勧誘や売買の推奨を目的としたものではなく、投資に関する最終決定はご自身の判断と責任において行ってください。本アプリの利用により生じた直接的・間接的損害について、運営者は一切の責任を負いません。

**2. 広告の配信について（ステマ規制対応・Amazon等）**

本アプリでは、アフィリエイトプログラム（Amazonアソシエイト、各証券会社等のプロモーション）を利用して各種サービスや書籍を紹介する場合があります（PRが含まれます）。

※運営者はAmazon.co.jpを宣伝しリンクすることによって紹介料を獲得できる手段を提供することを目的に設定されたアフィリエイトプログラムである、Amazonアソシエイト・プログラムの参加者です。

**3. アクセス解析ツールについて**

本アプリでは、サービスの改善や利用状況の把握のため、Cookieを使用したアクセス解析ツールを利用する場合があります。データは匿名で収集されており、個人を特定するものではありません。
        """
    )
