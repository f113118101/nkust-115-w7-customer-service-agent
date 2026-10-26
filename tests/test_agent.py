"""Agent 迴圈測試：用假的 LLM client 依劇本回覆，不需真模型與金鑰。"""
import json
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx
import openai
import pytest

import agent
import db
import prompts


def tool_call(name, args, call_id="call_1"):
    fn = SimpleNamespace(name=name, arguments=json.dumps(args, ensure_ascii=False))
    return SimpleNamespace(id=call_id, type="function", function=fn)


def reply(content=None, tool_calls=None):
    msg = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


def _chunk(content=None, tool_calls=None):
    return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=content, tool_calls=tool_calls))])


def to_chunks(resp):
    """把一個完整回覆拆成串流片段：文字切兩段、每個工具的 arguments 也切兩段。"""
    msg = resp.choices[0].message
    chunks = []
    if msg.content:
        half = len(msg.content) // 2
        chunks += [_chunk(content=part) for part in (msg.content[:half], msg.content[half:]) if part]
    for i, tc in enumerate(msg.tool_calls or []):
        a = tc.function.arguments
        h = len(a) // 2
        chunks.append(_chunk(tool_calls=[SimpleNamespace(index=i, id=tc.id, function=SimpleNamespace(name=tc.function.name, arguments=a[:h]))]))
        chunks.append(_chunk(tool_calls=[SimpleNamespace(index=i, id=None, function=SimpleNamespace(name=None, arguments=a[h:]))]))
    return chunks


class FakeClient:
    """依序回傳劇本中的回覆（stream=True 時拆成片段）；劇本元素若是 Exception 就丟出。"""

    def __init__(self, script):
        self.script = list(script)
        self.requests = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.requests.append(json.loads(json.dumps(kwargs, default=str)))
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return iter(to_chunks(item)) if kwargs.get("stream") else item


@pytest.fixture(autouse=True)
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(db, "now", lambda: datetime(2026, 10, 26, 10, 0))
    monkeypatch.setattr(agent, "API_KEY", "test-key")
    monkeypatch.setattr(agent.time, "sleep", lambda s: None)
    db.init_db()


def use(monkeypatch, script, mode="native"):
    client = FakeClient(script)
    monkeypatch.setattr(agent, "_client", client)
    monkeypatch.setattr(agent, "_tool_mode", mode)
    return client


def rate_limit_error():
    req = httpx.Request("POST", "http://test/v1/chat/completions")
    return openai.RateLimitError("429", response=httpx.Response(429, request=req), body=None)


# ---------- native ----------

def test_native_tool_then_answer(monkeypatch):
    client = use(monkeypatch, [
        reply(tool_calls=[tool_call("calculate_cost", {"service_keyword": "羽球場", "date": "2026-10-27",
                                                       "start_time": "16:00", "hours": 2})]),
        reply("平日 16:00 起租 2 小時共 NT$600。"),
    ])
    text, trace, history = agent.run_agent("平日 16:00 租羽球場 2 小時多少？")
    assert text == "平日 16:00 起租 2 小時共 NT$600。"
    assert trace[0]["tool"] == "calculate_cost" and trace[0]["result"]["total"] == 600
    tool_msg = client.requests[1]["messages"][-1]
    assert tool_msg["role"] == "tool" and json.loads(tool_msg["content"])["total"] == 600
    assert "tools" in client.requests[0]
    assert [m["role"] for m in history] == ["user", "assistant", "tool", "assistant"]


def test_unknown_tool_refused(monkeypatch):
    use(monkeypatch, [reply(tool_calls=[tool_call("run_sql", {"q": "DROP TABLE services"})]), reply("抱歉。")])
    _, trace, _ = agent.run_agent("hi")
    assert "unknown tool" in trace[0]["result"]["error"]
    assert db.query_one("SELECT COUNT(*) AS n FROM services")["n"] == 20


def test_max_steps_stops_loop(monkeypatch):
    monkeypatch.setattr(agent, "MAX_STEPS", 3)
    client = use(monkeypatch, [reply(tool_calls=[tool_call("get_center_info", {"topic": "parking"})])] * 10)
    text, trace, _ = agent.run_agent("停車？")
    assert len(client.requests) == 3
    assert text == agent.BUSY_REPLY and "max_steps" in trace[-1]["result"]["error"]


def test_invalid_tool_arguments(monkeypatch):
    bad = SimpleNamespace(id="c1", type="function", function=SimpleNamespace(name="get_center_info", arguments="{oops"))
    use(monkeypatch, [reply(tool_calls=[bad]), reply("抱歉")])
    _, trace, _ = agent.run_agent("x")
    assert "not valid JSON" in trace[0]["result"]["error"]


def test_rate_limit_backoff_then_success(monkeypatch):
    waits = []
    monkeypatch.setattr(agent.time, "sleep", waits.append)
    use(monkeypatch, [rate_limit_error(), rate_limit_error(), reply("您好")])
    text, _, _ = agent.run_agent("你好")
    assert text == "您好" and waits == [2, 4]


def test_rate_limit_gives_up(monkeypatch):
    use(monkeypatch, [rate_limit_error()] * 4)
    text, trace, _ = agent.run_agent("你好")
    assert text == agent.BUSY_REPLY and "429" in trace[-1]["result"]["error"]


def test_no_api_key(monkeypatch):
    monkeypatch.setattr(agent, "API_KEY", "")
    text, trace, _ = agent.run_agent("你好")
    assert text == agent.NO_KEY_REPLY and trace[0]["result"]["error"]


def test_confirm_token_carried_in_history(monkeypatch):
    args = {"class_id": "G01", "session_date": "2026-10-31", "member_name": "測試者", "phone": "0912345678"}
    client = use(monkeypatch, [reply(tool_calls=[tool_call("book_group_class", args)]), reply("請確認以下資料…")])
    _, trace, history = agent.run_agent("幫我報名週六空中瑜珈，測試者 0912345678")
    token = trace[0]["result"]["confirm_token"]

    client.script = [reply(tool_calls=[tool_call("book_group_class", {**args, "confirm_token": token}, "call_2")]),
                     reply("報名完成！")]
    _, trace2, _ = agent.run_agent("確認", history)
    assert trace2[0]["result"]["status"] == "booked"
    # 第二輪送給模型的訊息中看得到上一輪的 token
    assert token in json.dumps(client.requests[-2]["messages"], ensure_ascii=False)


def test_history_trim_keeps_turn_boundaries():
    history = []
    for i in range(15):
        history += [{"role": "user", "content": f"q{i}"},
                    {"role": "assistant", "content": "", "tool_calls": []},
                    {"role": "tool", "tool_call_id": "x", "content": "{}"},
                    {"role": "assistant", "content": f"a{i}"}]
    trimmed = agent._trim_history(history)
    assert trimmed[0] == {"role": "user", "content": f"q{15 - agent.MAX_HISTORY_TURNS}"}
    assert sum(1 for m in trimmed if m["role"] == "user") == agent.MAX_HISTORY_TURNS


# ---------- json fallback ----------

def test_json_mode_tool_then_answer(monkeypatch):
    client = use(monkeypatch, [
        reply('```json\n{"tool": "search_services", "args": {"keyword": "游泳"}}\n```'),
        reply('{"answer": "學生票每次 NT$80。"}'),
    ], mode="json")
    text, trace, _ = agent.run_agent("學生游泳多少？")
    assert text == "學生票每次 NT$80。"
    assert trace[0]["tool"] == "search_services" and trace[0]["result"]["found"]
    assert "tools" not in client.requests[0]
    assert "EXACTLY ONE line of JSON" in client.requests[0]["messages"][0]["content"]
    assert client.requests[1]["messages"][-1]["content"].startswith("TOOL_RESULT search_services: ")


def test_json_mode_retry_on_bad_format(monkeypatch):
    client = use(monkeypatch, [reply("好的，我來查一下"), reply('Sure: {"answer": "我們 06:00 開門。"} ok')], mode="json")
    text, trace, _ = agent.run_agent("幾點開？")
    assert text == "我們 06:00 開門。"
    assert client.requests[1]["messages"][-1]["content"] == agent.FORMAT_REMINDER
    assert "unparseable" in trace[0]["result"]["error"]


def test_json_mode_unknown_tool(monkeypatch):
    use(monkeypatch, [reply('{"tool": "hack", "args": {}}'), reply('{"answer": "抱歉"}')], mode="json")
    _, trace, _ = agent.run_agent("x")
    assert "unknown tool" in trace[0]["result"]["error"]


def test_parse_json_reply():
    assert agent.parse_json_reply('{"answer": "hi"}') == {"answer": "hi"}
    assert agent.parse_json_reply('text {"x": 1} then {"tool": "a", "args": {}}') == {"tool": "a", "args": {}}
    assert agent.parse_json_reply("no json") is None


# ---------- auto 偵測 ----------

def test_auto_mode_detects_native(monkeypatch):
    monkeypatch.setattr(agent, "TOOL_MODE", "auto")
    use(monkeypatch, [reply(tool_calls=[tool_call("get_weather", {"city": "台北"})])], mode=None)
    assert agent.get_tool_mode() == "native"


def test_auto_mode_falls_back_to_json(monkeypatch):
    monkeypatch.setattr(agent, "TOOL_MODE", "auto")
    use(monkeypatch, [reply("台北今天晴天")], mode=None)
    assert agent.get_tool_mode() == "json"


def test_auto_mode_bad_request_means_json(monkeypatch):
    monkeypatch.setattr(agent, "TOOL_MODE", "auto")
    req = httpx.Request("POST", "http://test")
    err = openai.BadRequestError("tools not supported", response=httpx.Response(400, request=req), body=None)
    use(monkeypatch, [err], mode=None)
    assert agent.get_tool_mode() == "json"


# ---------- prompt ----------

def test_system_prompt_has_date_and_rules():
    p = prompts.build_system_prompt(datetime(2026, 10, 26, 10, 0))
    assert "Today is 2026-10-26 (Monday)" in p and "2026-10-31 Saturday" in p
    assert "2026-11-02 Monday" in p and "2026-11-03" not in p  # 只列今天 + 7 天
    for section in ("Grounding rules", "Scope rules", "Routing rules", "Language and format rules"):
        assert section in p
    assert "assumptions" in p and "cheapest_plan" in p


# ---------- 串流、逾時、耗時、歷史截短 ----------

def test_stream_events(monkeypatch):
    use(monkeypatch, [
        reply(tool_calls=[tool_call("calculate_cost", {"service_keyword": "游泳", "quantity": 3, "identity": "student"})]),
        reply("學生票 3 張共 NT$240。"),
    ])
    events = list(agent.run_agent_stream("學生游泳 3 張多少？"))
    kinds = [e[0] for e in events]
    assert kinds[0] == "status" and events[0][1] == "正在計算費用…"
    assert "trace" in kinds and kinds[-1] == "done"
    assert "".join(e[1] for e in events if e[0] == "delta") == "學生票 3 張共 NT$240。"
    assert events[-1][1] == "學生票 3 張共 NT$240。"


def test_streamed_text_before_tool_call_is_reset(monkeypatch):
    use(monkeypatch, [
        reply("我查一下", tool_calls=[tool_call("get_center_info", {"topic": "parking"})]),
        reply("附近有公有停車場。"),
    ])
    kinds = [e[0] for e in agent.run_agent_stream("停車？")]
    assert kinds.index("reset") < kinds.index("status")


def test_timing_recorded_in_trace(monkeypatch):
    use(monkeypatch, [reply(tool_calls=[tool_call("get_center_info", {"topic": "parking"})]), reply("好")])
    _, trace, _ = agent.run_agent("停車？")
    assert "llm_sec" in trace[0] and "tool_sec" in trace[0]
    assert trace[-1]["event"] == "answer" and "llm_sec" in trace[-1]


def test_timeout_gives_friendly_reply(monkeypatch):
    use(monkeypatch, [openai.APITimeoutError(request=httpx.Request("POST", "http://test"))])
    text, trace, _ = agent.run_agent("你好")
    assert text == agent.TIMEOUT_REPLY and "(07) 625-0666" in text
    assert "timeout" in trace[-1]["result"]["error"]


def test_history_compacts_long_results_but_keeps_token(monkeypatch):
    args = {"class_id": "G01", "session_date": "2026-10-31", "member_name": "測試者", "phone": "0912345678"}
    use(monkeypatch, [
        reply(tool_calls=[tool_call("list_group_classes", {}, "c1"), tool_call("book_group_class", args, "c2")]),
        reply("請確認"),
    ])
    _, trace, history = agent.run_agent("這週有什麼課？順便報名週六空中瑜珈")
    full_list = json.dumps(trace[0]["result"], ensure_ascii=False)
    tool_msgs = [m for m in history if m["role"] == "tool"]
    assert len(full_list) > agent.HISTORY_TOOL_RESULT_CHARS
    assert len(tool_msgs[0]["content"]) < len(full_list)
    compact = json.loads(tool_msgs[0]["content"])
    assert {s["class_id"] for s in compact["sessions"]} >= {"G01", "G03"} and "name_en" not in json.dumps(compact)
    assert json.loads(tool_msgs[1]["content"])["confirm_token"] == trace[1]["result"]["confirm_token"]


def test_thinking_off_kwargs_sent(monkeypatch):
    monkeypatch.setattr(agent, "THINKING", "off_kwargs")
    client = use(monkeypatch, [reply("您好")])
    agent.run_agent("你好")
    assert client.requests[0]["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}


def test_thinking_default_sends_nothing(monkeypatch):
    client = use(monkeypatch, [reply("您好")])
    agent.run_agent("你好")
    assert "extra_body" not in client.requests[0]
    assert not client.requests[0]["messages"][0]["content"].startswith("/no_think")
