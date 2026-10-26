"""SQLite 資料庫：每次啟動刪除舊檔，從 data/*.csv 重建，並提供查詢 helper。

所有 SQL 都寫在程式裡、用 ? 參數化；模型只提供參數值，永遠不寫 SQL。
"""
import csv
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = BASE_DIR / "center.db"

# 台灣沒有日光節約時間，固定 UTC+8 即可，不需額外安裝 tzdata
TZ = timezone(timedelta(hours=8), "Asia/Taipei")

# CSV 檔 → 資料表欄位型別（INTEGER 欄位的空字串會存成 NULL）
CSV_TABLES = {
    "facility_info": ("facility_info.csv", {
        "topic": "TEXT PRIMARY KEY", "content": "TEXT NOT NULL"}),
    "services": ("services.csv", {
        "service_id": "TEXT PRIMARY KEY", "name": "TEXT NOT NULL", "name_en": "TEXT",
        "category": "TEXT NOT NULL", "price": "INTEGER", "unit": "TEXT",
        "peak_type": "TEXT", "identity": "TEXT", "available": "INTEGER NOT NULL", "note": "TEXT"}),
    "coaches": ("coaches.csv", {
        "coach_id": "TEXT PRIMARY KEY", "name": "TEXT NOT NULL", "specialty": "TEXT",
        "private_price": "INTEGER", "does_private": "INTEGER NOT NULL",
        "does_group": "INTEGER NOT NULL", "bio": "TEXT"}),
    "coach_availability": ("coach_availability.csv", {
        "coach_id": "TEXT NOT NULL", "weekday": "INTEGER NOT NULL",
        "start_time": "TEXT NOT NULL", "end_time": "TEXT NOT NULL"}),
    "group_classes": ("group_classes.csv", {
        "class_id": "TEXT PRIMARY KEY", "name": "TEXT NOT NULL", "name_en": "TEXT",
        "coach_id": "TEXT NOT NULL", "weekday": "INTEGER NOT NULL", "start_time": "TEXT NOT NULL",
        "duration_min": "INTEGER NOT NULL", "room": "TEXT", "min_students": "INTEGER NOT NULL",
        "max_students": "INTEGER NOT NULL", "price": "INTEGER", "available": "INTEGER NOT NULL"}),
    "aliases": ("aliases.csv", {
        "alias": "TEXT NOT NULL", "canonical": "TEXT NOT NULL"}),
}

RUNTIME_SCHEMA = """
CREATE TABLE group_enrollments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    class_id TEXT NOT NULL,
    session_date TEXT NOT NULL,
    member_name TEXT NOT NULL,
    phone TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('enrolled', 'waitlist', 'cancelled')),
    created_at TEXT NOT NULL
);
CREATE TABLE private_bookings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    coach_id TEXT NOT NULL,
    date TEXT NOT NULL,
    start_time TEXT NOT NULL,
    member_name TEXT NOT NULL,
    phone TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('booked', 'cancelled')),
    created_at TEXT NOT NULL
);
CREATE TABLE pending_actions (
    token TEXT PRIMARY KEY,
    action_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
"""

IDENTITY_VALUES = {"adult", "student", "half"}
# 關鍵字拆詞後要忽略的贅字
STOPWORDS = {"多少", "多少錢", "價格", "價錢", "費用", "收費", "請問", "一次", "租借", "租", "the", "price", "how", "much"}


def now():
    """台北現在時間（naive datetime）。測試時可 monkeypatch。"""
    return datetime.now(TZ).replace(tzinfo=None)


def today():
    return now().date()


def read_csv(filename):
    with open(DATA_DIR / filename, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _convert(value, col_type):
    value = value.strip()
    if col_type.startswith("INTEGER"):
        return int(value) if value else None
    return value


@contextmanager
def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def query(sql, params=()):
    with connect() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def query_one(sql, params=()):
    rows = query(sql, params)
    return rows[0] if rows else None


def execute(sql, params=()):
    """執行寫入，回傳 lastrowid。"""
    with connect() as conn:
        return conn.execute(sql, params).lastrowid


def upcoming_session_date(weekday, start_time, ref=None):
    """某星期幾、某時間的課，從 ref（預設現在）起算的下一堂日期（尚未開始的那一堂）。"""
    ref = ref or now()
    hh, mm = map(int, start_time.split(":"))
    for i in range(8):
        d = ref.date() + timedelta(days=i)
        if d.isoweekday() == weekday and datetime.combine(d, time(hh, mm)) > ref:
            return d
    raise ValueError(f"invalid weekday {weekday}")


def init_db():
    """刪除舊 DB，從 CSV 建立所有資料表，並載入示範報名資料。"""
    if DB_PATH.exists():
        DB_PATH.unlink()
    with connect() as conn:
        for table, (filename, columns) in CSV_TABLES.items():
            cols_sql = ", ".join(f"{c} {t}" for c, t in columns.items())
            conn.execute(f"CREATE TABLE {table} ({cols_sql})")
            placeholders = ", ".join("?" for _ in columns)
            rows = [[_convert(r[c], t) for c, t in columns.items()] for r in read_csv(filename)]
            conn.executemany(f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})", rows)
        conn.executescript(RUNTIME_SCHEMA)

        # 示範報名：week_offset=0 代表該課「下一堂」，1 代表再下一堂，避免日期過期
        classes = {r["class_id"]: r for r in conn.execute("SELECT * FROM group_classes")}
        created = now().isoformat(timespec="seconds")
        for r in read_csv("enrollments_seed.csv"):
            cls = classes[r["class_id"]]
            first = upcoming_session_date(cls["weekday"], cls["start_time"])
            session = first + timedelta(weeks=int(r["week_offset"]))
            conn.execute(
                "INSERT INTO group_enrollments (class_id, session_date, member_name, phone, status, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (r["class_id"], session.isoformat(), r["member_name"], r["phone"], r["status"], created))


# ---------- 寬鬆名稱比對 ----------

def expand_keyword(keyword):
    """把客人的關鍵字展開成比對詞：先套 aliases（長的先），再把剩下的字拆詞。

    回傳 (terms, identity_hint)；identity_hint 是從「學生」「敬老」等詞推出的身分。
    """
    kw = (keyword or "").strip().lower()
    if not kw:
        return [], None
    terms, identity = [kw], None
    rest = kw
    aliases = query("SELECT alias, canonical FROM aliases")
    for a in sorted(aliases, key=lambda x: len(x["alias"]), reverse=True):
        alias = a["alias"].lower()
        if alias in rest:
            if a["canonical"] in IDENTITY_VALUES:
                identity = identity or a["canonical"]
            else:
                terms.append(a["canonical"].lower())
            rest = rest.replace(alias, " ")
    for tok in re.split(r"[\s,，、/()（）]+", rest):
        if len(tok) >= 2 and tok not in STOPWORDS:
            terms.append(tok)
    return list(dict.fromkeys(terms)), identity


def best_matches(rows, keyword, fields=("name", "name_en")):
    """回傳 (最高分的 rows, identity_hint)；分數 = 有幾個比對詞出現在 name / name_en 中。"""
    terms, identity = expand_keyword(keyword)
    scored = []
    for row in rows:
        texts = [str(row.get(f) or "").lower() for f in fields]
        score = sum(1 for t in terms if any(t in text for text in texts))
        if score:
            scored.append((score, row))
    if not scored:
        return [], identity
    top = max(s for s, _ in scored)
    return [r for s, r in scored if s == top], identity
