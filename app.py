"""EasySplit — a small, local Splitwise-style expense sharing dashboard.
Run: streamlit run app.py
"""
from __future__ import annotations

"""Database and balance calculations for EasySplit.

Amounts are stored as integer paisa/cents to avoid floating-point rounding.
Only the Python standard library is required by this module.
"""

import sqlite3
from contextlib import contextmanager
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Iterable

DB_PATH = Path(__file__).resolve().parent / "easysplit.sqlite3"


def to_cents(amount: str | float | Decimal) -> int:
    value = Decimal(str(amount))
    if not value.is_finite() or value <= 0:
        raise ValueError("Amount must be greater than zero.")
    return int((value * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def money(cents: int) -> str:
    prefix = "−" if cents < 0 else ""
    return f"{prefix}Rs {abs(cents) / 100:,.2f}"


@contextmanager
def connection(path: Path | str | None = None):
    conn = sqlite3.connect(str(path or DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def initialize(path: Path | str | None = None, seed: bool = True) -> None:
    with connection(path) as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS groups (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          name TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS members (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
          name TEXT NOT NULL,
          UNIQUE(group_id, name)
        );
        CREATE TABLE IF NOT EXISTS expenses (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
          title TEXT NOT NULL,
          category TEXT NOT NULL,
          total_cents INTEGER NOT NULL CHECK(total_cents > 0),
          paid_by INTEGER NOT NULL REFERENCES members(id),
          expense_date TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS expense_shares (
          expense_id INTEGER NOT NULL REFERENCES expenses(id) ON DELETE CASCADE,
          member_id INTEGER NOT NULL REFERENCES members(id),
          amount_cents INTEGER NOT NULL CHECK(amount_cents >= 0),
          PRIMARY KEY(expense_id, member_id)
        );
        CREATE TABLE IF NOT EXISTS settlements (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
          from_member INTEGER NOT NULL REFERENCES members(id),
          to_member INTEGER NOT NULL REFERENCES members(id),
          amount_cents INTEGER NOT NULL CHECK(amount_cents > 0),
          settlement_date TEXT NOT NULL,
          note TEXT NOT NULL DEFAULT '',
          CHECK(from_member <> to_member)
        );
        """)
        if seed and conn.execute("SELECT COUNT(*) FROM groups").fetchone()[0] == 0:
            group_id = conn.execute("INSERT INTO groups(name) VALUES (?)", ("Weekend Trip",)).lastrowid
            ids = {}
            for name in ("You", "Ali", "Sara", "Hina"):
                ids[name] = conn.execute(
                    "INSERT INTO members(group_id,name) VALUES (?,?)", (group_id, name)
                ).lastrowid
            examples = [
                ("Hotel stay", "Stay", 16000, "You", ("You", "Ali", "Sara", "Hina"), 5),
                ("Fuel for the drive", "Travel", 4400, "Ali", ("You", "Ali", "Sara", "Hina"), 4),
                ("Dinner at the lake", "Food", 6800, "Sara", ("You", "Ali", "Sara", "Hina"), 3),
                ("Coffee stop", "Food", 1500, "Hina", ("You", "Hina"), 2),
                ("Entry tickets", "Activities", 8000, "You", ("You", "Ali"), 1),
                ("Lunch", "Food", 3200, "Ali", ("You", "Ali", "Sara", "Hina"), 0),
            ]
            for title, category, total, payer, shared, days_ago in examples:
                _insert_expense(conn, group_id, title, category, total * 100, ids[payer],
                                [ids[name] for name in shared],
                                (date.today() - timedelta(days=days_ago)).isoformat())


def groups(path=None) -> list[dict]:
    with connection(path) as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM groups ORDER BY id")]


def add_group(name: str, path=None) -> int:
    name = name.strip()
    if not name:
        raise ValueError("Enter a group name.")
    with connection(path) as conn:
        return conn.execute("INSERT INTO groups(name) VALUES (?)", (name,)).lastrowid


def members(group_id: int, path=None) -> list[dict]:
    with connection(path) as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM members WHERE group_id=? ORDER BY id", (group_id,)
        )]


def add_member(group_id: int, name: str, path=None) -> int:
    name = name.strip()
    if not name:
        raise ValueError("Enter a member name.")
    with connection(path) as conn:
        try:
            return conn.execute("INSERT INTO members(group_id,name) VALUES (?,?)",
                                (group_id, name)).lastrowid
        except sqlite3.IntegrityError as exc:
            raise ValueError("This member already exists in the group.") from exc


def _insert_expense(conn: sqlite3.Connection, group_id: int, title: str,
                    category: str, total_cents: int, paid_by: int,
                    participant_ids: Iterable[int], expense_date: str) -> int:
    participants = list(dict.fromkeys(participant_ids))
    if not participants or total_cents <= 0:
        raise ValueError("Provide a positive amount and at least one participant.")
    group_member_ids = {r[0] for r in conn.execute(
        "SELECT id FROM members WHERE group_id=?", (group_id,)
    )}
    if paid_by not in group_member_ids or not set(participants).issubset(group_member_ids):
        raise ValueError("Payer and participants must belong to the selected group.")
    expense_id = conn.execute("""
      INSERT INTO expenses(group_id,title,category,total_cents,paid_by,expense_date)
      VALUES (?,?,?,?,?,?)""", (group_id, title, category, total_cents, paid_by, expense_date)
    ).lastrowid
    base, extra = divmod(total_cents, len(participants))
    for index, member_id in enumerate(sorted(participants)):
        conn.execute("""
          INSERT INTO expense_shares(expense_id,member_id,amount_cents)
          VALUES (?,?,?)""", (expense_id, member_id, base + (index < extra)))
    return expense_id


def add_expense(group_id: int, title: str, category: str,
                total_cents: int, paid_by: int, participant_ids: list[int],
                expense_date: str, path=None) -> int:
    title = title.strip()
    if not title:
        raise ValueError("Enter a description for the expense.")
    with connection(path) as conn:
        return _insert_expense(conn, group_id, title, category, total_cents,
                               paid_by, participant_ids, expense_date)


def expenses(group_id: int, path=None) -> list[dict]:
    with connection(path) as conn:
        return [dict(r) for r in conn.execute("""
          SELECT e.*, m.name AS payer_name FROM expenses e
          JOIN members m ON e.paid_by=m.id
          WHERE e.group_id=? ORDER BY e.expense_date DESC,e.id DESC
        """, (group_id,))]


def expense_shares(expense_id: int, path=None) -> list[dict]:
    with connection(path) as conn:
        return [dict(r) for r in conn.execute("""
          SELECT m.name, s.amount_cents FROM expense_shares s
          JOIN members m ON m.id=s.member_id
          WHERE s.expense_id=? ORDER BY m.id""", (expense_id,))]


def add_settlement(group_id: int, from_member: int, to_member: int,
                   amount_cents: int, settlement_date: str,
                   note: str = "", path=None) -> int:
    if amount_cents <= 0 or from_member == to_member:
        raise ValueError("Select different members and enter a positive amount.")
    with connection(path) as conn:
        ids = {r[0] for r in conn.execute(
            "SELECT id FROM members WHERE group_id=?", (group_id,)
        )}
        if from_member not in ids or to_member not in ids:
            raise ValueError("Both people must belong to this group.")
        return conn.execute("""
          INSERT INTO settlements(group_id,from_member,to_member,amount_cents,settlement_date,note)
          VALUES (?,?,?,?,?,?)
        """, (group_id, from_member, to_member, amount_cents,
              settlement_date, note.strip())).lastrowid


def settlements(group_id: int, path=None) -> list[dict]:
    with connection(path) as conn:
        return [dict(r) for r in conn.execute("""
          SELECT s.*, a.name AS from_name, b.name AS to_name
          FROM settlements s
          JOIN members a ON s.from_member=a.id
          JOIN members b ON s.to_member=b.id
          WHERE s.group_id=? ORDER BY s.settlement_date DESC,s.id DESC
        """, (group_id,))]


def balances(group_id: int, path=None) -> list[dict]:
    people = members(group_id, path)
    net = {p["id"]: 0 for p in people}
    with connection(path) as conn:
        for r in conn.execute("""
            SELECT paid_by, total_cents FROM expenses WHERE group_id=?
        """, (group_id,)):
            net[r["paid_by"]] += r["total_cents"]
        for r in conn.execute("""
            SELECT s.member_id,s.amount_cents FROM expense_shares s
            JOIN expenses e ON e.id=s.expense_id WHERE e.group_id=?
        """, (group_id,)):
            net[r["member_id"]] -= r["amount_cents"]
        for r in conn.execute("""
            SELECT from_member,to_member,amount_cents FROM settlements
            WHERE group_id=?
        """, (group_id,)):
            net[r["from_member"]] += r["amount_cents"]
            net[r["to_member"]] -= r["amount_cents"]
    if sum(net.values()) != 0:
        raise AssertionError("Group balances must sum to zero.")
    return [{**person, "net_cents": net[person["id"]]} for person in people]


def suggested_transfers(people: list[dict]) -> list[dict]:
    """Greedy cash-flow matching, using integers to avoid rounding errors."""
    owed = [[p["id"], p["name"], p["net_cents"]]
            for p in people if p["net_cents"] > 0]
    owing = [[p["id"], p["name"], -p["net_cents"]]
             for p in people if p["net_cents"] < 0]
    owed.sort(key=lambda p: (-p[2], p[0]))
    owing.sort(key=lambda p: (-p[2], p[0]))
    transfers = []
    i = j = 0
    while i < len(owing) and j < len(owed):
        value = min(owing[i][2], owed[j][2])
        transfers.append({"from_id": owing[i][0], "from_name": owing[i][1],
                          "to_id": owed[j][0], "to_name": owed[j][1],
                          "amount_cents": value})
        owing[i][2] -= value
        owed[j][2] -= value
        if owing[i][2] == 0:
            i += 1
        if owed[j][2] == 0:
            j += 1
    return transfers


from collections import defaultdict
from datetime import date
from html import escape

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import sys
db = sys.modules[__name__]

st.set_page_config(
    page_title="EasySplit · Shared expenses",
    page_icon="🪙",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
    .stApp {background: #f7f9fc; color: #1c2a37;}
    section[data-testid="stSidebar"] {background: #fff; border-right: 1px solid #e6ecf2;}
    .block-container {padding-top: 1.35rem; max-width: 1440px;}
    h1, h2, h3 {letter-spacing: -0.035em;}
    h1 {font-weight: 780;}
    div[data-testid="stForm"] {background: white; border: 1px solid #e5edf2; border-radius: 15px; padding: 18px;}
    div[data-testid="stVerticalBlockBorderWrapper"] {border-radius: 15px;}
    .brand {display:flex;align-items:center;gap:10px;font-size:24px;font-weight:800;letter-spacing:-1px;margin-bottom:22px;color:#172e37}
    .brand-mark {width:34px;height:34px;border-radius:11px;background:#186b63;color:white;display:inline-flex;align-items:center;justify-content:center;font-size:21px}
    .eyebrow {text-transform:uppercase;letter-spacing:.13em;font-size:.72rem;font-weight:800;color:#748596;margin:0 0 5px}
    .subtitle {color:#738397;font-size:15px;margin-top:-.6rem;margin-bottom:1.6rem}
    .kpi {border: 1px solid #e4ebf0;background:white;border-radius:17px;padding:20px 22px;box-shadow:0 4px 18px #26344706;height:147px;}
    .kpi .kpi-top{display:flex;align-items:center;justify-content:space-between;color:#718194;font-size:13px;font-weight:650}
    .kpi .kpi-icon{font-size:17px;border-radius:9px;background:#f0f6f4;padding:6px}
    .kpi .kpi-value{color:#162d38;font-size:30px;font-weight:800;letter-spacing:-.045em;margin-top:20px}
    .kpi .kpi-note{font-size:12px;color:#8996a5;margin-top:2px}
    .positive{color:#09836a !important}.negative{color:#d16c44 !important}
    .section-title{font-weight:780;font-size:18px;color:#203541;letter-spacing:-.035em;margin-bottom:14px}
    .panel {border:1px solid #e4ebf0;border-radius:17px;padding:21px 23px;background:#fff;box-shadow:0 4px 18px #26344704;}
    .transfer{display:flex;justify-content:space-between;gap:14px;align-items:center;border-bottom:1px solid #eaf0f3;padding:13px 0}
    .transfer:last-child{border-bottom:none;padding-bottom:0}
    .transfer-person{color:#314453;font-size:14px}
    .transfer-amount{color:#0b7664;font-weight:800;white-space:nowrap}
    .muted{color:#7f8e9d;font-size:13px}
    .pill {display:inline-block;font-size:11px;font-weight:700;letter-spacing:.02em;background:#e9f4f1;color:#117464;border-radius:20px;padding:5px 9px;}
    .section-gap{height:14px}
</style>
""", unsafe_allow_html=True)


def kpi_card(title: str, value: str, note: str, emoji: str, cls: str = "") -> None:
    st.markdown(f"""
    <div class="kpi">
      <div class="kpi-top"><span>{escape(title)}</span><span class="kpi-icon">{emoji}</span></div>
      <div class="kpi-value {cls}">{escape(value)}</div>
      <div class="kpi-note">{escape(note)}</div>
    </div>""", unsafe_allow_html=True)


def title_block(page: str, group_name: str) -> None:
    st.markdown('<div class="eyebrow">SHARED EXPENSE MANAGER</div>', unsafe_allow_html=True)
    st.title(page)
    st.markdown(f'<div class="subtitle">{escape(group_name)} · Keep every shared payment clear and fair.</div>',
                unsafe_allow_html=True)


def group_spending_plot(records: list[dict]) -> go.Figure:
    total_by_category = defaultdict(int)
    for item in records:
        total_by_category[item["category"]] += item["total_cents"]
    colors = ["#217b72", "#50b79e", "#b7dfd3", "#7188bd", "#ecb46e", "#c69cd8"]
    fig = go.Figure(go.Pie(
        labels=list(total_by_category),
        values=[v / 100 for v in total_by_category.values()],
        hole=.71, sort=False, marker={"colors": colors[:len(total_by_category)]},
        textinfo="none", hovertemplate="%{label}: Rs %{value:,.2f}<extra></extra>"
    ))
    fig.update_layout(
        height=275, margin=dict(l=5,r=5,b=7,t=7), showlegend=True,
        legend=dict(orientation="h", y=-.07, x=.5, xanchor="center", font=dict(size=11, color="#596c7c")),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        annotations=[dict(text="SPENDING",x=.5,y=.55,showarrow=False,font=dict(size=11,color="#8b9aa7")),
                     dict(text=f"Rs {sum(total_by_category.values())/100:,.0f}",x=.5,y=.44,
                          showarrow=False,font=dict(size=19,color="#1b3340"))]
    )
    return fig


def group_balances_plot(people: list[dict]) -> go.Figure:
    fig = go.Figure(go.Bar(
        x=[p["net_cents"] / 100 for p in people], y=[p["name"] for p in people],
        orientation="h", marker_color=["#20967f" if p["net_cents"] >= 0 else "#efa47f" for p in people],
        text=[f"{'+' if p['net_cents'] > 0 else ''}{db.money(p['net_cents'])}" for p in people],
        textposition="outside", cliponaxis=False,
        hovertemplate="%{y}: Rs %{x:,.2f}<extra></extra>"
    ))
    fig.update_layout(
        height=275, margin=dict(l=5,r=90,b=5,t=15),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#566b79"), bargap=.46,
        xaxis=dict(showgrid=True, gridcolor="#edf1f5", zeroline=True, zerolinecolor="#becbd2",
                   showticklabels=False, title=""),
        yaxis=dict(autorange="reversed", showgrid=False, title="")
    )
    return fig


def recent_expenses_table(records: list[dict], limit: int | None = None) -> None:
    subset = records[:limit] if limit is not None else records
    if not subset:
        st.info("No expenses yet. Add your first shared expense to get started.")
        return
    df = pd.DataFrame([{
        "Date": item["expense_date"], "Expense": item["title"],
        "Category": item["category"], "Paid by": item["payer_name"],
        "Amount": db.money(item["total_cents"]),
    } for item in subset])
    st.dataframe(df, use_container_width=True, hide_index=True,
                 column_config={"Expense": st.column_config.TextColumn(width="large")})


def overview(group_name, people, records, transfers, viewer):
    title_block("Overview", group_name)
    total = sum(item["total_cents"] for item in records)
    viewer_id = viewer["id"] if viewer else None
    you_owe = sum(x["amount_cents"] for x in transfers if x["from_id"] == viewer_id)
    owed_to_you = sum(x["amount_cents"] for x in transfers if x["to_id"] == viewer_id)
    counts = len(records)
    columns = st.columns(4, gap="medium")
    with columns[0]:
        kpi_card("Total group expenses", db.money(total), f"{counts} shared expenses", "💳")
    with columns[1]:
        kpi_card("You are owed", db.money(owed_to_you), f"Payable to {viewer['name'] if viewer else 'you'}", "↗️", "positive")
    with columns[2]:
        kpi_card("You owe", db.money(you_owe), "Remaining amount to pay", "↙️", "negative")
    with columns[3]:
        kpi_card("People in this group", str(len(people)), "Sharing expenses together", "👥")
    st.markdown('<div class="section-gap"></div>', unsafe_allow_html=True)
    left, right = st.columns([1, 1], gap="medium")
    with left:
        with st.container(border=True):
            st.markdown('<div class="section-title">Spending by category</div>', unsafe_allow_html=True)
            if records:
                st.plotly_chart(group_spending_plot(records), use_container_width=True,
                                config={"displayModeBar":False})
            else:
                st.info("Add expenses to see the spending breakdown.")
    with right:
        with st.container(border=True):
            st.markdown('<div class="section-title">Who owes whom?</div>', unsafe_allow_html=True)
            if people and records:
                st.plotly_chart(group_balances_plot(people), use_container_width=True,
                                config={"displayModeBar":False})
            else:
                st.info("Group balances will appear here.")
    lower_left, lower_right = st.columns([1.35, 1], gap="medium")
    with lower_left:
        with st.container(border=True):
            st.markdown('<div class="section-title">Recent expenses</div>', unsafe_allow_html=True)
            recent_expenses_table(records, 6)
    with lower_right:
        with st.container(border=True):
            st.markdown('<div class="section-title">Suggested settlements</div>', unsafe_allow_html=True)
            if not transfers:
                st.success("All settled! Nobody owes anyone.")
            for tr in transfers[:6]:
                st.markdown(f"""
                <div class="transfer">
                  <div class="transfer-person"><strong>{escape(tr['from_name'])}</strong>
                    <span class="muted"> → </span><strong>{escape(tr['to_name'])}</strong></div>
                  <div class="transfer-amount">{escape(db.money(tr['amount_cents']))}</div>
                </div>""", unsafe_allow_html=True)
            st.caption("Suggestions summarize net group balances; they don't transfer money.")


def add_expense_view(group_id, group_name, people):
    title_block("Add an expense", group_name)
    if not people:
        st.warning("Add members to this group before recording expenses.")
        return
    lookup = {p["id"]: p["name"] for p in people}
    with st.form("add_expense", clear_on_submit=False):
        title = st.text_input("What was the expense?", placeholder="e.g. Dinner at the café")
        a,b = st.columns(2)
        with a:
            amount = st.number_input("Total amount (PKR)", min_value=0.01, value=1000.0, step=100.0, format="%.2f")
            paid_by = st.selectbox("Paid by", list(lookup), format_func=lambda x: lookup[x])
        with b:
            category = st.selectbox("Category", ["Food", "Travel", "Stay", "Groceries", "Activities", "Utilities", "Other"])
            when = st.date_input("Date of expense", value=date.today(), max_value=date.today())
        participants = st.multiselect("Split equally among", list(lookup), default=list(lookup),
                                      format_func=lambda x: lookup[x],
                                      help="The amount is divided equally among selected members, including or excluding the payer.")
        if participants:
            st.caption(f"Each person's approximate share: {db.money(db.to_cents(amount) // len(participants))}. Any remaining paisa is allocated automatically.")
        submit = st.form_submit_button("Save expense", type="primary", use_container_width=True)
        if submit:
            try:
                db.add_expense(group_id, title, category, db.to_cents(amount), paid_by, participants,
                               when.isoformat())
                st.success("Expense saved. Balances have been updated.")
            except ValueError as exc:
                st.error(str(exc))
    st.markdown("#### Recent expenses")
    recent_expenses_table(db.expenses(group_id), 5)


def settle_view(group_id, group_name, people, transfers):
    title_block("Settle up", group_name)
    st.markdown("#### Suggested payments")
    if transfers:
        st.dataframe(pd.DataFrame([{
            "From": tr["from_name"], "To": tr["to_name"],
            "Amount": db.money(tr["amount_cents"])
        } for tr in transfers]), hide_index=True, use_container_width=True)
    else:
        st.success("The group is fully settled.")
    st.caption("These recommendations minimize outstanding balances. Record a settlement after money is actually paid.")
    st.markdown("#### Record a payment")
    if len(people) < 2:
        st.info("At least two members are required to record a payment.")
        return
    lookup = {p["id"]: p["name"] for p in people}
    with st.form("settlement"):
        first, second = st.columns(2)
        with first:
            sender = st.selectbox("Who paid?", list(lookup), format_func=lambda x: lookup[x])
            amount = st.number_input("Amount paid (PKR)", min_value=0.01, value=500.0, step=100.0, format="%.2f")
        with second:
            receiver = st.selectbox("Who received?", list(lookup), index=1,
                                    format_func=lambda x: lookup[x])
            paid_on = st.date_input("Payment date", date.today(), max_value=date.today())
        note = st.text_input("Note (optional)", placeholder="e.g. Bank transfer")
        if st.form_submit_button("Record settlement", type="primary", use_container_width=True):
            try:
                db.add_settlement(group_id, sender, receiver, db.to_cents(amount), paid_on.isoformat(), note)
                st.success("Settlement recorded. Current balances reflect this payment.")
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
    records = db.settlements(group_id)
    if records:
        st.markdown("#### Payment history")
        st.dataframe(pd.DataFrame([{
            "Date": x["settlement_date"], "From": x["from_name"], "To": x["to_name"],
            "Amount": db.money(x["amount_cents"]), "Note": x["note"]
        } for x in records]), hide_index=True, use_container_width=True)


def activity_view(group_id, group_name, records):
    title_block("All expenses", group_name)
    recent_expenses_table(records)
    if records:
        st.markdown("#### Expense details")
        for expense in records:
            with st.expander(f"{expense['expense_date']}   ·   {expense['title']}   ·   {db.money(expense['total_cents'])}"):
                st.write(f"Paid by **{expense['payer_name']}** · Category: {expense['category']}")
                parts = db.expense_shares(expense["id"])
                st.dataframe(pd.DataFrame([{
                    "Member": part["name"], "Share": db.money(part["amount_cents"])
                } for part in parts]), hide_index=True, use_container_width=True)


def members_view(group_id, group_name, people):
    title_block("Group members", group_name)
    st.caption("Invite members by adding their names. This is a local demo, so no notifications are sent.")
    with st.form("add_member"):
        new_name = st.text_input("Member name", placeholder="e.g. Ahmed")
        if st.form_submit_button("Add member", type="primary"):
            try:
                db.add_member(group_id, new_name)
                st.success(f"Added {new_name.strip()} to the group.")
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
    if people:
        current = db.balances(group_id)
        st.dataframe(pd.DataFrame([{
            "Member": p["name"], "Status": "Gets back" if p["net_cents"] > 0 else "Owes" if p["net_cents"] < 0 else "Settled",
            "Net balance": db.money(p["net_cents"])
        } for p in current]), hide_index=True, use_container_width=True)


def main():
    db.initialize()
    st.sidebar.markdown('<div class="brand"><span class="brand-mark">↔</span> easy<span style="color:#177c6e">split</span></div>', unsafe_allow_html=True)
    all_groups = db.groups()
    if not all_groups:
        st.warning("No groups yet. Create one in the sidebar.")
        return
    group_lookup = {g["id"]: g["name"] for g in all_groups}
    chosen_id = st.sidebar.selectbox("CURRENT GROUP", list(group_lookup),
                                    format_func=lambda id: group_lookup[id])
    group_name = group_lookup[chosen_id]
    with st.sidebar.expander("＋ New group"):
        with st.form("new_group"):
            name = st.text_input("Group name", placeholder="e.g. Flatmates")
            if st.form_submit_button("Create group"):
                try:
                    new_id = db.add_group(name)
                    db.add_member(new_id, "You")
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
    st.sidebar.markdown("---")
    page = st.sidebar.radio("NAVIGATION", ["Overview", "Add Expense", "Settle Up", "All Expenses", "Members"],
                            label_visibility="collapsed")
    st.sidebar.markdown("---")
    people = db.balances(chosen_id)
    viewer = None
    if people:
        by_id = {p["id"]: p["name"] for p in people}
        you = next((p["id"] for p in people if p["name"].casefold() == "you"), people[0]["id"])
        selected = st.sidebar.selectbox("VIEW BALANCES AS", list(by_id),
                                       index=list(by_id).index(you),
                                       format_func=lambda id: by_id[id])
        viewer = next(p for p in people if p["id"] == selected)
    st.sidebar.caption("Local demo · Amounts in PKR · Stored in SQLite")
    records = db.expenses(chosen_id)
    transfers = db.suggested_transfers(people)
    if page == "Overview":
        overview(group_name, people, records, transfers, viewer)
    elif page == "Add Expense":
        add_expense_view(chosen_id, group_name, people)
    elif page == "Settle Up":
        settle_view(chosen_id, group_name, people, transfers)
    elif page == "All Expenses":
        activity_view(chosen_id, group_name, records)
    else:
        members_view(chosen_id, group_name, people)


if __name__ == "__main__":
    main()
