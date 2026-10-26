"""客服 Agent 的工具函式、tool schema 與白名單 registry。

工具原則：
1. 回傳 dict / list 資料，不回傳句子（句子由模型寫）
2. 查不到 → {"found": false, "reason": ...}；參數錯誤 → {"error": ...}
3. 金額、人數判斷都在程式算
4. 參數有型別與格式（日期 YYYY-MM-DD、時間 HH:MM），模型不寫 SQL
"""
import functools
import json
import math
import re
import secrets
from datetime import date as date_cls
from datetime import datetime, timedelta

import db

CENTER_PHONE = "(07) 625-0666"
CENTER_LINE = "岡山運動中心 LINE 官方帳號"
OPEN_HOUR, CLOSE_HOUR = 6, 22
SESSION_MIN = 60  # 私人教練課每堂 60 分鐘
PACK_SIZE = 10
TOKEN_TTL_MIN = 10
PHONE_RE = re.compile(r"09\d{8}")
WEEKDAY_ZH = {1: "週一", 2: "週二", 3: "週三", 4: "週四", 5: "週五", 6: "週六", 7: "週日"}
IDENTITY_ZH = {"adult": "全票", "student": "學生票", "half": "半票"}
# 查不到時提示模型：這代表「資料裡沒有」，不代表「本館沒有」
# 預約成功後提示模型：只報編號與上課資訊；實測模型常自行加上「憑編號報到」等資料中沒有的流程
BOOKED_STEP = ("tell the customer the booking_id and the session details only. Do not mention check-in, ID, "
               "arrival or any other procedure at all (neither that one exists nor that none exists): it is not "
               "in our data. Describe class status in plain words, never as field names like is_confirmed")
NOT_IN_DATA_STEP = ("not in our data: tell the customer you don't have this information "
                    "(do not say the center doesn't offer it) and call transfer_to_human")


class ToolError(Exception):
    """參數或條件不符；由 @tool 轉成 {"error": ...} 回給模型。"""


def tool(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ToolError as e:
            return {"error": str(e)}
    return wrapper


# ---------- 共用 helper ----------

# 遮罩字元不用 *：聊天框以 Markdown 顯示，* 會被當成斜體符號吃掉（「曾*琪」會變成「曾琪」）
def mask_phone(phone):
    return phone[:4] + "xxx" + phone[-3:]


def mask_name(name):
    if len(name) <= 1:
        return name
    if len(name) == 2:
        return name[0] + "○"
    return name[0] + "○" * (len(name) - 2) + name[-1]


def parse_date(value, field="date"):
    try:
        return datetime.strptime(str(value).strip(), "%Y-%m-%d").date()
    except ValueError:
        raise ToolError(f"{field} must be YYYY-MM-DD, got '{value}'")


def parse_hhmm(value, field="start_time"):
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", str(value).strip())
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise ToolError(f"{field} must be HH:MM (24-hour), got '{value}'")
    return int(m.group(1)) * 60 + int(m.group(2))


def fmt_min(minutes):
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def clean_phone(phone):
    p = re.sub(r"[\s\-()]", "", str(phone or ""))
    if not PHONE_RE.fullmatch(p):
        raise ToolError("phone must be a Taiwan mobile number like 0912345678 (10 digits starting with 09)")
    return p


def clean_name(name):
    n = str(name or "").strip()
    if not n:
        raise ToolError("member_name is required")
    return n


def family_of(service_name):
    """「羽球場 尖峰」→「羽球場」；同一家族的票種/尖離峰視為同一項服務。"""
    return service_name.split(" ")[0]


def overlaps(s1, e1, s2, e2):
    return s1 < e2 and s2 < e1


# ---------- 1. 查資訊 ----------

@tool
def get_center_info(topic):
    row = db.query_one("SELECT topic, content FROM facility_info WHERE topic = ?", (topic,))
    if not row:
        topics = [r["topic"] for r in db.query("SELECT topic FROM facility_info")]
        return {"found": False, "reason": f"no topic '{topic}'", "available_topics": topics}
    return {"found": True, "topic": row["topic"], "content": row["content"],
            "is_demo_data": row["content"].startswith("（示範資料）")}


# ---------- 2. 查服務與價格 ----------

def _service_record(r):
    if not r["available"]:
        return {"service_id": r["service_id"], "name": r["name"],
                "category": r["category"], "available": False, "note": r["note"]}
    rec = {"service_id": r["service_id"], "name": r["name"],
           "category": r["category"], "price": r["price"], "unit": r["unit"],
           "peak_type": r["peak_type"], "identity": r["identity"], "available": True}
    if r["note"]:
        rec["note"] = r["note"]
    return rec


def _class_record(c):
    rec = {"class_id": c["class_id"], "name": c["name"], "category": "group_class",
           "weekday": WEEKDAY_ZH[c["weekday"]], "start_time": c["start_time"],
           "coach": c["coach_name"], "available": bool(c["available"])}
    if c["available"]:
        rec.update(price=c["price"], unit="堂", min_students=c["min_students"], max_students=c["max_students"])
    else:
        rec["note"] = "本期暫停開課"
    return rec


def _all_classes():
    return db.query("SELECT g.*, c.name AS coach_name FROM group_classes g "
                    "JOIN coaches c ON c.coach_id = g.coach_id ORDER BY g.class_id")


@tool
def search_services(keyword, category=None):
    sql, params = "SELECT * FROM services", ()
    if category:
        sql, params = sql + " WHERE category = ?", (category,)
    services = db.query(sql, params)
    matches, _ = db.best_matches(services, keyword)
    results = [_service_record(r) for r in matches]
    if category in (None, "group_class"):
        class_matches, _ = db.best_matches(_all_classes(), keyword)
        results += [_class_record(c) for c in class_matches]
    if not results:
        cats = [r["category"] for r in db.query("SELECT DISTINCT category FROM services")]
        return {"found": False, "reason": f"no service matches '{keyword}'", "available_categories": cats,
                "next_step": NOT_IN_DATA_STEP}
    return {"found": True, "count": len(results), "results": results}


# ---------- 3. 團體課 ----------

def _class_counts(class_id, session_date):
    rows = db.query("SELECT status, COUNT(*) AS n FROM group_enrollments "
                    "WHERE class_id = ? AND session_date = ? GROUP BY status", (class_id, session_date))
    counts = {r["status"]: r["n"] for r in rows}
    return counts.get("enrolled", 0), counts.get("waitlist", 0)


def _session_info(cls, session_date):
    d = session_date.isoformat()
    enrolled, waitlist = _class_counts(cls["class_id"], d)
    mn, mx = cls["min_students"], cls["max_students"]
    start = parse_hhmm(cls["start_time"])
    if enrolled >= mx:
        status = "full_waitlist_open"
    elif enrolled >= mn:
        status = "confirmed_spots_left"
    else:
        status = "not_enough_students_yet"
    info = {
        "class_id": cls["class_id"], "name": cls["name"],
        "date": d, "weekday": WEEKDAY_ZH[cls["weekday"]],
        "start_time": cls["start_time"], "end_time": fmt_min(start + cls["duration_min"]),
        "coach": cls["coach_name"], "room": cls["room"], "price": cls["price"],
        "enrolled": enrolled, "min_students": mn, "max_students": mx,
        "spots_left": max(0, mx - enrolled), "waitlist": waitlist,
        "is_confirmed": enrolled >= mn, "students_needed_to_run": max(0, mn - enrolled),
        "status": status,
    }
    if enrolled < mn:
        deadline = datetime.combine(session_date, datetime.min.time()) + timedelta(minutes=start) - timedelta(hours=24)
        info["cancelled_if_below_min_by"] = deadline.strftime("%Y-%m-%d %H:%M")
    return info


def _session_started(cls, session_date):
    start = datetime.combine(session_date, datetime.min.time()) + timedelta(minutes=parse_hhmm(cls["start_time"]))
    return start <= db.now()


@tool
def list_group_classes(keyword=None, date=None, weekday=None):
    classes = _all_classes()
    if keyword:
        classes, _ = db.best_matches(classes, keyword)
        if not classes:
            names = [c["name"] for c in _all_classes() if c["available"]]
            return {"found": False, "reason": f"no group class matches '{keyword}'", "available_classes": names}

    target = None
    if date:
        target = parse_date(date)
        if target < db.today():
            raise ToolError(f"date {target} is in the past; today is {db.today()}")
        weekday = target.isoweekday()
    if weekday is not None and not 1 <= int(weekday) <= 7:
        raise ToolError("weekday must be 1 (Mon) to 7 (Sun)")

    sessions, paused = [], []
    for c in classes:
        if weekday is not None and c["weekday"] != int(weekday):
            continue
        if not c["available"]:
            paused.append(_class_record(c))
            continue
        d = target or db.upcoming_session_date(c["weekday"], c["start_time"])
        if _session_started(c, d):
            continue
        sessions.append(_session_info(c, d))

    if not sessions and not paused:
        schedule = [{"class_id": c["class_id"], "name": c["name"], "weekday": WEEKDAY_ZH[c["weekday"]],
                     "start_time": c["start_time"]} for c in classes if c["available"]]
        return {"found": False, "reason": "no upcoming group class matches the given day/date",
                "weekly_schedule": schedule}
    sessions.sort(key=lambda s: (s["date"], s["start_time"]))
    result = {"found": True, "today": db.today().isoformat(), "sessions": sessions}
    if paused:
        result["paused_classes"] = paused
    return result


# ---------- 4. 私人教練空檔 ----------

def _match_coaches(coaches, keyword):
    terms, _ = db.expand_keyword(keyword)
    kw = keyword.strip().lower()
    out = []
    for c in coaches:
        fields = [c["coach_id"].lower(), c["name"].lower(), (c["specialty"] or "").lower()]
        if any(t in f or (len(f) >= 2 and f in t) for t in terms + [kw] for f in fields):
            out.append(c)
    return out


def _coach_free_slots(coach, d):
    """教練某天可預約的整點 60 分鐘時段（扣掉已預約的私教與該教練的團課）。"""
    windows = db.query("SELECT start_time, end_time FROM coach_availability WHERE coach_id = ? AND weekday = ?",
                       (coach["coach_id"], d.isoweekday()))
    booked = {r["start_time"] for r in db.query(
        "SELECT start_time FROM private_bookings WHERE coach_id = ? AND date = ? AND status = 'booked'",
        (coach["coach_id"], d.isoformat()))}
    group = [(parse_hhmm(g["start_time"]), parse_hhmm(g["start_time"]) + g["duration_min"]) for g in db.query(
        "SELECT start_time, duration_min FROM group_classes WHERE coach_id = ? AND weekday = ? AND available = 1",
        (coach["coach_id"], d.isoweekday()))]
    slots = []
    for w in windows:
        ws, we = parse_hhmm(w["start_time"]), parse_hhmm(w["end_time"])
        start = math.ceil(ws / 60) * 60
        while start + SESSION_MIN <= we:
            t = fmt_min(start)
            if t not in booked and not any(overlaps(start, start + SESSION_MIN, gs, ge) for gs, ge in group):
                slots.append(t)
            start += 60
    return slots


def _private_coaches():
    return db.query("SELECT * FROM coaches WHERE does_private = 1 ORDER BY coach_id")


@tool
def find_private_slots(date, coach_keyword=None, specialty=None):
    d = parse_date(date)
    earliest = db.today() + timedelta(days=1)
    if d < earliest:
        return {"found": False, "reason": "private sessions must be booked at least 1 day in advance",
                "earliest_bookable_date": earliest.isoformat()}
    coaches = _private_coaches()
    for kw in (coach_keyword, specialty):
        if kw:
            coaches = _match_coaches(coaches, kw)
    if not coaches:
        return {"found": False, "reason": "no private coach matches the request",
                "private_coaches": [{"coach_id": c["coach_id"], "name": c["name"], "specialty": c["specialty"]}
                                    for c in _private_coaches()]}
    results = []
    for c in coaches:
        slots = _coach_free_slots(c, d)
        if slots:
            results.append({"coach_id": c["coach_id"], "name": c["name"], "specialty": c["specialty"],
                            "price": c["private_price"], "duration_min": SESSION_MIN, "slots": slots})
    if not results:
        weekly = []
        for c in coaches:
            days = sorted({r["weekday"] for r in db.query(
                "SELECT weekday FROM coach_availability WHERE coach_id = ?", (c["coach_id"],))})
            weekly.append({"coach_id": c["coach_id"], "name": c["name"],
                           "available_weekdays": [WEEKDAY_ZH[x] for x in days]})
        return {"found": False, "reason": f"no free private slot on {d} ({WEEKDAY_ZH[d.isoweekday()]})",
                "coach_weekly_availability": weekly}
    return {"found": True, "date": d.isoformat(), "weekday": WEEKDAY_ZH[d.isoweekday()], "coaches": results}


# ---------- 5. 費用計算 ----------

def _cost_court(family, rows, courts, date, start_time, hours):
    if not date or not start_time:
        raise ToolError("court rental needs date (YYYY-MM-DD) and start_time (HH:MM) to decide peak/off-peak")
    d = parse_date(date)
    start = parse_hhmm(start_time)
    hours = int(hours or 1)
    if start % 60:
        raise ToolError("court rental starts on the hour, e.g. 16:00")
    if hours < 1:
        raise ToolError("hours must be at least 1")
    sh, eh = start // 60, start // 60 + hours
    if sh < OPEN_HOUR or eh > CLOSE_HOUR:
        raise ToolError(f"rental must be within opening hours {OPEN_HOUR:02d}:00-{CLOSE_HOUR:02d}:00")
    prices = {r["peak_type"]: r["price"] for r in rows}
    holiday = d.isoweekday() >= 6
    peak_from = 10 if holiday else 17

    # 逐小時判斷尖離峰，相同費率的連續時段合併成一列
    items = []
    for h in range(sh, eh):
        peak = h >= peak_from
        if items and items[-1]["_peak"] == peak:
            seg = items[-1]
            seg["_end"] = h + 1
        else:
            seg = {"_peak": peak, "_start": h, "_end": h + 1}
            items.append(seg)
    out = []
    for seg in items:
        seg_hours = seg["_end"] - seg["_start"]
        unit_price = prices["peak" if seg["_peak"] else "offpeak"]
        out.append({
            "desc": f"{family} {'假日' if holiday else '平日'} {seg['_start']:02d}:00-{seg['_end']:02d}:00 "
                    f"{'尖峰' if seg['_peak'] else '離峰'}",
            "unit_price": unit_price, "unit": "面/時", "hours": seg_hours, "courts": courts,
            "qty": seg_hours * courts, "subtotal": unit_price * seg_hours * courts})
    return {"found": True, "service": family, "date": d.isoformat(),
            "day_type": "holiday" if holiday else "weekday",
            "items": out, "total": sum(i["subtotal"] for i in out), "currency": "TWD"}


def _cost_sessions(label, single_price, pack_price, quantity, picked_pack):
    """課程：比較「N 堂單買」與「10 堂方案（可混搭單堂）」，回傳最省組合。"""
    sessions = quantity * PACK_SIZE if picked_pack else quantity
    best = None
    for packs in range(0, math.ceil(sessions / PACK_SIZE) + 1):
        singles = max(0, sessions - packs * PACK_SIZE)
        cost = packs * pack_price + singles * single_price
        if best is None or cost < best[0]:
            best = (cost, packs, singles)
    cost, packs, singles = best
    items = []
    if packs:
        items.append({"desc": f"{label} {PACK_SIZE}堂方案", "unit_price": pack_price, "qty": packs,
                      "subtotal": packs * pack_price})
    if singles:
        items.append({"desc": f"{label} 單堂", "unit_price": single_price, "qty": singles,
                      "subtotal": singles * single_price})
    all_single = sessions * single_price
    result = {"found": True, "service": label, "sessions": sessions, "items": items, "total": cost,
              "currency": "TWD",
              "comparison": {"all_single_sessions": all_single, "cheapest_combination": cost,
                             "saving": all_single - cost},
              "is_demo_data": True}
    unused = packs * PACK_SIZE + singles - sessions
    if unused:
        result["unused_sessions_in_pack"] = unused
    return result


def _cost_tickets(family_rows, matches, identity, quantity, hours):
    notes = []
    if any(r["identity"] != "any" for r in matches):
        # 有分身分的單次票：依身分挑票價
        ident = identity or "adult"
        options = {r["identity"]: r for r in family_rows if r["identity"] != "any" and r["available"]}
        if ident not in options:
            return {"error": f"identity '{ident}' ticket is not offered for {family_of(matches[0]['name'])}",
                    "available_identities": sorted(options)}
        row = options[ident]
    else:
        if len(matches) > 1:
            return {"error": "keyword matches several items, please be more specific",
                    "options": [m["name"] for m in matches]}
        row = matches[0]
        if identity:
            notes.append(f"{row['name']} has one price for all identities")
    if not row["available"]:
        return {"error": f"{row['name']} is not available", "available": False, "note": row["note"]}
    qty = quantity
    desc = row["name"]
    if row["unit"] == "小時":
        per_person = int(hours or 1)
        qty = quantity * per_person
        desc += f" {per_person}小時 x {quantity}人"
    item = {"desc": desc, "unit_price": row["price"], "unit": row["unit"], "qty": qty,
            "subtotal": row["price"] * qty}
    result = {"found": True, "service": row["name"], "items": [item], "total": item["subtotal"], "currency": "TWD"}
    if row["note"]:
        notes.append(row["note"])
    if notes:
        result["notes"] = notes
    return result


@tool
def calculate_cost(service_keyword, quantity=1, identity=None, date=None, start_time=None, hours=None):
    quantity = int(quantity)
    if quantity < 1:
        raise ToolError("quantity must be at least 1")
    services = db.query("SELECT * FROM services")
    matches, hint = db.best_matches(services, service_keyword)
    identity = identity or hint

    if not matches:
        # 直接說課名（如「空中瑜珈」）→ 用團課單堂價 + 10 堂方案計算
        classes, _ = db.best_matches([c for c in _all_classes() if c["available"]], service_keyword)
        if len(classes) == 1:
            pack = db.query_one("SELECT price FROM services WHERE service_id = 'S20'")
            return _cost_sessions(classes[0]["name"], classes[0]["price"], pack["price"], quantity, False)
        return {"found": False, "reason": f"no priced service matches '{service_keyword}'",
                "next_step": NOT_IN_DATA_STEP}

    families = sorted({family_of(r["name"]) for r in matches})
    if len(families) > 1:
        return {"error": "keyword matches several services, please be more specific", "options": families}
    family = families[0]
    family_rows = [r for r in services if family_of(r["name"]) == family]
    if not any(r["available"] for r in family_rows):
        return {"error": f"{family} is not available", "available": False, "note": family_rows[0]["note"]}

    category = family_rows[0]["category"]
    if category == "court":
        return _cost_court(family, family_rows, quantity, date, start_time, hours)
    if category in ("private_coach", "group_class"):
        single = next(r for r in family_rows if "單堂" in r["name"])
        pack = next(r for r in family_rows if "方案" in r["name"])
        picked_pack = all("方案" in r["name"] for r in matches)
        return _cost_sessions(family, single["price"], pack["price"], quantity, picked_pack)
    return _cost_tickets(family_rows, matches, identity, quantity, hours)


# ---------- 6. 方案比較 ----------

@tool
def compare_plans(category, visits_per_month, hours_per_visit=1, identity="adult"):
    visits = int(visits_per_month)
    hours = max(1, math.ceil(float(hours_per_visit or 1)))
    if visits < 1:
        raise ToolError("visits_per_month must be at least 1")
    rows = db.query("SELECT * FROM services WHERE category = ? AND available = 1", (category,))
    singles = {r["identity"]: r for r in rows if r["identity"] != "any"}
    identity = identity or "adult"
    if identity not in singles:
        return {"error": f"no {identity} single ticket for {category}", "available_identities": sorted(singles)}
    single = singles[identity]
    monthly = next(r for r in rows if "月卡" in r["name"])
    book = next(r for r in rows if "回數票" in r["name"])

    per_visit_single = single["price"] * (hours if single["unit"] == "小時" else 1)
    ticket_price = book["price"] / 30
    days_to_use_book = math.ceil(30 / visits * 30)
    options = [
        {"plan": "single_entry", "name": single["name"], "monthly_cost": per_visit_single * visits,
         "calc": f"{per_visit_single} x {visits} visits"},
        {"plan": "monthly_pass", "name": monthly["name"], "monthly_cost": monthly["price"],
         "calc": f"{monthly['price']} per 30 days, unlimited"},
        {"plan": "ticket_book", "name": book["name"], "monthly_cost": round(ticket_price * visits),
         "calc": f"{book['price']}/30 tickets = {ticket_price:g} per ticket x {visits} visits",
         "days_to_use_30_tickets": days_to_use_book,
         "exceeds_90_day_validity": days_to_use_book > 90},
    ]
    cheapest = min(options, key=lambda o: o["monthly_cost"])
    single_cost = options[0]["monthly_cost"]
    for o in options:
        o["saving_vs_single"] = single_cost - o["monthly_cost"]
    return {"found": True, "category": category, "identity": identity, "visits_per_month": visits,
            "hours_per_visit": hours, "options": options, "cheapest_plan": cheapest["plan"],
            "cheapest_monthly_cost": cheapest["monthly_cost"], "currency": "TWD",
            "assumptions": ["1 month = 30 days", "1 ticket = 1 visit",
                            "gym single entry is charged per hour" if category == "gym" else "pool single entry is per visit"]}


# ---------- 預約兩段式確認 ----------

def _create_token(action_type, payload):
    token = secrets.token_hex(3)
    expires = db.now() + timedelta(minutes=TOKEN_TTL_MIN)
    db.execute("INSERT INTO pending_actions (token, action_type, payload_json, expires_at) VALUES (?, ?, ?, ?)",
               (token, action_type, json.dumps(payload, sort_keys=True, ensure_ascii=False),
                expires.isoformat(timespec="seconds")))
    return token


def _check_token(token, action_type, payload):
    """token 必須存在、未過期、動作相同，且 payload 與預覽時完全一致。"""
    row = db.query_one("SELECT * FROM pending_actions WHERE token = ?", (str(token).strip(),))
    if not row or row["action_type"] != action_type:
        raise ToolError("invalid confirm_token; call again without confirm_token to get a new preview")
    if datetime.fromisoformat(row["expires_at"]) < db.now():
        db.execute("DELETE FROM pending_actions WHERE token = ?", (row["token"],))
        raise ToolError("confirm_token expired; call again without confirm_token to get a new preview")
    if json.loads(row["payload_json"]) != payload:
        raise ToolError("booking details differ from the preview; confirm_token rejected")
    db.execute("DELETE FROM pending_actions WHERE token = ?", (row["token"],))


def _get_class(class_id):
    key = str(class_id).strip()
    classes = _all_classes()
    exact = [c for c in classes if c["class_id"].lower() == key.lower()]
    found = exact or db.best_matches(classes, key)[0]
    if not found:
        raise ToolError(f"no group class '{class_id}'; use list_group_classes to find class_id")
    if len(found) > 1:
        raise ToolError(f"'{class_id}' matches several classes: " + ", ".join(f"{c['class_id']} {c['name']}" for c in found))
    return found[0]


def _get_coach(coach_id):
    key = str(coach_id).strip()
    coaches = db.query("SELECT * FROM coaches")
    exact = [c for c in coaches if c["coach_id"].lower() == key.lower() or c["name"] == key]
    found = exact or _match_coaches(coaches, key)
    if not found:
        raise ToolError(f"no coach '{coach_id}'; use find_private_slots to find coach_id")
    if len(found) > 1:
        raise ToolError(f"'{coach_id}' matches several coaches: " + ", ".join(f"{c['coach_id']} {c['name']}" for c in found))
    return found[0]


@tool
def book_group_class(class_id, session_date, member_name, phone, confirm_token=None):
    cls = _get_class(class_id)
    if not cls["available"]:
        raise ToolError(f"{cls['name']} is paused this term and cannot be booked")
    d = parse_date(session_date, "session_date")
    if d.isoweekday() != cls["weekday"]:
        raise ToolError(f"{cls['name']} is held on {WEEKDAY_ZH[cls['weekday']]}, but {d} is {WEEKDAY_ZH[d.isoweekday()]}")
    if _session_started(cls, d):
        raise ToolError(f"the {d} {cls['start_time']} session has already started or passed")
    name, phone = clean_name(member_name), clean_phone(phone)
    dup = db.query_one("SELECT id FROM group_enrollments WHERE class_id = ? AND session_date = ? AND phone = ? "
                       "AND status IN ('enrolled', 'waitlist')", (cls["class_id"], d.isoformat(), phone))
    if dup:
        raise ToolError("this phone is already enrolled or on the waitlist for this session")

    enrolled, waitlist = _class_counts(cls["class_id"], d.isoformat())
    will_be = "enrolled" if enrolled < cls["max_students"] else "waitlist"
    payload = {"class_id": cls["class_id"], "session_date": d.isoformat(), "member_name": name, "phone": phone}

    if not confirm_token:
        summary = {
            "class_id": cls["class_id"], "class": cls["name"],
            "date": d.isoformat(), "weekday": WEEKDAY_ZH[d.isoweekday()], "start_time": cls["start_time"],
            "coach": cls["coach_name"], "room": cls["room"], "price": cls["price"],
            "member_name": name, "phone": mask_phone(phone),
            "will_be": will_be, "enrolled_now": enrolled,
            "min_students": cls["min_students"], "max_students": cls["max_students"]}
        if will_be == "waitlist":
            summary["waitlist_position"] = waitlist + 1
        return {"status": "preview", "summary": summary,
                "confirm_token": _create_token("book_group_class", payload), "expires_in_min": TOKEN_TTL_MIN,
                "next_step": "read the summary to the customer; call again with confirm_token only after they confirm"}

    _check_token(confirm_token, "book_group_class", payload)
    booking_id = db.execute(
        "INSERT INTO group_enrollments (class_id, session_date, member_name, phone, status, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (cls["class_id"], d.isoformat(), name, phone, will_be, db.now().isoformat(timespec="seconds")))
    result = {"status": "booked", "booking_type": "group", "booking_id": booking_id,
              "enrollment_status": will_be, "member_name": name, "phone": mask_phone(phone),
              "session": _session_info(cls, d), "next_step": BOOKED_STEP}
    if will_be == "waitlist":
        result["waitlist_position"] = _class_counts(cls["class_id"], d.isoformat())[1]
    return result


@tool
def book_private_session(coach_id, date, start_time, member_name, phone, confirm_token=None):
    coach = _get_coach(coach_id)
    if not coach["does_private"]:
        raise ToolError(f"{coach['name']} does not offer private sessions")
    d = parse_date(date)
    earliest = db.today() + timedelta(days=1)
    if d < earliest:
        raise ToolError(f"private sessions must be booked at least 1 day in advance; earliest date is {earliest}")
    start = parse_hhmm(start_time)
    if start % 60:
        raise ToolError("private sessions start on the hour, e.g. 14:00")
    t = fmt_min(start)
    name, phone = clean_name(member_name), clean_phone(phone)

    windows = db.query("SELECT start_time, end_time FROM coach_availability WHERE coach_id = ? AND weekday = ?",
                       (coach["coach_id"], d.isoweekday()))
    if not any(parse_hhmm(w["start_time"]) <= start and start + SESSION_MIN <= parse_hhmm(w["end_time"])
               for w in windows):
        raise ToolError(f"{coach['name']} is not available at {t} on {d}; free slots: {_coach_free_slots(coach, d)}")
    for g in db.query("SELECT name, start_time, duration_min FROM group_classes "
                      "WHERE coach_id = ? AND weekday = ? AND available = 1", (coach["coach_id"], d.isoweekday())):
        gs = parse_hhmm(g["start_time"])
        if overlaps(start, start + SESSION_MIN, gs, gs + g["duration_min"]):
            raise ToolError(f"{coach['name']} teaches group class {g['name']} at {g['start_time']} that day")
    if db.query_one("SELECT id FROM private_bookings WHERE coach_id = ? AND date = ? AND start_time = ? "
                    "AND status = 'booked'", (coach["coach_id"], d.isoformat(), t)):
        raise ToolError(f"{coach['name']} is already booked at {t} on {d}; free slots: {_coach_free_slots(coach, d)}")
    if db.query_one("SELECT id FROM private_bookings WHERE phone = ? AND date = ? AND start_time = ? "
                    "AND status = 'booked'", (phone, d.isoformat(), t)):
        raise ToolError("this phone already has a private session at the same time")

    payload = {"coach_id": coach["coach_id"], "date": d.isoformat(), "start_time": t,
               "member_name": name, "phone": phone}
    if not confirm_token:
        summary = {"coach_id": coach["coach_id"], "coach": coach["name"], "specialty": coach["specialty"],
                   "date": d.isoformat(), "weekday": WEEKDAY_ZH[d.isoweekday()],
                   "start_time": t, "end_time": fmt_min(start + SESSION_MIN), "price": coach["private_price"],
                   "member_name": name, "phone": mask_phone(phone),
                   "cancel_policy": "cancel within 24 hours before the session counts as used"}
        return {"status": "preview", "summary": summary,
                "confirm_token": _create_token("book_private_session", payload), "expires_in_min": TOKEN_TTL_MIN,
                "next_step": "read the summary to the customer; call again with confirm_token only after they confirm"}

    _check_token(confirm_token, "book_private_session", payload)
    booking_id = db.execute(
        "INSERT INTO private_bookings (coach_id, date, start_time, member_name, phone, status, created_at) "
        "VALUES (?, ?, ?, ?, ?, 'booked', ?)",
        (coach["coach_id"], d.isoformat(), t, name, phone, db.now().isoformat(timespec="seconds")))
    return {"status": "booked", "booking_type": "private", "booking_id": booking_id,
            "coach": coach["name"], "date": d.isoformat(), "start_time": t, "end_time": fmt_min(start + SESSION_MIN),
            "price": coach["private_price"], "member_name": name, "phone": mask_phone(phone),
            "next_step": BOOKED_STEP}


# ---------- 9. 取消預約 ----------

def _first_waitlisted(class_id, session_date):
    return db.query_one("SELECT * FROM group_enrollments WHERE class_id = ? AND session_date = ? "
                        "AND status = 'waitlist' ORDER BY id LIMIT 1", (class_id, session_date))


@tool
def cancel_booking(booking_type, booking_id, phone, confirm_token=None):
    phone = clean_phone(phone)
    booking_id = int(booking_id)
    if booking_type == "group":
        row = db.query_one("SELECT * FROM group_enrollments WHERE id = ?", (booking_id,))
    elif booking_type == "private":
        row = db.query_one("SELECT * FROM private_bookings WHERE id = ?", (booking_id,))
    else:
        raise ToolError("booking_type must be 'group' or 'private'")
    if not row:
        return {"found": False, "reason": f"no {booking_type} booking #{booking_id}"}
    if row["phone"] != phone:
        raise ToolError("phone number does not match this booking")
    if row["status"] == "cancelled":
        raise ToolError(f"booking #{booking_id} is already cancelled")

    payload = {"booking_type": booking_type, "booking_id": booking_id, "phone": phone}
    if booking_type == "private":
        coach = _get_coach(row["coach_id"])
        session = datetime.fromisoformat(f"{row['date']}T{row['start_time']}")
        if session <= db.now():
            raise ToolError("this session has already started or passed")
        late = session - db.now() < timedelta(hours=24)
        summary = {"booking_type": "private", "booking_id": booking_id, "coach": coach["name"],
                   "date": row["date"], "start_time": row["start_time"],
                   "member_name": row["member_name"], "phone": mask_phone(phone),
                   "late_cancellation": late, "counts_as_used_session": late}
        if not confirm_token:
            return {"status": "preview", "summary": summary,
                    "confirm_token": _create_token("cancel_booking", payload), "expires_in_min": TOKEN_TTL_MIN,
                    "next_step": "read the summary to the customer; call again with confirm_token only after they confirm"}
        _check_token(confirm_token, "cancel_booking", payload)
        db.execute("UPDATE private_bookings SET status = 'cancelled' WHERE id = ?", (booking_id,))
        return {"status": "cancelled", **summary}

    cls = _get_class(row["class_id"])
    d = date_cls.fromisoformat(row["session_date"])
    if _session_started(cls, d):
        raise ToolError("this session has already started or passed")
    if not confirm_token:
        nxt = _first_waitlisted(row["class_id"], row["session_date"]) if row["status"] == "enrolled" else None
        summary = {"booking_type": "group", "booking_id": booking_id, "class": cls["name"],
                   "session_date": row["session_date"], "start_time": cls["start_time"],
                   "member_name": row["member_name"], "phone": mask_phone(phone), "current_status": row["status"],
                   "waitlist_will_be_promoted": nxt is not None}
        return {"status": "preview", "summary": summary,
                "confirm_token": _create_token("cancel_booking", payload), "expires_in_min": TOKEN_TTL_MIN,
                "next_step": "read the summary to the customer; call again with confirm_token only after they confirm"}

    _check_token(confirm_token, "cancel_booking", payload)
    db.execute("UPDATE group_enrollments SET status = 'cancelled' WHERE id = ?", (booking_id,))
    result = {"status": "cancelled", "booking_type": "group", "booking_id": booking_id,
              "class": cls["name"], "session_date": row["session_date"], "was": row["status"]}
    if row["status"] == "enrolled":
        nxt = _first_waitlisted(row["class_id"], row["session_date"])
        if nxt:
            db.execute("UPDATE group_enrollments SET status = 'enrolled' WHERE id = ?", (nxt["id"],))
            result["promoted_from_waitlist"] = {"booking_id": nxt["id"], "member_name": mask_name(nxt["member_name"]),
                                                "phone": mask_phone(nxt["phone"])}
    result["session"] = _session_info(cls, d)
    return result


# ---------- 10. 轉真人 ----------

@tool
def transfer_to_human(reason):
    n = db.now()
    return {"transferred": True, "case_id": f"H{n:%Y%m%d%H%M}-{secrets.token_hex(2)}",
            "reason": str(reason), "phone": CENTER_PHONE, "line": CENTER_LINE,
            "service_hours": f"{OPEN_HOUR:02d}:00-{CLOSE_HOUR:02d}:00 daily", "created_at": n.isoformat(timespec="minutes")}


# ---------- Tool schema ----------

TOPICS = [r["topic"] for r in db.read_csv("facility_info.csv")]
DATE_DESC = "Date in YYYY-MM-DD. Convert words like 'this Friday' or 'tomorrow' using today's date from the system prompt."
PHONE_DESC = "Customer's Taiwan mobile number, 10 digits starting with 09, e.g. 0912345678."


def _schema(name, description, properties, required):
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties, "required": required}}}


SCHEMAS = {
    "get_center_info": _schema(
        "get_center_info",
        "Look up general facts about the sports center: opening hours, address and transport, contact, facilities, "
        "gym or pool rules, discount identity rules, ticket rules, court peak hours, cancellation, parking, payment, "
        "group class rules, private coach rules, lost and found.",
        {"topic": {"type": "string", "enum": TOPICS, "description": "The topic to look up."}},
        ["topic"]),
    "search_services": _schema(
        "search_services",
        "Search services and their list prices: pool and gym tickets (single, monthly pass, 30-ticket book), "
        "court rental (badminton, table tennis, pickleball, peak/off-peak), body composition analysis, "
        "personal training and group class prices. Also shows if a service is not available. "
        "Use this for 'how much is X' without quantity or time, and to check whether the center sells or offers "
        "anything (products, drinks, equipment); found:false means it is not in our data.",
        {"keyword": {"type": "string", "description": "Service name in the customer's words, e.g. 游泳, gym, 羽球, 私人教練."},
         "category": {"type": "string", "enum": ["pool", "gym", "court", "private_coach", "group_class"],
                      "description": "Optional category filter."}},
        ["keyword"]),
    "list_group_classes": _schema(
        "list_group_classes",
        "List upcoming group class sessions with time, coach, room, enrolled count, min/max students, spots left, "
        "waitlist, and whether the class has enough students to run. Use for schedule, 'is it full', "
        "'will it run' questions.",
        {"keyword": {"type": "string", "description": "Optional class name, e.g. 空中瑜珈, Zumba, TRX."},
         "date": {"type": "string", "description": "Optional. " + DATE_DESC},
         "weekday": {"type": "integer", "description": "Optional weekday 1=Mon ... 7=Sun (use date when possible)."}},
        []),
    "find_private_slots": _schema(
        "find_private_slots",
        "Find free 60-minute private (1-on-1) coach slots on a date. Private sessions must be booked at least 1 day ahead.",
        {"date": {"type": "string", "description": DATE_DESC},
         "coach_keyword": {"type": "string", "description": "Optional coach name or coach_id, e.g. 林志豪 or C01."},
         "specialty": {"type": "string", "description": "Optional specialty, e.g. 重量訓練, 瑜珈, 游泳, 減脂."}},
        ["date"]),
    "calculate_cost": _schema(
        "calculate_cost",
        "Calculate the exact total cost in code. Use whenever the customer gives a quantity, identity, time or "
        "number of sessions. Court rental splits each hour into peak/off-peak by weekday/holiday. Tickets use "
        "identity prices. Classes compare single sessions against the 10-session pack. Never do math yourself.",
        {"service_keyword": {"type": "string", "description": "Service name, e.g. 羽球場, 游泳, 健身房, 私人教練, 團體課, 空中瑜珈."},
         "quantity": {"type": "integer", "description": "Number of tickets / people / courts / sessions. Default 1."},
         "identity": {"type": "string", "enum": ["adult", "student", "half"],
                      "description": "Ticket identity: adult, student (student ID), half (age 65+ or under 6)."},
         "date": {"type": "string", "description": "Court rental only. " + DATE_DESC},
         "start_time": {"type": "string", "description": "Court rental only. Start time HH:MM on the hour, e.g. 18:00."},
         "hours": {"type": "integer", "description": "Court rental hours, or gym hours per person. Default 1."}},
        ["service_keyword"]),
    "compare_plans": _schema(
        "compare_plans",
        "Compare monthly cost of single entry vs monthly pass vs 30-ticket book for the pool or gym, "
        "and return the cheapest plan. Convert 'N times a week' to visits_per_month = N x 4.",
        {"category": {"type": "string", "enum": ["pool", "gym"]},
         "visits_per_month": {"type": "integer", "description": "Visits per month."},
         "hours_per_visit": {"type": "integer", "description": "Gym hours per visit (gym single entry is charged per hour). Default 1."},
         "identity": {"type": "string", "enum": ["adult", "student", "half"], "description": "Default adult."}},
        ["category", "visits_per_month"]),
    "book_group_class": _schema(
        "book_group_class",
        "Book a group class session (writes data). Step 1: call WITHOUT confirm_token to get a preview and a token. "
        "Step 2: read the summary to the customer; ONLY after they clearly confirm, call again with the same "
        "arguments plus confirm_token. If the class is full the customer joins the waitlist.",
        {"class_id": {"type": "string", "description": "class_id from list_group_classes, e.g. G01."},
         "session_date": {"type": "string", "description": "Session date. " + DATE_DESC},
         "member_name": {"type": "string", "description": "Customer's name."},
         "phone": {"type": "string", "description": PHONE_DESC},
         "confirm_token": {"type": "string", "description": "Token from the preview. Only after customer confirmation."}},
        ["class_id", "session_date", "member_name", "phone"]),
    "book_private_session": _schema(
        "book_private_session",
        "Book a 60-minute private coach session (writes data). Step 1: call WITHOUT confirm_token to get a preview "
        "and a token. Step 2: ONLY after the customer clearly confirms, call again with the same arguments plus confirm_token.",
        {"coach_id": {"type": "string", "description": "coach_id from find_private_slots, e.g. C01."},
         "date": {"type": "string", "description": DATE_DESC},
         "start_time": {"type": "string", "description": "Start time HH:MM on the hour, from find_private_slots."},
         "member_name": {"type": "string", "description": "Customer's name."},
         "phone": {"type": "string", "description": PHONE_DESC},
         "confirm_token": {"type": "string", "description": "Token from the preview. Only after customer confirmation."}},
        ["coach_id", "date", "start_time", "member_name", "phone"]),
    "cancel_booking": _schema(
        "cancel_booking",
        "Cancel a group class or private session booking (writes data). Needs the booking_id and the phone used "
        "to book. Step 1: call WITHOUT confirm_token to get a preview and a token. Step 2: ONLY after the customer "
        "clearly confirms, call again with the same arguments plus confirm_token. "
        "Cancelling a group class automatically moves the first waitlisted person in.",
        {"booking_type": {"type": "string", "enum": ["group", "private"]},
         "booking_id": {"type": "integer", "description": "Booking number given when the booking was made."},
         "phone": {"type": "string", "description": PHONE_DESC},
         "confirm_token": {"type": "string", "description": "Token from the preview. Only after customer confirmation."}},
        ["booking_type", "booking_id", "phone"]),
    "transfer_to_human": _schema(
        "transfer_to_human",
        "Hand the customer over to a staff member. Use for complaints, refund disputes, injuries, asking for a "
        "manager, or any question the data does not cover.",
        {"reason": {"type": "string", "description": "Short summary of the customer's issue."}},
        ["reason"]),
}

FUNCTIONS = {
    "get_center_info": get_center_info, "search_services": search_services,
    "list_group_classes": list_group_classes, "find_private_slots": find_private_slots,
    "calculate_cost": calculate_cost, "compare_plans": compare_plans,
    "book_group_class": book_group_class, "book_private_session": book_private_session,
    "cancel_booking": cancel_booking, "transfer_to_human": transfer_to_human,
}

# 白名單：Agent 只能執行這裡面的工具
TOOLS = {name: (FUNCTIONS[name], SCHEMAS[name]) for name in SCHEMAS}
TOOL_SCHEMAS = [schema for _, schema in TOOLS.values()]


def _coerce(name, value, spec):
    t = spec.get("type")
    try:
        if t == "integer":
            f = float(value)
            if f != int(f):
                raise ValueError
            value = int(f)
        elif t == "number":
            value = float(value)
        elif t == "string":
            value = str(value).strip()
    except (TypeError, ValueError):
        raise ToolError(f"argument '{name}' must be {t}, got {value!r}")
    if "enum" in spec and value not in spec["enum"]:
        raise ToolError(f"argument '{name}' must be one of {spec['enum']}, got {value!r}")
    return value


def execute_tool(name, args):
    """依白名單執行工具；未知工具、參數錯誤都回 {"error": ...}，不丟例外。"""
    if name not in TOOLS:
        return {"error": f"unknown tool '{name}'", "allowed_tools": list(TOOLS)}
    fn, schema = TOOLS[name]
    if isinstance(args, str):
        try:
            args = json.loads(args) if args.strip() else {}
        except json.JSONDecodeError:
            return {"error": "arguments must be a JSON object"}
    if args is None:
        args = {}
    if not isinstance(args, dict):
        return {"error": "arguments must be a JSON object"}
    params = schema["function"]["parameters"]
    try:
        clean = {k: _coerce(k, v, params["properties"][k]) for k, v in args.items()
                 if k in params["properties"] and v is not None and v != ""}
    except ToolError as e:
        return {"error": str(e)}
    missing = [r for r in params["required"] if r not in clean]
    if missing:
        return {"error": f"missing required argument(s): {', '.join(missing)}"}
    try:
        return fn(**clean)
    except Exception as e:  # 工具內部錯誤也回給模型，不讓 Agent 迴圈中斷
        return {"error": f"{type(e).__name__}: {e}"}
