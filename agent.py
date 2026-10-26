"""Agent 迴圈 + LLM 介面。

兩種工具模式：
- native：標準 OpenAI tools / tool_calls / role:"tool" 流程，最終回答用 stream=True 串流
- json：模型不支援 tools 時，在 system prompt 附工具清單，要求模型每次只輸出一行 JSON
TOOL_MODE=auto 時偵測一次並快取在 _tool_mode（app.py 啟動時就先偵測）。

run_agent_stream() 是 generator，邊執行邊 yield 事件給 UI；run_agent() 收集事件後一次回傳。
"""
import json
import os
import time

from dotenv import load_dotenv
from openai import APIConnectionError, APIStatusError, APITimeoutError, BadRequestError, OpenAI, RateLimitError

import prompts
import tools

load_dotenv()


def _env(name, default):
    # 容許 .env 中寫成「TOOL_MODE=auto  # auto | native | json」
    return (os.getenv(name) or default).split("#")[0].strip()


LLM_API_URL = _env("LLM_API_URL", "https://www.iai.nkust.edu.tw/aihub/v1/")
MODEL_NAME = _env("MODEL_NAME", "Furen-large")
API_KEY = _env("API_KEY", "")
TOOL_MODE = _env("TOOL_MODE", "auto").lower()
MAX_STEPS = int(_env("MAX_STEPS", "6"))
# 思考模式：default（模型預設，Furen 系列會先產生 reasoning_content）
#           off_kwargs（extra_body chat_template_kwargs.enable_thinking=False，實測有效）
#           off_prompt（system prompt 開頭加 /no_think，實測對 Furen-large 無效，保留供比較）
THINKING = _env("THINKING", "default").lower()
LLM_TIMEOUT_SEC = 30
MAX_HISTORY_TURNS = 6  # 送給模型的歷史最多保留最近幾輪（以客人訊息為界，不會切斷工具呼叫）
HISTORY_TOOL_RESULT_CHARS = 600  # 放進歷史的工具結果超過這個長度就截短
RETRY_WAITS = (2, 4, 8)  # 429 退避秒數
# 學校端點會快取「完全相同」的請求（約 0.03 秒回傳）。量測速度時由測試腳本設成隨機字串，
# 放在 OpenAI 的 user 欄位讓每題都真的被模型計算；app 本身不設定。
REQUEST_USER = None

BUSY_REPLY = "抱歉，系統忙碌，請稍後再試或改撥 (07) 625-0666。"
ERROR_REPLY = "抱歉，系統暫時無法回應，請稍後再試或改撥 (07) 625-0666。"
TIMEOUT_REPLY = "抱歉，系統回應逾時，請稍後再試，或撥打 (07) 625-0666 由專人為您服務。"
NO_KEY_REPLY = "系統尚未設定 API 金鑰（.env 的 API_KEY），目前無法回答，請洽 (07) 625-0666。"
FORMAT_REMINDER = ('FORMAT ERROR. Reply with exactly one line of JSON: {"tool": "<name>", "args": {...}} '
                   'or {"answer": "<message>"}. No other text.')

# 呼叫工具時顯示在聊天框的狀態
TOOL_STATUS = {
    "get_center_info": "正在查詢場館資訊…",
    "search_services": "正在查詢服務與價格…",
    "list_group_classes": "正在查詢團課名額…",
    "find_private_slots": "正在查詢教練空檔…",
    "calculate_cost": "正在計算費用…",
    "compare_plans": "正在比較方案…",
    "book_group_class": "正在處理團課報名…",
    "book_private_session": "正在處理私人教練預約…",
    "cancel_booking": "正在處理取消…",
    "transfer_to_human": "正在為您轉接專人…",
}

# 截短工具結果時保留的欄位（confirm_token 一定要留，下一輪確認才拿得到）
HISTORY_KEEP_KEYS = {
    "found", "status", "error", "reason", "confirm_token", "expires_in_min", "booking_id", "booking_type",
    "enrollment_status", "waitlist_position", "total", "cheapest_plan", "cheapest_monthly_cost",
    "sessions", "coaches", "results", "summary", "class_id", "coach_id", "service_id", "name", "class",
    "coach", "date", "session_date", "start_time", "slots", "price", "enrolled", "spots_left", "waitlist",
    "is_confirmed", "students_needed_to_run", "will_be", "member_name", "phone",
}

_client = None
_tool_mode = None


class LLMError(Exception):
    pass


def get_client():
    global _client
    if _client is None:
        if not API_KEY:
            raise LLMError("API_KEY is not set")
        # 重試由 chat() 自己處理（429 退避），關掉 SDK 內建重試
        _client = OpenAI(base_url=LLM_API_URL, api_key=API_KEY, timeout=LLM_TIMEOUT_SEC, max_retries=0)
    return _client


def _wrap_error(e):
    if isinstance(e, APITimeoutError):
        return LLMError(f"timeout after {LLM_TIMEOUT_SEC}s")
    return LLMError(f"{type(e).__name__}: {e}")


def chat(messages, tool_schemas=None, stream=False):
    """呼叫 LLM；429 依 2s、4s、8s 退避重試，其餘錯誤轉成 LLMError。"""
    kwargs = {"model": MODEL_NAME, "messages": messages, "temperature": 0}
    if REQUEST_USER:
        kwargs["user"] = REQUEST_USER
    if THINKING == "off_kwargs":
        kwargs["extra_body"] = {"chat_template_kwargs": {"enable_thinking": False}}
    if tool_schemas:
        kwargs["tools"] = tool_schemas
        kwargs["tool_choice"] = "auto"
    if stream:
        kwargs["stream"] = True
    for attempt in range(len(RETRY_WAITS) + 1):
        try:
            return get_client().chat.completions.create(**kwargs)
        except RateLimitError as e:
            if attempt == len(RETRY_WAITS):
                raise LLMError(f"rate limited (429) after {attempt} retries") from e
            time.sleep(RETRY_WAITS[attempt])
        except (APIStatusError, APIConnectionError) as e:
            raise _wrap_error(e) from e


def _stream_message(messages, tool_schemas):
    """串流呼叫：邊收邊 yield ("delta", 文字)；結束時 yield ("message", content, tool_calls)。

    tool_calls 的片段依 index 拼回完整的 id / name / arguments。
    """
    content, calls = "", {}
    try:
        for chunk in chat(messages, tool_schemas, stream=True):
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            for tc in delta.tool_calls or []:
                c = calls.setdefault(tc.index, {"id": "", "name": "", "arguments": ""})
                c["id"] = tc.id or c["id"]
                if tc.function and tc.function.name and not c["name"]:
                    c["name"] = tc.function.name
                if tc.function and tc.function.arguments:
                    c["arguments"] += tc.function.arguments
            if delta.content:
                content += delta.content
                if not calls:
                    yield ("delta", delta.content)
    except (APIStatusError, APIConnectionError) as e:
        raise _wrap_error(e) from e
    tool_calls = [{"id": c["id"] or f"call_{i}", "name": c["name"], "arguments": c["arguments"]}
                  for i, c in sorted(calls.items())]
    yield ("message", content, tool_calls)


# ---------- 偵測模型是否支援 native tool calling ----------

PROBE_TOOL = {"type": "function", "function": {
    "name": "get_weather", "description": "Get the current weather of a city.",
    "parameters": {"type": "object", "properties": {"city": {"type": "string", "description": "City name"}},
                   "required": ["city"]}}}
PROBE_QUESTION = "台北天氣如何？請用工具查詢。"


def probe_native_tools():
    """回傳 (supported, detail)。supported=True 表示模型會回傳 tool_calls。"""
    try:
        resp = chat([{"role": "user", "content": PROBE_QUESTION}], [PROBE_TOOL])
    except LLMError as e:
        if isinstance(e.__cause__, BadRequestError):
            return False, f"endpoint rejected tools: {e}"
        raise
    msg = resp.choices[0].message
    if msg.tool_calls:
        tc = msg.tool_calls[0]
        return True, {"tool": tc.function.name, "arguments": tc.function.arguments, "id": tc.id, "message": msg}
    return False, f"no tool_calls in reply: {msg.content!r}"


def get_tool_mode():
    global _tool_mode
    if _tool_mode is None:
        if TOOL_MODE in ("native", "json"):
            _tool_mode = TOOL_MODE
        else:
            supported, _ = probe_native_tools()
            _tool_mode = "native" if supported else "json"
    return _tool_mode


# ---------- JSON 模式解析 ----------

def parse_json_reply(text):
    """容錯解析：去掉 ```json 圍欄，取第一個能解析且含 tool 或 answer 的 {...}。"""
    if not text:
        return None
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    decoder = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch != "{":
            continue
        try:
            obj, _ = decoder.raw_decode(text, i)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and ("tool" in obj or "answer" in obj):
            return obj
    return None


# ---------- 歷史 ----------

def _is_customer_turn(m):
    # json 模式的工具結果與格式提醒也是 role:user，但不算客人的一輪
    content = m.get("content") or ""
    return m.get("role") == "user" and not content.startswith("TOOL_RESULT ") and content != FORMAT_REMINDER


def _trim_history(history):
    """只保留最近 MAX_HISTORY_TURNS 輪；從客人訊息切，避免留下孤立的 tool 訊息。"""
    user_idx = [i for i, m in enumerate(history) if _is_customer_turn(m)]
    if len(user_idx) <= MAX_HISTORY_TURNS:
        return list(history)
    return list(history[user_idx[-MAX_HISTORY_TURNS]:])


def compact_result(obj):
    """只保留 HISTORY_KEEP_KEYS 中的欄位（遞迴處理 list / dict）。"""
    if isinstance(obj, dict):
        return {k: compact_result(v) for k, v in obj.items() if k in HISTORY_KEEP_KEYS}
    if isinstance(obj, list):
        return [compact_result(x) for x in obj]
    return obj


def _compact_text(text):
    if len(text) <= HISTORY_TOOL_RESULT_CHARS:
        return text
    try:
        return json.dumps(compact_result(json.loads(text)), ensure_ascii=False)
    except json.JSONDecodeError:
        return text[:HISTORY_TOOL_RESULT_CHARS] + "…"


def _history_message(m):
    """工具結果放進歷史前截短；本輪送給模型的仍是完整結果。"""
    if m.get("role") == "tool":
        return {**m, "content": _compact_text(m["content"])}
    if m.get("role") == "user" and (m.get("content") or "").startswith("TOOL_RESULT "):
        head, _, body = m["content"].partition(": ")
        return {**m, "content": f"{head}: {_compact_text(body)}"}
    return m


# ---------- Agent 迴圈 ----------

def _parse_args(raw):
    if isinstance(raw, dict):
        return raw
    try:
        args = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return None
    return args if isinstance(args, dict) else None


def _run_tool(step, name, args, trace, llm_sec):
    t0 = time.perf_counter()
    result = tools.execute_tool(name, args)
    trace.append({"step": step, "tool": name, "args": args, "result": result,
                  "llm_sec": llm_sec, "tool_sec": round(time.perf_counter() - t0, 3)})
    return result


def _answer_event(step, llm_sec):
    # 最後一次產生回答的 LLM 呼叫也記錄耗時（tool 為 None，不算工具呼叫）
    return {"step": step, "tool": None, "event": "answer", "llm_sec": llm_sec}


def _native_loop(messages, trace, out):
    """out 是 dict，用來把最終回答傳回呼叫端（generator 不方便 return 值給 for 迴圈）。"""
    for step in range(1, MAX_STEPS + 1):
        t0 = time.perf_counter()
        content, tool_calls, streamed = "", [], False
        for ev in _stream_message(messages, tools.TOOL_SCHEMAS):
            if ev[0] == "delta":
                if not streamed and not ev[1].strip():
                    continue  # 略過開頭的空白行
                streamed = True
                yield ev
            else:
                _, content, tool_calls = ev
        llm_sec = round(time.perf_counter() - t0, 2)
        if not tool_calls:
            text = content.strip()
            messages.append({"role": "assistant", "content": text})
            trace.append(_answer_event(step, llm_sec))
            out["reply"] = text
            return
        if streamed:
            yield ("reset",)  # 模型先說了話又呼叫工具：清掉已顯示的文字
        messages.append({"role": "assistant", "content": content or "", "tool_calls": [
            {"id": tc["id"], "type": "function", "function": {"name": tc["name"], "arguments": tc["arguments"]}}
            for tc in tool_calls]})
        for tc in tool_calls:
            yield ("status", TOOL_STATUS.get(tc["name"], "正在查詢…"))
            args = _parse_args(tc["arguments"])
            if args is None:
                result = {"error": "arguments are not valid JSON"}
                trace.append({"step": step, "tool": tc["name"], "args": tc["arguments"], "result": result,
                              "llm_sec": llm_sec})
            else:
                result = _run_tool(step, tc["name"], args, trace, llm_sec)
            yield ("trace", list(trace))
            messages.append({"role": "tool", "tool_call_id": tc["id"], "content": json.dumps(result, ensure_ascii=False)})


def _json_loop(messages, trace, out):
    retried = False
    for step in range(1, MAX_STEPS + 1):
        t0 = time.perf_counter()
        text = chat(messages).choices[0].message.content or ""
        llm_sec = round(time.perf_counter() - t0, 2)
        messages.append({"role": "assistant", "content": text})
        obj = parse_json_reply(text)
        if obj is None:
            if retried:
                # 連續兩次格式錯誤：若有文字就當成回答，總比沒有回應好
                trace.append(_answer_event(step, llm_sec))
                out["reply"] = text.strip() or None
                return
            retried = True
            trace.append({"step": step, "tool": None, "args": None, "llm_sec": llm_sec,
                          "result": {"error": "unparseable reply, asked model to retry"}})
            messages.append({"role": "user", "content": FORMAT_REMINDER})
            continue
        if "answer" in obj:
            trace.append(_answer_event(step, llm_sec))
            out["reply"] = str(obj["answer"])
            yield ("delta", out["reply"])
            return
        name = str(obj.get("tool"))
        yield ("status", TOOL_STATUS.get(name, "正在查詢…"))
        result = _run_tool(step, name, obj.get("args") or {}, trace, llm_sec)
        yield ("trace", list(trace))
        messages.append({"role": "user", "content": f"TOOL_RESULT {name}: {json.dumps(result, ensure_ascii=False)}"})


def run_agent_stream(user_msg, history=None):
    """執行一輪對話（generator）。

    history 是先前的 LLM 訊息（含工具呼叫與結果，不含 system），讓下一輪拿得到 confirm_token。
    yield 事件：
      ("status", 文字)  正在呼叫工具
      ("trace", trace)  工具軌跡更新
      ("delta", 文字)   最終回答的串流片段
      ("reset",)        先前串流的文字作廢
      ("done", reply, trace, new_history)
    """
    trace = []
    history = _trim_history(history or [])
    if not API_KEY:
        trace.append({"step": 0, "tool": None, "args": None, "result": {"error": "API_KEY is not set"}})
        yield ("done", NO_KEY_REPLY, trace, history)
        return
    out = {"reply": None}
    try:
        mode = get_tool_mode()
        system = prompts.build_system_prompt()
        if THINKING == "off_prompt":
            system = "/no_think\n" + system
        if mode == "json":
            system += prompts.build_json_mode_instructions(tools.TOOL_SCHEMAS)
        messages = [{"role": "system", "content": system}] + history + [{"role": "user", "content": user_msg}]
        loop = _native_loop if mode == "native" else _json_loop
        yield from loop(messages, trace, out)
    except LLMError as e:
        trace.append({"step": len(trace) + 1, "tool": None, "args": None, "result": {"error": str(e)}})
        msg = str(e)
        reply = BUSY_REPLY if "429" in msg else TIMEOUT_REPLY if "timeout" in msg else ERROR_REPLY
        yield ("done", reply, trace, history)
        return
    reply = out["reply"]
    if reply is None:
        trace.append({"step": MAX_STEPS, "tool": None, "args": None,
                      "result": {"error": f"stopped after max_steps={MAX_STEPS}"}})
        reply = BUSY_REPLY
        messages.append({"role": "assistant", "content": reply})
    yield ("done", reply, trace, [_history_message(m) for m in messages[1:]])


def run_agent(user_msg, history=None):
    """非串流版本：回傳 (reply, trace, new_history)。測試腳本使用。"""
    for ev in run_agent_stream(user_msg, history):
        if ev[0] == "done":
            return ev[1], ev[2], ev[3]
