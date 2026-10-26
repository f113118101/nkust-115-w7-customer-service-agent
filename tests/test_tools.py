"""工具單元測試（不需 LLM）。固定「現在」為 2026-10-26（週一）10:00，日期才不會隨執行日變動。

執行：python -m pytest tests -v
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

import db
import tools
from tools import execute_tool

NOW = datetime(2026, 10, 26, 10, 0)  # 週一
TUE, WED, SAT, SUN = "2026-10-27", "2026-10-28", "2026-10-31", "2026-11-01"


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    clock = {"now": NOW}
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(db, "now", lambda: clock["now"])
    db.init_db()
    return clock


def call(name, **args):
    return execute_tool(name, args)


def count_enrollments(class_id, date, status=None):
    sql = "SELECT COUNT(*) AS n FROM group_enrollments WHERE class_id = ? AND session_date = ?"
    params = [class_id, date]
    if status:
        sql += " AND status = ?"
        params.append(status)
    return db.query_one(sql, params)["n"]


def session(result, class_id):
    return next(s for s in result["sessions"] if s["class_id"] == class_id)


# ---------- DB 建置 ----------

def test_tables_built_from_csv():
    assert db.query_one("SELECT COUNT(*) AS n FROM facility_info")["n"] >= 8
    assert db.query_one("SELECT COUNT(*) AS n FROM services")["n"] >= 15
    assert db.query_one("SELECT price FROM services WHERE service_id = 'S16'")["price"] is None


def test_seed_dates_are_upcoming_sessions():
    rows = db.query("SELECT DISTINCT class_id, session_date FROM group_enrollments ORDER BY class_id, session_date")
    assert ("G01", SAT) in [(r["class_id"], r["session_date"]) for r in rows]
    assert ("G01", "2026-11-07") in [(r["class_id"], r["session_date"]) for r in rows]  # week_offset=1
    assert ("G03", TUE) in [(r["class_id"], r["session_date"]) for r in rows]


# ---------- 1. get_center_info ----------

@pytest.mark.parametrize("topic", tools.TOPICS)
def test_every_topic_found(topic):
    r = call("get_center_info", topic=topic)
    assert r["found"] is True and r["content"]


def test_unknown_topic_not_found():
    r = tools.get_center_info("pets")
    assert r["found"] is False
    assert "opening_hours" in r["available_topics"]


def test_unknown_topic_rejected_by_enum():
    assert "error" in call("get_center_info", topic="pets")


# ---------- 2. search_services / 別名 ----------

def test_alias_swim():
    r = call("search_services", keyword="游泳")
    assert r["found"] and all(x["category"] == "pool" for x in r["results"] if x["category"] != "group_class")
    assert {"S01", "S02", "S03", "S04", "S05"} <= {x.get("service_id") for x in r["results"]}


def test_alias_gym():
    r = call("search_services", keyword="gym")
    ids = {x["service_id"] for x in r["results"]}
    assert {"S06", "S07", "S08"} <= ids


def test_alias_aerial_yoga_class():
    r = call("list_group_classes", keyword="空瑜")
    assert r["found"] and {s["class_id"] for s in r["sessions"]} == {"G01"}


def test_search_not_found():
    r = call("search_services", keyword="運動護具")
    assert r["found"] is False and r["reason"]


def test_unavailable_service_has_no_price():
    r = call("search_services", keyword="籃球")
    rec = next(x for x in r["results"] if x["service_id"] == "S16")
    assert rec["available"] is False
    assert rec["note"]
    assert "price" not in rec


def test_unavailable_service_cost_error():
    r = call("calculate_cost", service_keyword="籃球場", date=TUE, start_time="10:00", hours=1)
    assert "error" in r and r["available"] is False
    assert "total" not in r


# ---------- 5. calculate_cost ----------

def test_badminton_weekday_crosses_peak():
    r = call("calculate_cost", service_keyword="羽球場", date=TUE, start_time="16:00", hours=2)
    assert [i["subtotal"] for i in r["items"]] == [250, 350]
    assert r["total"] == 600


def test_badminton_holiday_crosses_peak():
    r = call("calculate_cost", service_keyword="羽毛球", date=SAT, start_time="09:00", hours=2)
    assert [i["unit_price"] for i in r["items"]] == [250, 350]
    assert r["total"] == 600


def test_court_multiple_courts_and_peak_only():
    r = call("calculate_cost", service_keyword="桌球", quantity=2, date=TUE, start_time="18:00", hours=2)
    assert r["total"] == 150 * 2 * 2


def test_court_needs_time():
    assert "error" in call("calculate_cost", service_keyword="羽球場")


def test_court_outside_opening_hours():
    assert "error" in call("calculate_cost", service_keyword="羽球場", date=TUE, start_time="21:00", hours=2)


def test_student_pool_ticket_x3():
    r = call("calculate_cost", service_keyword="游泳", quantity=3, identity="student")
    assert r["total"] == 240


def test_identity_from_keyword_alias():
    r = call("calculate_cost", service_keyword="學生游泳票", quantity=3)
    assert r["total"] == 240


def test_gym_student_ticket_error():
    r = call("calculate_cost", service_keyword="健身房", identity="student")
    assert "error" in r and r["available_identities"] == ["adult"]


def test_gym_hours():
    r = call("calculate_cost", service_keyword="健身房", hours=3)
    assert r["total"] == 150


def test_monthly_pass_by_keyword():
    r = call("calculate_cost", service_keyword="游泳月卡")
    assert r["total"] == 1250


def test_ambiguous_keyword():
    r = call("calculate_cost", service_keyword="月卡")
    assert "error" in r and len(r["options"]) == 2


def test_private_coach_sessions_compare_pack():
    r = call("calculate_cost", service_keyword="私人教練", quantity=12)
    assert r["total"] == 10800 + 2 * 1200
    assert r["comparison"]["all_single_sessions"] == 14400


def test_group_class_few_sessions_single_cheaper():
    r = call("calculate_cost", service_keyword="團體課", quantity=5)
    assert r["total"] == 1250 and r["comparison"]["saving"] == 0


def test_group_class_by_class_name():
    r = call("calculate_cost", service_keyword="空中瑜珈", quantity=10)
    assert r["total"] == 2200


# ---------- 6. compare_plans ----------

def test_compare_gym_long_visits_monthly_cheapest():
    # 健身房單次 50 元/小時：每月 20 次、每次 2 小時 → 單次 2000、回數票 1600、月卡 1250
    r = call("compare_plans", category="gym", visits_per_month=20, hours_per_visit=2)
    assert r["cheapest_plan"] == "monthly_pass" and r["cheapest_monthly_cost"] == 1250


def test_compare_gym_short_visits_single_cheapest():
    # 每月 20 次、每次 1 小時 → 單次 1000 < 月卡 1250
    r = call("compare_plans", category="gym", visits_per_month=20)
    assert r["cheapest_plan"] == "single_entry" and r["cheapest_monthly_cost"] == 1000


def test_compare_pool_student():
    r = call("compare_plans", category="pool", visits_per_month=8, identity="student")
    costs = {o["plan"]: o["monthly_cost"] for o in r["options"]}
    assert costs == {"single_entry": 640, "monthly_pass": 1250, "ticket_book": 640}


# ---------- 3. list_group_classes ----------

def test_class_statuses():
    r = call("list_group_classes")
    g01, g02, g03 = session(r, "G01"), session(r, "G02"), session(r, "G03")
    assert g01["date"] == SAT and g01["students_needed_to_run"] == 1 and not g01["is_confirmed"]
    assert "cancelled_if_below_min_by" in g01
    assert g02["status"] == "confirmed_spots_left" and g02["spots_left"] == 10
    assert g03["status"] == "full_waitlist_open" and g03["waitlist"] == 1
    assert {"G08"} == {p["class_id"] for p in r["paused_classes"]}


def test_classes_by_date():
    r = call("list_group_classes", date=SAT)
    assert {s["class_id"] for s in r["sessions"]} == {"G01", "G05"}


def test_classes_past_date_error():
    assert "error" in call("list_group_classes", date="2026-10-01")


# ---------- 7. book_group_class ----------

def book_group(class_id, date, name, phone):
    preview = call("book_group_class", class_id=class_id, session_date=date, member_name=name, phone=phone)
    assert preview["status"] == "preview", preview
    return preview, call("book_group_class", class_id=class_id, session_date=date, member_name=name,
                         phone=phone, confirm_token=preview["confirm_token"])


def test_preview_does_not_write():
    before = count_enrollments("G01", SAT)
    r = call("book_group_class", class_id="G01", session_date=SAT, member_name="測試者", phone="0912345678")
    assert r["status"] == "preview" and r["confirm_token"]
    assert r["summary"]["phone"] == "0912xxx678"
    assert count_enrollments("G01", SAT) == before


def test_booking_makes_class_confirmed():
    _, r = book_group("G01", SAT, "測試者", "0912345678")
    assert r["status"] == "booked" and r["enrollment_status"] == "enrolled"
    assert r["session"]["is_confirmed"] is True and r["session"]["students_needed_to_run"] == 0


def test_wrong_token_rejected():
    call("book_group_class", class_id="G01", session_date=SAT, member_name="測試者", phone="0912345678")
    r = call("book_group_class", class_id="G01", session_date=SAT, member_name="測試者",
             phone="0912345678", confirm_token="zzzzzz")
    assert "error" in r
    assert count_enrollments("G01", SAT) == 3


def test_token_payload_mismatch_rejected():
    p = call("book_group_class", class_id="G01", session_date=SAT, member_name="測試者", phone="0912345678")
    r = call("book_group_class", class_id="G01", session_date=SAT, member_name="別人",
             phone="0912345678", confirm_token=p["confirm_token"])
    assert "error" in r and count_enrollments("G01", SAT) == 3


def test_token_expired(fresh_db):
    p = call("book_group_class", class_id="G01", session_date=SAT, member_name="測試者", phone="0912345678")
    fresh_db["now"] = NOW + timedelta(minutes=11)
    r = call("book_group_class", class_id="G01", session_date=SAT, member_name="測試者",
             phone="0912345678", confirm_token=p["confirm_token"])
    assert "expired" in r["error"]


def test_token_cannot_be_reused():
    p, _ = book_group("G01", SAT, "測試者", "0912345678")
    r = call("book_group_class", class_id="G01", session_date=SAT, member_name="測試者",
             phone="0912345678", confirm_token=p["confirm_token"])
    assert "error" in r


def test_duplicate_booking_rejected():
    r = call("book_group_class", class_id="G01", session_date=SAT, member_name="李小美", phone="0911000001")
    assert "error" in r


def test_weekday_mismatch_rejected():
    r = call("book_group_class", class_id="G01", session_date=SUN, member_name="測試者", phone="0912345678")
    assert "error" in r and "週六" in r["error"]


def test_past_session_rejected():
    r = call("book_group_class", class_id="G01", session_date="2026-10-24", member_name="測試者", phone="0912345678")
    assert "error" in r


def test_invalid_phone_rejected():
    r = call("book_group_class", class_id="G01", session_date=SAT, member_name="測試者", phone="12345")
    assert "error" in r


def test_paused_class_rejected():
    r = call("book_group_class", class_id="G08", session_date=SUN, member_name="測試者", phone="0912345678")
    assert "error" in r


def test_class_name_accepted_as_id():
    r = call("book_group_class", class_id="空中瑜珈", session_date=SAT, member_name="測試者", phone="0912345678")
    assert r["status"] == "preview" and r["summary"]["class_id"] == "G01"


def test_full_class_goes_to_waitlist():
    p, r = book_group("G03", TUE, "測試者", "0912345678")
    assert p["summary"]["will_be"] == "waitlist" and p["summary"]["waitlist_position"] == 2
    assert r["enrollment_status"] == "waitlist" and r["waitlist_position"] == 2
    assert count_enrollments("G03", TUE, "enrolled") == 8


# ---------- 9. cancel_booking ----------

def seed_id(phone):
    return db.query_one("SELECT id FROM group_enrollments WHERE phone = ?", (phone,))["id"]


def cancel(booking_type, booking_id, phone):
    preview = call("cancel_booking", booking_type=booking_type, booking_id=booking_id, phone=phone)
    assert preview["status"] == "preview", preview
    return preview, call("cancel_booking", booking_type=booking_type, booking_id=booking_id, phone=phone,
                         confirm_token=preview["confirm_token"])


def test_cancel_preview_does_not_write():
    bid = seed_id("0922000001")
    p = call("cancel_booking", booking_type="group", booking_id=bid, phone="0922000001")
    assert p["status"] == "preview" and p["summary"]["waitlist_will_be_promoted"] is True
    assert db.query_one("SELECT status FROM group_enrollments WHERE id = ?", (bid,))["status"] == "enrolled"
    assert count_enrollments("G03", TUE, "waitlist") == 1


def test_cancel_promotes_waitlist():
    _, r = cancel("group", seed_id("0922000001"), "0922000001")
    assert r["status"] == "cancelled"
    promoted = r["promoted_from_waitlist"]
    assert promoted["member_name"] == "曾○琪" and promoted["phone"] == "0922xxx009"
    assert r["session"]["enrolled"] == 8 and r["session"]["waitlist"] == 0


def test_cancel_wrong_token_rejected():
    bid = seed_id("0922000001")
    call("cancel_booking", booking_type="group", booking_id=bid, phone="0922000001")
    r = call("cancel_booking", booking_type="group", booking_id=bid, phone="0922000001", confirm_token="zzzzzz")
    assert "error" in r
    assert db.query_one("SELECT status FROM group_enrollments WHERE id = ?", (bid,))["status"] == "enrolled"


def test_cancel_token_for_other_booking_rejected():
    p = call("cancel_booking", booking_type="group", booking_id=seed_id("0922000001"), phone="0922000001")
    other = seed_id("0922000002")
    r = call("cancel_booking", booking_type="group", booking_id=other, phone="0922000002",
             confirm_token=p["confirm_token"])
    assert "differ" in r["error"]
    assert db.query_one("SELECT status FROM group_enrollments WHERE id = ?", (other,))["status"] == "enrolled"


def test_booking_token_cannot_cancel():
    p = call("book_group_class", class_id="G01", session_date=SAT, member_name="測試者", phone="0912345678")
    bid = seed_id("0911000001")
    r = call("cancel_booking", booking_type="group", booking_id=bid, phone="0911000001",
             confirm_token=p["confirm_token"])
    assert "invalid" in r["error"]


def test_cancel_wrong_phone_rejected():
    r = call("cancel_booking", booking_type="group", booking_id=seed_id("0922000001"), phone="0999999999")
    assert "error" in r
    assert count_enrollments("G03", TUE, "enrolled") == 8


def test_cancel_twice_rejected():
    bid = seed_id("0933000001")
    cancel("group", bid, "0933000001")
    assert "error" in call("cancel_booking", booking_type="group", booking_id=bid, phone="0933000001")


def test_cancel_unknown_booking():
    r = call("cancel_booking", booking_type="private", booking_id=999, phone="0912345678")
    assert r["found"] is False


# ---------- 4. / 8. 私人教練 ----------

def test_find_private_slots_tomorrow():
    r = call("find_private_slots", date=TUE)
    by_coach = {c["coach_id"]: c["slots"] for c in r["coaches"]}
    assert by_coach["C01"] == ["14:00", "15:00", "16:00", "17:00"]
    assert by_coach["C03"] == ["09:00", "10:00", "11:00"]


def test_find_private_slots_specialty_alias():
    r = call("find_private_slots", date=TUE, specialty="重訓")
    assert [c["coach_id"] for c in r["coaches"]] == ["C01"]


def test_find_private_slots_today_rejected():
    r = call("find_private_slots", date="2026-10-26")
    assert r["found"] is False and r["earliest_bookable_date"] == TUE


def test_private_booking_flow_and_slot_removed():
    p = call("book_private_session", coach_id="C01", date=TUE, start_time="14:00",
             member_name="測試者", phone="0912345678")
    assert p["status"] == "preview" and p["summary"]["price"] == 1200
    assert db.query_one("SELECT COUNT(*) AS n FROM private_bookings")["n"] == 0
    r = call("book_private_session", coach_id="C01", date=TUE, start_time="14:00",
             member_name="測試者", phone="0912345678", confirm_token=p["confirm_token"])
    assert r["status"] == "booked"
    slots = call("find_private_slots", date=TUE, coach_keyword="C01")["coaches"][0]["slots"]
    assert "14:00" not in slots
    # 同一時段別人再約 → 拒絕
    r2 = call("book_private_session", coach_id="C01", date=TUE, start_time="14:00",
              member_name="另一人", phone="0987654321")
    assert "already booked" in r2["error"]


def test_private_outside_availability_rejected():
    r = call("book_private_session", coach_id="C01", date=TUE, start_time="10:00",
             member_name="測試者", phone="0912345678")
    assert "not available" in r["error"]


def test_private_conflicts_with_group_class():
    # CSV 設計上私教空檔不與團課重疊；這裡人為加一段空檔，確認程式仍會擋下 TRX（週二 19:00）
    db.execute("INSERT INTO coach_availability VALUES ('C01', 2, '18:00', '21:00')")
    r = call("book_private_session", coach_id="C01", date=TUE, start_time="19:00",
             member_name="測試者", phone="0912345678")
    assert "group class" in r["error"]
    slots = call("find_private_slots", date=TUE, coach_keyword="C01")["coaches"][0]["slots"]
    assert "19:00" not in slots and "18:00" in slots and "20:00" in slots


def test_private_today_rejected():
    r = call("book_private_session", coach_id="C01", date="2026-10-26", start_time="14:00",
             member_name="測試者", phone="0912345678")
    assert "1 day in advance" in r["error"]


def test_private_coach_without_private_lessons():
    r = call("book_private_session", coach_id="C04", date=WED, start_time="10:00",
             member_name="測試者", phone="0912345678")
    assert "does not offer" in r["error"]


def test_private_not_on_the_hour():
    r = call("book_private_session", coach_id="C01", date=TUE, start_time="14:30",
             member_name="測試者", phone="0912345678")
    assert "error" in r


def test_cancel_private_late(fresh_db):
    p = call("book_private_session", coach_id="C03", date=TUE, start_time="09:00",
             member_name="測試者", phone="0912345678")
    r = call("book_private_session", coach_id="C03", date=TUE, start_time="09:00",
             member_name="測試者", phone="0912345678", confirm_token=p["confirm_token"])
    fresh_db["now"] = datetime(2026, 10, 26, 20, 0)  # 開課前 13 小時
    p, c = cancel("private", r["booking_id"], "0912345678")
    assert p["summary"]["counts_as_used_session"] is True
    assert c["status"] == "cancelled" and c["late_cancellation"] is True
    assert db.query_one("SELECT status FROM private_bookings WHERE id = ?", (r["booking_id"],))["status"] == "cancelled"


def test_cancel_private_preview_does_not_write():
    p = call("book_private_session", coach_id="C03", date=TUE, start_time="10:00",
             member_name="測試者", phone="0912345678")
    r = call("book_private_session", coach_id="C03", date=TUE, start_time="10:00",
             member_name="測試者", phone="0912345678", confirm_token=p["confirm_token"])
    c = call("cancel_booking", booking_type="private", booking_id=r["booking_id"], phone="0912345678")
    assert c["status"] == "preview" and c["summary"]["late_cancellation"] is False
    assert db.query_one("SELECT status FROM private_bookings WHERE id = ?", (r["booking_id"],))["status"] == "booked"


# ---------- 10. transfer_to_human ----------

def test_transfer_to_human():
    r = call("transfer_to_human", reason="想客訴櫃台人員")
    assert r["transferred"] and r["phone"] == "(07) 625-0666" and r["reason"] == "想客訴櫃台人員"


# ---------- registry ----------

def test_unknown_tool_rejected():
    r = execute_tool("drop_database", {})
    assert "unknown tool" in r["error"]


def test_missing_required_argument():
    assert "missing" in execute_tool("calculate_cost", {})["error"]


def test_args_as_json_string_and_coercion():
    r = execute_tool("calculate_cost", '{"service_keyword": "游泳", "quantity": "3", "identity": "student"}')
    assert r["total"] == 240


def test_bad_integer_argument():
    assert "error" in execute_tool("calculate_cost", {"service_keyword": "游泳", "quantity": "three"})


def test_tool_results_are_data_not_sentences():
    for name, args in [("get_center_info", {"topic": "parking"}), ("search_services", {"keyword": "游泳"}),
                       ("list_group_classes", {}), ("transfer_to_human", {"reason": "x"})]:
        assert isinstance(execute_tool(name, args), dict)
