"""
医師負荷可視化ツール
- ローカル実行時 : data/ フォルダの CSV を使用
- Streamlit Cloud : Google Sheets を使用（st.secrets で設定）
"""

import streamlit as st
import pandas as pd
import os
import io
from datetime import date

# ===== 定数 =====
DATA_DIR = "data"
CSV_PATH = os.path.join(DATA_DIR, "doctors.csv")
MEMBER_CSV_PATH = os.path.join(DATA_DIR, "members.csv")

LOW_THRESHOLD = 40
HIGH_THRESHOLD = 70

COLUMNS = [
    "日付", "医師名", "受け持ち患者数", "重症患者数", "新規入院数",
    "退院予定数",
    "プラザ外来_午前", "プラザ外来_午後",
    "総合外来_患者数",
    "当直明け", "当直入り", "会議時刻", "主観的余裕", "新規受入可否", "メモ"
]

DEFAULT_MEMBERS = [
    "山川", "鈴木", "横倉", "髙﨑", "庄司",
    "五十嵐", "橋本", "伊藤", "青島", "日比野"
]

DEFAULT_WEIGHTS = {
    "受け持ち患者数": 3,
    "重症患者数": 10,
    "新規入院数": 12,
    "退院予定数": -4,
    "プラザ外来_午前": 15,
    "プラザ外来_午後": 15,
    "プラザ外来_反対時間帯追加": 10,
    "総合外来_患者数": 3,
    "当直明け": 20,
    "当直入り": 10,
    "主観的余裕_係数": 5,
}


# ===== Google Sheets 接続 =====

def get_gsheet_client():
    """Streamlit Cloud 上で Google Sheets クライアントを返す。失敗したら例外を再送出。"""
    import gspread
    from google.oauth2.service_account import Credentials
    creds = Credentials.from_service_account_info(
        st.secrets["gcp_service_account"],
        scopes=[
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/drive",
        ],
    )
    return gspread.authorize(creds)


def get_or_create_worksheet(sh, name: str, rows: int = 1000, cols: int = 20):
    """指定名のワークシートを取得。なければ作成して返す。"""
    import gspread
    try:
        return sh.worksheet(name)
    except gspread.exceptions.WorksheetNotFound:
        return sh.add_worksheet(title=name, rows=rows, cols=cols)


def load_weights() -> dict:
    if use_gsheet():
        try:
            gc = get_gsheet_client()
            sh = gc.open_by_key(st.secrets["spreadsheet_id"])
            ws = get_or_create_worksheet(sh, "settings", rows=50, cols=2)
            records = ws.get_all_records()
            w = DEFAULT_WEIGHTS.copy()
            for r in records:
                if r.get("key") in w:
                    w[r["key"]] = int(r["value"])
            return w
        except Exception:
            return DEFAULT_WEIGHTS.copy()
    path = os.path.join(DATA_DIR, "settings.csv")
    if os.path.exists(path):
        try:
            w = DEFAULT_WEIGHTS.copy()
            df = pd.read_csv(path, dtype=str)
            for _, row in df.iterrows():
                if row["key"] in w:
                    w[row["key"]] = int(row["value"])
            return w
        except Exception:
            pass
    return DEFAULT_WEIGHTS.copy()


def save_weights(w: dict):
    rows = [["key", "value"]] + [[k, v] for k, v in w.items()]
    if use_gsheet():
        try:
            gc = get_gsheet_client()
            sh = gc.open_by_key(st.secrets["spreadsheet_id"])
            ws = get_or_create_worksheet(sh, "settings", rows=50, cols=2)
            ws.clear()
            ws.update("A1", rows)
        except Exception as e:
            st.error(f"設定の保存に失敗しました: {e}")
        return
    os.makedirs(DATA_DIR, exist_ok=True)
    pd.DataFrame(rows[1:], columns=["key", "value"]).to_csv(
        os.path.join(DATA_DIR, "settings.csv"), index=False
    )


def use_gsheet() -> bool:
    """Google Sheets を使う環境かどうかを判定する。"""
    return "gcp_service_account" in st.secrets and "spreadsheet_id" in st.secrets


# ===== 医師名簿の入出力 =====

def load_members() -> list[str]:
    if use_gsheet():
        try:
            gc = get_gsheet_client()
            sh = gc.open_by_key(st.secrets["spreadsheet_id"])
            ws = get_or_create_worksheet(sh, "members")
            values = ws.col_values(1)  # A列を全部取得
            members = [v for v in values if v and v != "医師名"]
            return members if members else DEFAULT_MEMBERS
        except Exception as e:
            st.warning(f"名簿の読み込みに失敗しました: [{type(e).__name__}] {e!r}")
            return DEFAULT_MEMBERS
    # ローカル CSV
    os.makedirs(DATA_DIR, exist_ok=True)
    if os.path.exists(MEMBER_CSV_PATH):
        try:
            return pd.read_csv(MEMBER_CSV_PATH, dtype=str)["医師名"].dropna().tolist()
        except Exception:
            pass
    save_members(DEFAULT_MEMBERS)
    return DEFAULT_MEMBERS.copy()


def save_members(members: list[str]):
    if use_gsheet():
        try:
            gc = get_gsheet_client()
            sh = gc.open_by_key(st.secrets["spreadsheet_id"])
            ws = get_or_create_worksheet(sh, "members")
            ws.clear()
            ws.update("A1", [["医師名"]] + [[m] for m in members])
        except Exception as e:
            st.error(f"名簿の保存に失敗しました: {e}")
        return
    os.makedirs(DATA_DIR, exist_ok=True)
    pd.DataFrame({"医師名": members}).to_csv(MEMBER_CSV_PATH, index=False, encoding="utf-8-sig")


# ===== 日次データの入出力 =====

def _normalize_df(df: pd.DataFrame) -> pd.DataFrame:
    """読み込んだDataFrameの列型を正規化する。"""
    for col in COLUMNS:
        if col not in df.columns:
            df[col] = "0" if col == "総合外来_患者数" else "False"
    for col in ["受け持ち患者数", "重症患者数", "新規入院数", "退院予定数", "主観的余裕", "総合外来_患者数"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)
    for col in ["プラザ外来_午前", "プラザ外来_午後", "当直明け", "当直入り"]:
        df[col] = df[col].map({"True": True, "False": False, True: True, False: False}).fillna(False)
    # 旧 bool 型の会議あり列を移行
    df["会議時刻"] = df["会議時刻"].map(
        {"True": "不明", "False": "なし", True: "不明", False: "なし"}
    ).fillna(df["会議時刻"]).fillna("なし")
    return df[COLUMNS]


def load_data() -> pd.DataFrame:
    if use_gsheet():
        try:
            gc = get_gsheet_client()
            sh = gc.open_by_key(st.secrets["spreadsheet_id"])
            ws = get_or_create_worksheet(sh, "doctors")
            records = ws.get_all_records()
            if not records:
                return pd.DataFrame(columns=COLUMNS)
            df = pd.DataFrame(records)
            return _normalize_df(df)
        except Exception as e:
            st.warning(f"データの読み込みに失敗しました: [{type(e).__name__}] {e!r}")
            return pd.DataFrame(columns=COLUMNS)
    # ローカル CSV
    os.makedirs(DATA_DIR, exist_ok=True)
    if os.path.exists(CSV_PATH):
        try:
            df = pd.read_csv(CSV_PATH, dtype=str)
            if df.empty:
                return pd.DataFrame(columns=COLUMNS)
            # 旧フォーマット移行
            if "外来" in df.columns and "プラザ外来_午前" not in df.columns:
                df["プラザ外来_午前"] = df["外来"].isin(["午前"]).map({True: "True", False: "False"})
                df["プラザ外来_午後"] = df["外来"].isin(["午後"]).map({True: "True", False: "False"})
                df["総合外来_患者数"] = "0"
                df = df.drop(columns=["外来"])
            return _normalize_df(df)
        except Exception as e:
            st.warning(f"データの読み込みに失敗しました: {e}")
    return pd.DataFrame(columns=COLUMNS)


def save_data(df: pd.DataFrame):
    if use_gsheet():
        try:
            gc = get_gsheet_client()
            sh = gc.open_by_key(st.secrets["spreadsheet_id"])
            ws = get_or_create_worksheet(sh, "doctors")
            ws.clear()
            # ヘッダー＋全行を一括書き込み
            rows = [COLUMNS] + df[COLUMNS].astype(str).values.tolist()
            ws.update("A1", rows)
        except Exception as e:
            st.error(f"データの保存に失敗しました: {e}")
        return
    os.makedirs(DATA_DIR, exist_ok=True)
    df.to_csv(CSV_PATH, index=False, encoding="utf-8-sig")


# ===== スコア計算 =====

def calc_load_score(row: pd.Series, w: dict | None = None) -> int:
    return sum(calc_score_breakdown(row, w).values())


def calc_score_breakdown(row: pd.Series, w: dict | None = None) -> dict:
    if w is None:
        w = st.session_state.get("weights", DEFAULT_WEIGHTS)
    b = {}
    b["受け持ち患者数"] = int(row["受け持ち患者数"]) * w["受け持ち患者数"]
    b["重症患者数"] = int(row["重症患者数"]) * w["重症患者数"]
    b["新規入院数"] = int(row["新規入院数"]) * w["新規入院数"]
    b["退院予定数"] = int(row["退院予定数"]) * w["退院予定数"]
    b["プラザ外来_午前"] = w["プラザ外来_午前"] if row["プラザ外来_午前"] in [True, "True"] else 0
    b["プラザ外来_午後"] = w["プラザ外来_午後"] if row["プラザ外来_午後"] in [True, "True"] else 0
    b["総合外来_患者数"] = int(row["総合外来_患者数"]) * w["総合外来_患者数"]
    b["当直明け"] = w["当直明け"] if row["当直明け"] in [True, "True"] else 0
    b["当直入り"] = w["当直入り"] if row["当直入り"] in [True, "True"] else 0
    b["主観的余裕"] = (6 - int(row["主観的余裕"])) * w["主観的余裕_係数"]
    total = max(sum(b.values()), 0)
    # 合計が0未満にならないよう調整（個別はそのまま）
    return {k: v for k, v in b.items()}


def get_load_label(score: int) -> str:
    if score < LOW_THRESHOLD:
        return "低負荷"
    elif score < HIGH_THRESHOLD:
        return "中等度"
    else:
        return "高負荷"


def get_load_color(score: int) -> str:
    if score < LOW_THRESHOLD:
        return "🟢"
    elif score < HIGH_THRESHOLD:
        return "🟡"
    else:
        return "🔴"


def plaza_label(row: pd.Series) -> str:
    slots = []
    if row["プラザ外来_午前"] in [True, "True"]:
        slots.append("午前")
    if row["プラザ外来_午後"] in [True, "True"]:
        slots.append("午後")
    return "・".join(slots) if slots else "なし"


def unavailable_slots(row: pd.Series) -> list[str]:
    slots = []
    if row["プラザ外来_午前"] in [True, "True"]:
        slots.append("午前")
    if row["プラザ外来_午後"] in [True, "True"]:
        slots.append("午後")
    return slots


# ===== パスワード認証 =====

def check_password() -> bool:
    return True


# ===== Streamlit アプリ本体 =====

def main():
    st.set_page_config(page_title="医師負荷可視化ツール", page_icon="🏥", layout="wide")

    if not check_password():
        return

    if "df" not in st.session_state:
        st.session_state.df = load_data()
    if "members" not in st.session_state:
        st.session_state.members = load_members()
    if "weights" not in st.session_state:
        st.session_state.weights = load_weights()

    df = st.session_state.df
    members = st.session_state.members

    st.title("🏥 医師負荷可視化ツール")
    st.caption("新規入院割り振りの判断支援・チーム医療安全のためのツールです。")

    page = st.sidebar.radio(
        "画面を選択",
        ["✏️ 日次入力（医師用）", "📊 ダッシュボード", "🆕 新規入院アサイン支援", "⚙️ 設定"]
    )

    st.sidebar.markdown("---")
    st.sidebar.markdown(f"**今日の日付：** {date.today()}")

    today = str(date.today())
    today_count = df[df["日付"] == today]["医師名"].nunique() if not df.empty else 0
    st.sidebar.markdown(f"**本日入力済み：** {today_count} / {len(members)} 名")

    if members:
        today_done = df[df["日付"] == today]["医師名"].tolist() if not df.empty else []
        not_yet = [m for m in members if m not in today_done]
        if not_yet:
            with st.sidebar.expander(f"未入力 {len(not_yet)} 名"):
                for name in not_yet:
                    st.markdown(f"- {name}")


    if page == "📊 ダッシュボード":
        show_dashboard(df)
    elif page == "✏️ 日次入力（医師用）":
        show_input_form(df, members)
    elif page == "🆕 新規入院アサイン支援":
        show_assign_support(df)
    elif page == "⚙️ 設定":
        show_settings(members)


# ===== ダッシュボード画面 =====

def show_dashboard(df: pd.DataFrame):
    st.header("📊 本日の医師負荷ダッシュボード")

    today = str(date.today())
    today_df = df[df["日付"] == today].copy() if not df.empty else pd.DataFrame(columns=COLUMNS)

    if today_df.empty:
        st.info("今日のデータがまだありません。各医師が「日次入力」から入力してください。")
        if not df.empty:
            st.markdown("---")
            latest_date = df["日付"].max()
            today_df = df[df["日付"] == latest_date].copy()
            st.caption(f"⚠️ 最新データ（{latest_date}）を表示しています")
        else:
            return

    today_df["負荷スコア"] = today_df.apply(calc_load_score, axis=1)
    today_df["負荷レベル"] = today_df["負荷スコア"].apply(get_load_label)
    today_df["状態"] = today_df["負荷スコア"].apply(get_load_color)
    today_df["プラザ外来"] = today_df.apply(plaza_label, axis=1)
    today_df = today_df.sort_values("負荷スコア", ascending=False).reset_index(drop=True)

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("入力済み医師数", f"{len(today_df)} 名")
    with col2:
        st.metric("🟢 低負荷", f"{(today_df['負荷スコア'] < LOW_THRESHOLD).sum()} 名")
    with col3:
        mid = ((today_df["負荷スコア"] >= LOW_THRESHOLD) & (today_df["負荷スコア"] < HIGH_THRESHOLD)).sum()
        st.metric("🟡 中等度", f"{mid} 名")
    with col4:
        st.metric("🔴 高負荷", f"{(today_df['負荷スコア'] >= HIGH_THRESHOLD).sum()} 名")

    st.markdown("---")

    display_df = today_df[[
        "状態", "医師名", "負荷スコア", "負荷レベル",
        "受け持ち患者数", "重症患者数", "新規入院数", "退院予定数",
        "プラザ外来", "総合外来_患者数",
        "当直明け", "当直入り", "会議時刻", "主観的余裕", "新規受入可否", "メモ"
    ]].rename(columns={
        "状態": "●",
        "総合外来_患者数": "総合外来(人)",
        "当直明け": "当直明",
        "当直入り": "当直入",
        "主観的余裕": "余裕(1-5)",
    })
    bool_disp = {True: "あり", False: "なし", "True": "あり", "False": "なし"}
    display_df["当直明"] = display_df["当直明"].map(bool_disp)
    display_df["当直入"] = display_df["当直入"].map(bool_disp)
    st.dataframe(display_df, use_container_width=True, hide_index=True)

    st.markdown("---")
    st.subheader("負荷スコア比較")
    st.bar_chart(today_df[["医師名", "負荷スコア"]].set_index("医師名"))
    st.caption(
        f"🟢 低負荷：{LOW_THRESHOLD}点未満　"
        f"🟡 中等度：{LOW_THRESHOLD}〜{HIGH_THRESHOLD-1}点　"
        f"🔴 高負荷：{HIGH_THRESHOLD}点以上"
    )

    st.markdown("---")
    with st.expander("📊 スコア内訳を見る"):
        w = st.session_state.get("weights", DEFAULT_WEIGHTS)
        label_map = {
            "受け持ち患者数": f"受け持ち患者数（×{w['受け持ち患者数']}点）",
            "重症患者数": f"重症患者数（×{w['重症患者数']}点）",
            "新規入院数": f"新規入院数（×{w['新規入院数']}点）",
            "退院予定数": f"退院予定数（×{w['退院予定数']}点）",
            "プラザ外来_午前": f"プラザ外来・午前（+{w['プラザ外来_午前']}点）",
            "プラザ外来_午後": f"プラザ外来・午後（+{w['プラザ外来_午後']}点）",
            "総合外来_患者数": f"総合外来患者数（×{w['総合外来_患者数']}点）",
            "当直明け": f"当直明け（+{w['当直明け']}点）",
            "当直入り": f"当直入り（+{w['当直入り']}点）",
            "主観的余裕": f"主観的余裕（余裕1→+{5*w['主観的余裕_係数']}点、余裕5→+{1*w['主観的余裕_係数']}点）",
        }
        breakdown_rows = []
        for _, row in today_df.iterrows():
            bd = calc_score_breakdown(row, w)
            r = {"医師名": row["医師名"]}
            r.update({label_map[k]: v for k, v in bd.items()})
            r["合計"] = max(sum(bd.values()), 0)
            breakdown_rows.append(r)
        bd_df = pd.DataFrame(breakdown_rows).set_index("医師名")
        st.dataframe(bd_df, use_container_width=True)


# ===== 日次入力画面（医師用） =====

def show_input_form(df: pd.DataFrame, members: list[str]):
    st.header("✏️ 日次入力")
    st.info("自分の名前を選んで、今日の状況を入力してください。同じ日に再入力すると上書きされます。")

    if not members:
        st.warning("医師名簿が空です。「医師名簿管理」画面から医師を追加してください。")
        return

    today = str(date.today())
    today_done = df[df["日付"] == today]["医師名"].tolist() if not df.empty else []

    member_options = [
        f"{m}　✅ 入力済み" if m in today_done else m
        for m in members
    ]

    # 前回選択した医師を記憶して、保存後も同じ医師を表示する
    last_doctor = st.session_state.get("last_selected_doctor", members[0])
    last_labels = [f"{last_doctor}　✅ 入力済み", last_doctor]
    default_index = next(
        (i for i, opt in enumerate(member_options) if opt in last_labels), 0
    )

    selected_label = st.selectbox(
        "👤 あなたの名前を選んでください",
        member_options,
        index=default_index,
        help="入力済みの医師は ✅ が表示されます。再入力すると上書きされます。"
    )
    doctor_name = selected_label.replace("　✅ 入力済み", "")
    st.session_state["last_selected_doctor"] = doctor_name

    existing = df[(df["日付"] == today) & (df["医師名"] == doctor_name)]

    # 当日データがなければ直近の過去データを引き継ぐ（翌日用）
    if existing.empty and not df.empty:
        past = df[df["医師名"] == doctor_name]
        if not past.empty:
            existing = past.sort_values("日付").iloc[[-1]]

    def get_val(col, default):
        # 当日データがある場合のみ参照（過去データは数値系のみ引き継ぐ）
        today_row = df[(df["日付"] == today) & (df["医師名"] == doctor_name)]
        if not today_row.empty and col in today_row.columns:
            v = today_row.iloc[0][col]
            return v if pd.notna(v) else default
        # 翌日引き継ぎ：受け持ち患者数・重症患者数のみ
        if col in ["受け持ち患者数", "重症患者数"] and not existing.empty and col in existing.columns:
            v = existing.iloc[0][col]
            return v if pd.notna(v) else default
        return default

    st.markdown(f"### {doctor_name} さんの入力フォーム")
    if doctor_name in today_done:
        st.warning("今日すでに入力済みです。内容を確認・修正して再保存できます。")
        if st.button("🗑️ 入力内容をクリア", type="secondary"):
            mask = (df["日付"] == today) & (df["医師名"] == doctor_name)
            df = df[~mask]
            st.session_state.df = df
            save_data(df)
            st.rerun()

    k = doctor_name  # ウィジェットkeyのプレフィックス（医師切替でリセット）

    with st.form("input_form"):

        st.markdown("**🛏️ 入院患者**")
        input_date = st.date_input("日付", value=date.today(), key=f"{k}_date")
        c1, c2 = st.columns(2)
        with c1:
            patients = st.number_input(
                "受け持ち患者数", min_value=0, max_value=100,
                value=int(get_val("受け持ち患者数", 0)), step=1, key=f"{k}_patients"
            )
            new_admission = st.number_input(
                "新規入院数（本日受けた数）", min_value=0, max_value=20,
                value=int(get_val("新規入院数", 0)), step=1, key=f"{k}_new_admission"
            )
        with c2:
            critical = st.number_input(
                "重症患者数", min_value=0, max_value=50,
                value=int(get_val("重症患者数", 0)), step=1, key=f"{k}_critical"
            )
            discharge = st.number_input(
                "退院予定数", min_value=0, max_value=30,
                value=int(get_val("退院予定数", 0)), step=1, key=f"{k}_discharge"
            )

        st.divider()

        st.markdown("**🏥 プラザ外来**")
        st.caption("担当する時間帯にチェック。その時間帯は新規入院を担当できません。")
        pc1, pc2 = st.columns(2)
        with pc1:
            plaza_am = st.checkbox("午前", value=bool(get_val("プラザ外来_午前", False)), key=f"{k}_plaza_am")
        with pc2:
            plaza_pm = st.checkbox("午後", value=bool(get_val("プラザ外来_午後", False)), key=f"{k}_plaza_pm")

        st.divider()

        st.markdown("**🏢 総合外来**")
        general_patients = st.number_input(
            "予定患者数", min_value=0, max_value=100,
            value=int(get_val("総合外来_患者数", 0)), step=1, key=f"{k}_general"
        )

        st.divider()

        st.markdown("**📋 その他**")
        post_oncall = st.checkbox("当直明け", value=bool(get_val("当直明け", False)), key=f"{k}_post_oncall")
        oncall_start = st.checkbox(
            "当直入り（今夜の当直あり）",
            value=bool(get_val("当直入り", False)),
            help="今夜の当直に備えて省エネモード。負荷スコアに+10点加算されます。",
            key=f"{k}_oncall_start"
        )
        _time_options = ["なし"] + [
            f"{h:02d}:{m:02d}" for h in range(7, 20) for m in (0, 30)
        ]
        _saved_meeting = str(get_val("会議時刻", "なし"))
        _meeting_index = _time_options.index(_saved_meeting) if _saved_meeting in _time_options else 0
        meeting_time = st.selectbox(
            "会議の開始時刻（なし＝会議なし）",
            _time_options,
            index=_meeting_index,
            help="スコアには影響しません。",
            key=f"{k}_meeting"
        )

        st.divider()

        subjective_margin = st.slider(
            "主観的余裕（1＝余裕なし 〜 5＝余裕あり）",
            min_value=1, max_value=5,
            value=int(get_val("主観的余裕", 3)),
            key=f"{k}_margin"
        )
        accept_options = ["可", "条件付き可", "不可"]
        current_accept = get_val("新規受入可否", "可")
        if current_accept not in accept_options:
            current_accept = "可"
        accept_new = st.radio(
            "新規入院の受入可否",
            accept_options,
            index=accept_options.index(current_accept),
            horizontal=True,
            key=f"{k}_accept"
        )
        memo = st.text_area(
            "メモ（任意）",
            value=str(get_val("メモ", "")),
            placeholder="例：午後から手術あり、重症患者対応中など",
            key=f"{k}_memo"
        )

        submitted = st.form_submit_button("💾 保存する", use_container_width=True, type="primary")

        if submitted:
            new_row = {
                "日付": str(input_date),
                "医師名": doctor_name,
                "受け持ち患者数": patients,
                "重症患者数": critical,
                "新規入院数": new_admission,
                "退院予定数": discharge,
                "プラザ外来_午前": plaza_am,
                "プラザ外来_午後": plaza_pm,
                "総合外来_患者数": general_patients,
                "当直明け": post_oncall,
                "当直入り": oncall_start,
                "会議時刻": meeting_time,
                "主観的余裕": subjective_margin,
                "新規受入可否": accept_new,
                "メモ": memo,
            }
            mask = (df["日付"] == str(input_date)) & (df["医師名"] == doctor_name)
            df = df[~mask]
            df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
            st.session_state.df = df
            save_data(df)

            score = calc_load_score(pd.Series(new_row))
            blocked = (["午前"] if plaza_am else []) + (["午後"] if plaza_pm else [])
            st.success(f"✅ {doctor_name} さんのデータを保存しました。")
            st.info(f"負荷スコア：**{score}点**　{get_load_color(score)} {get_load_label(score)}")
            if blocked:
                st.warning(f"⚠️ プラザ外来（{'・'.join(blocked)}）の時間帯は新規入院を担当できません。")
            if oncall_start:
                st.info("🌙 当直入りのため省エネモードです。新規入院の割り振りは他の医師を優先してください。")


# ===== 新規入院アサイン支援画面 =====

def show_assign_support(df: pd.DataFrame):
    st.header("🆕 新規入院アサイン支援")

    today = str(date.today())
    today_df = df[df["日付"] == today].copy() if not df.empty else pd.DataFrame(columns=COLUMNS)

    if today_df.empty:
        if df.empty:
            st.warning("データがありません。まず各医師が「日次入力」からデータを登録してください。")
            return
        latest_date = df["日付"].max()
        today_df = df[df["日付"] == latest_date].copy()
        st.caption(f"⚠️ 今日のデータがないため、{latest_date} のデータを使用しています。")

    today_df["負荷スコア"] = today_df.apply(calc_load_score, axis=1)
    today_df["負荷レベル"] = today_df["負荷スコア"].apply(get_load_label)
    today_df["状態"] = today_df["負荷スコア"].apply(get_load_color)

    st.subheader("新規入院の概要")
    col1, col2, col3 = st.columns(3)
    with col1:
        patient_severity = st.radio("患者の重症度", ["軽症", "中等症", "重症"], horizontal=True)
    with col2:
        admission_timing = st.radio("入院予定時間帯", ["午前", "午後", "未定"], horizontal=True)
    with col3:
        st.text_input("特記事項（任意）", placeholder="例：専門領域の指定あり")

    st.markdown("---")
    st.subheader("受入候補医師リスト（負荷スコア低い順）")

    w = st.session_state.get("weights", DEFAULT_WEIGHTS)
    acceptable = today_df[today_df["新規受入可否"].isin(["可", "条件付き可"])].copy()
    not_acceptable = today_df[today_df["新規受入可否"] == "不可"].copy()

    # 同一時間帯のプラザ外来医師は点数に関わらず絶対ブロック
    opposite = {"午前": "午後", "午後": "午前"}
    if admission_timing in ["午前", "午後"]:
        same_col = f"プラザ外来_{admission_timing}"
        opp_col = f"プラザ外来_{opposite[admission_timing]}"
        plaza_blocked = acceptable[acceptable[same_col].isin([True, "True"])].copy()
        acceptable = acceptable[~acceptable[same_col].isin([True, "True"])].copy()
        # 反対時間帯に外来がある医師：受入可能だが追加負担点を加算して表示
        has_opp_plaza = acceptable[opp_col].isin([True, "True"])
        acceptable["表示スコア"] = acceptable["負荷スコア"] + has_opp_plaza.astype(int) * w.get("プラザ外来_反対時間帯追加", 0)
    else:
        plaza_blocked = pd.DataFrame(columns=COLUMNS)
        acceptable["表示スコア"] = acceptable["負荷スコア"]

    if acceptable.empty:
        st.error("現在、新規入院を受け入れられる医師がいません。チームで対応を検討してください。")
    else:
        acceptable = acceptable.sort_values("表示スコア", ascending=True).reset_index(drop=True)
        for idx, row in acceptable.iterrows():
            score = row["表示スコア"]
            accept = row["新規受入可否"]

            # 反対時間帯外来フラグ
            has_opp = (admission_timing in ["午前", "午後"] and
                       row.get(f"プラザ外来_{opposite.get(admission_timing, '')}", False) in [True, "True"])

            reasons = []
            if score < LOW_THRESHOLD:
                reasons.append("負荷スコアが低い")
            if row["当直明け"] not in [True, "True"]:
                reasons.append("当直明けでない")
            if row["当直入り"] in [True, "True"]:
                reasons.append("⚠️ 今夜当直入り（省エネモード）")
            if str(row["会議時刻"]) not in ["なし", "", "False", "nan"]:
                reasons.append(f"⚠️ 本日会議あり（{row['会議時刻']}〜）")
            if int(row["重症患者数"]) == 0:
                reasons.append("重症患者なし")
            if int(row["主観的余裕"]) >= 4:
                reasons.append("本人も余裕あり")
            if accept == "可":
                reasons.append("受入に制限なし")
            elif accept == "条件付き可":
                reasons.append("条件付きで受入可能")
            if patient_severity == "重症" and int(row["重症患者数"]) > 2:
                reasons.append("⚠️ すでに重症患者多数")
            if has_opp:
                opp_label = opposite[admission_timing]
                reasons.append(f"⚠️ {opp_label}にプラザ外来あり（{admission_timing}は受入可）")

            reason_text = "、".join(reasons) if reasons else "（特記なし）"
            title = f"{row['状態']} **{row['医師名']}**　スコア {score}点（{get_load_label(int(score))}）　受入：{accept}"
            if has_opp:
                title += f"　⚠️ {opposite[admission_timing]}外来あり"

            with st.expander(title, expanded=(idx == 0)):
                col_a, col_b = st.columns(2)
                with col_a:
                    st.markdown(f"- 受け持ち患者数：{row['受け持ち患者数']} 名")
                    st.markdown(f"- 重症患者数：{row['重症患者数']} 名")
                    st.markdown(f"- 本日の新規入院：{row['新規入院数']} 名")
                    st.markdown(f"- 退院予定：{row['退院予定数']} 名")
                with col_b:
                    st.markdown(f"- プラザ外来：{plaza_label(row)}")
                    st.markdown(f"- 総合外来：{row['総合外来_患者数']} 人")
                    st.markdown(f"- 当直明け：{'あり ⚠️' if row['当直明け'] in [True, 'True'] else 'なし'}")
                    st.markdown(f"- 当直入り：{'あり 🌙' if row['当直入り'] in [True, 'True'] else 'なし'}")
                    _mt = str(row["会議時刻"])
                    st.markdown(f"- 会議：{_mt if _mt not in ['なし', '', 'False', 'nan'] else 'なし'}")
                    st.markdown(f"- 主観的余裕：{row['主観的余裕']} / 5")
                    if row["メモ"]:
                        st.markdown(f"- メモ：{row['メモ']}")
                if has_opp:
                    st.warning(f"⚠️ {opposite[admission_timing]}のプラザ外来あり。{admission_timing}の入院は受入可能ですが負担が増えます。")
                st.success(f"**推奨理由：** {reason_text}")

    if not plaza_blocked.empty:
        st.markdown("---")
        st.subheader(f"🚫 {admission_timing}はプラザ外来中のため対応不可（点数に関わらず）")
        for _, row in plaza_blocked.sort_values("負荷スコア").iterrows():
            st.markdown(
                f"- {get_load_color(row['負荷スコア'])} **{row['医師名']}**　"
                f"プラザ外来：{plaza_label(row)}"
            )

    if admission_timing == "未定":
        # 未定の場合：プラザ外来のある医師の制約を表示
        has_plaza = acceptable[
            acceptable["プラザ外来_午前"].isin([True, "True"]) |
            acceptable["プラザ外来_午後"].isin([True, "True"])
        ]
        if not has_plaza.empty:
            st.markdown("---")
            st.info("ℹ️ 入院時間帯を選択すると、プラザ外来中の医師が自動的に対応不可リストに移動します。")
            for _, row in has_plaza.iterrows():
                st.markdown(f"- **{row['医師名']}**：プラザ外来 {plaza_label(row)} の時間帯は対応不可")

    if not not_acceptable.empty:
        st.markdown("---")
        st.subheader("受入不可の医師（参考）")
        for _, row in not_acceptable.sort_values("負荷スコア", ascending=False).iterrows():
            memo_text = f"　メモ：{row['メモ']}" if row["メモ"] else ""
            st.markdown(
                f"- {get_load_color(row['負荷スコア'])} **{row['医師名']}**　"
                f"スコア {row['負荷スコア']}点{memo_text}"
            )


# ===== 設定画面（医師名簿管理 ＋ 点数の重み付け） =====

def show_settings(members: list[str]):
    st.header("⚙️ 設定")

    # ---------- 点数の重み付け ----------
    st.subheader("📐 負荷スコアの重み付け")
    st.caption("各項目がスコアに与える点数を変更できます。変更後は「保存する」を押してください。")

    w = st.session_state.get("weights", DEFAULT_WEIGHTS).copy()

    with st.form("weights_form"):
        st.markdown("**患者数系（1人あたりの点数）**")
        c1, c2 = st.columns(2)
        with c1:
            w["受け持ち患者数"] = st.number_input("受け持ち患者数", value=w["受け持ち患者数"], min_value=0, max_value=50, step=1)
            w["新規入院数"] = st.number_input("新規入院数", value=w["新規入院数"], min_value=0, max_value=50, step=1)
            w["総合外来_患者数"] = st.number_input("総合外来（患者数）", value=w["総合外来_患者数"], min_value=0, max_value=50, step=1)
        with c2:
            w["重症患者数"] = st.number_input("重症患者数", value=w["重症患者数"], min_value=0, max_value=50, step=1)
            w["退院予定数"] = st.number_input("退院予定数（マイナス点）", value=w["退院予定数"], min_value=-50, max_value=0, step=1)

        st.markdown("**外来・当直（固定加算点）**")
        c3, c4 = st.columns(2)
        with c3:
            w["プラザ外来_午前"] = st.number_input("プラザ外来・午前（日次スコア加算）", value=w["プラザ外来_午前"], min_value=0, max_value=100, step=1)
            w["当直明け"] = st.number_input("当直明け", value=w["当直明け"], min_value=0, max_value=100, step=1)
        with c4:
            w["プラザ外来_午後"] = st.number_input("プラザ外来・午後（日次スコア加算）", value=w["プラザ外来_午後"], min_value=0, max_value=100, step=1)
            w["当直入り"] = st.number_input("当直入り", value=w["当直入り"], min_value=0, max_value=100, step=1)

        st.markdown("**アサイン支援：反対時間帯外来の追加負担点**")
        st.caption("例：午後入院を検討中、対象医師が午前にプラザ外来ありの場合に加算される点数")
        w["プラザ外来_反対時間帯追加"] = st.number_input(
            "反対時間帯プラザ外来・追加負担点",
            value=w.get("プラザ外来_反対時間帯追加", 10), min_value=0, max_value=100, step=1
        )

        st.markdown("**主観的余裕（係数）**")
        st.caption("余裕1→係数×5点、余裕5→係数×1点 が加算されます")
        w["主観的余裕_係数"] = st.number_input("主観的余裕の係数", value=w["主観的余裕_係数"], min_value=0, max_value=20, step=1)

        if st.form_submit_button("💾 重み付けを保存する", type="primary"):
            save_weights(w)
            st.session_state.weights = w
            st.success("重み付けを保存しました。")

    st.markdown("---")

    # 現在の配点早見表
    with st.expander("📋 現在の配点早見表"):
        cw = st.session_state.get("weights", DEFAULT_WEIGHTS)
        rows = [
            ["受け持ち患者数", f"1人あたり +{cw['受け持ち患者数']}点"],
            ["重症患者数", f"1人あたり +{cw['重症患者数']}点"],
            ["新規入院数", f"1人あたり +{cw['新規入院数']}点"],
            ["退院予定数", f"1人あたり {cw['退院予定数']}点"],
            ["プラザ外来・午前", f"+{cw['プラザ外来_午前']}点"],
            ["プラザ外来・午後", f"+{cw['プラザ外来_午後']}点"],
            ["総合外来患者数", f"1人あたり +{cw['総合外来_患者数']}点"],
            ["当直明け", f"+{cw['当直明け']}点"],
            ["当直入り", f"+{cw['当直入り']}点"],
            ["主観的余裕 1（余裕なし）", f"+{5 * cw['主観的余裕_係数']}点"],
            ["主観的余裕 3（中程度）", f"+{3 * cw['主観的余裕_係数']}点"],
            ["主観的余裕 5（余裕あり）", f"+{1 * cw['主観的余裕_係数']}点"],
        ]
        st.table(pd.DataFrame(rows, columns=["項目", "点数"]))

    st.markdown("---")

    # ---------- 医師名簿管理 ----------
    st.subheader("👥 医師名簿管理")
    st.caption("診療科のメンバーを管理します。追加・削除した医師が日次入力の選択肢に反映されます。")

    col_list, col_edit = st.columns([1, 1])

    with col_list:
        st.markdown("**現在のメンバー**")
        if not members:
            st.write("（登録なし）")
        else:
            for i, name in enumerate(members, 1):
                st.markdown(f"{i}. {name}")

    with col_edit:
        with st.form("add_member_form"):
            new_name = st.text_input("追加する医師名", placeholder="例：佐々木")
            if st.form_submit_button("➕ 追加する"):
                new_name = new_name.strip()
                if not new_name:
                    st.error("名前を入力してください。")
                elif new_name in members:
                    st.warning(f"「{new_name}」はすでに登録されています。")
                else:
                    members.append(new_name)
                    save_members(members)
                    st.session_state.members = members
                    st.success(f"「{new_name}」を追加しました。")
                    st.rerun()

        st.markdown("---")

        if members:
            with st.form("remove_member_form"):
                remove_name = st.selectbox("削除する医師名", members)
                if st.form_submit_button("🗑️ 削除する"):
                    members.remove(remove_name)
                    save_members(members)
                    st.session_state.members = members
                    st.success(f"「{remove_name}」を削除しました。")
                    st.rerun()
        else:
            st.write("削除できるメンバーがいません。")

        st.markdown("---")

        if len(members) >= 2:
            with st.form("reorder_form"):
                move_name = st.selectbox("移動する医師名", members, key="move_select")
                move_dir = st.radio("移動方向", ["↑ 上に移動", "↓ 下に移動"], horizontal=True)
                if st.form_submit_button("移動する"):
                    idx = members.index(move_name)
                    if move_dir == "↑ 上に移動" and idx > 0:
                        members[idx], members[idx - 1] = members[idx - 1], members[idx]
                        save_members(members)
                        st.session_state.members = members
                        st.rerun()
                    elif move_dir == "↓ 下に移動" and idx < len(members) - 1:
                        members[idx], members[idx + 1] = members[idx + 1], members[idx]
                        save_members(members)
                        st.session_state.members = members
                        st.rerun()
                    else:
                        st.info("これ以上移動できません。")


if __name__ == "__main__":
    main()
