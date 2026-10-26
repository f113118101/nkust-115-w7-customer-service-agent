"""岡山運動中心 AI 客服：Gradio 入口。執行：python app.py"""
import inspect
import time

import gradio as gr

import agent
import db

GREETING = ("您好！我是岡山運動中心的 AI 客服 🏊‍♀️🏸\n"
            "我可以幫您：\n"
            "- 查詢營業時間、交通、場館規定\n"
            "- 查票價、算場地租借與課程費用、比較月卡與回數票\n"
            "- 查團體課名額、預約或取消團體課與私人教練課\n"
            "請問今天想了解什麼呢？")

EXAMPLES = [
    "學生游泳一次多少錢？",
    "平日晚上 6 點租羽球場 2 小時多少？",
    "我每週去健身房 4 次、每次 2 小時，買月卡划算嗎？",
    "這週六的空中瑜珈還有名額嗎？成團了嗎？",
    "我想預約明天下午的私人教練",
    "你們有賣運動護具嗎？",
    "幫我寫一篇作文",
]


def _chatbot(**kwargs):
    # Gradio 5 需指定 type="messages"；Gradio 6 已移除此參數（只支援 messages 格式）
    if "type" in inspect.signature(gr.Chatbot.__init__).parameters:
        kwargs["type"] = "messages"
    return gr.Chatbot(**kwargs)


def status_text():
    mode = agent._tool_mode or (agent.TOOL_MODE if agent.TOOL_MODE != "auto" else "auto（尚未偵測）")
    key = "已設定" if agent.API_KEY else "未設定"
    return f"**Model:** `{agent.MODEL_NAME}`　**TOOL_MODE:** `{mode}`　**MAX_STEPS:** `{agent.MAX_STEPS}`　**API_KEY:** {key}"


def timing_text(trace, total_sec=None):
    """每一步的 LLM / 工具耗時表。"""
    rows = [t for t in trace if "llm_sec" in t]
    if not rows:
        return ""
    lines = ["| step | tool | LLM 秒 | 工具秒 |", "|---|---|---|---|"]
    seen = set()
    for t in rows:
        # 同一次 LLM 呼叫產生多個工具時，LLM 秒數只列在第一個
        llm = "" if t["step"] in seen else f"{t['llm_sec']:.2f}"
        seen.add(t["step"])
        name = f"`{t['tool']}`" if t.get("tool") else "（產生回答）"
        tool_sec = f"{t['tool_sec']:.3f}" if "tool_sec" in t else ""
        lines.append(f"| {t['step']} | {name} | {llm} | {tool_sec} |")
    if total_sec is not None:
        lines.append(f"\n**本輪總耗時：{total_sec:.1f} 秒**")
    return "\n".join(lines)


def respond(user_msg, chat_history, llm_history):
    """generator：先顯示客人訊息，工具執行時更新狀態與軌跡，最後串流回答。"""
    user_msg = (user_msg or "").strip()
    if not user_msg:
        yield "", chat_history, llm_history, gr.update(), gr.update(), status_text()
        return
    t0 = time.perf_counter()
    bot = {"role": "assistant", "content": "⏳ 思考中…"}
    chat_history = chat_history + [{"role": "user", "content": user_msg}, bot]
    trace, answer = [], ""
    yield "", chat_history, llm_history, [], "", status_text()

    for ev in agent.run_agent_stream(user_msg, llm_history):
        kind = ev[0]
        if kind == "status":
            bot["content"] = f"⏳ {ev[1]}"
        elif kind == "trace":
            trace = ev[1]
        elif kind == "delta":
            answer += ev[1]
            bot["content"] = answer.lstrip()
        elif kind == "reset":
            answer = ""
            bot["content"] = "⏳ 思考中…"
        elif kind == "done":
            _, reply, trace, llm_history = ev
            bot["content"] = reply
            yield ("", chat_history, llm_history, trace or [{"note": "no tool called"}],
                   timing_text(trace, time.perf_counter() - t0), status_text())
            return
        yield "", chat_history, llm_history, trace, timing_text(trace), status_text()


def _example_handler(question):
    def handler(chat_history, llm_history):
        yield from respond(question, chat_history, llm_history)
    return handler


def clear():
    return [{"role": "assistant", "content": GREETING}], [], [], "", status_text()


def build_ui():
    with gr.Blocks(title="岡山運動中心 AI 客服") as demo:
        gr.Markdown("# 🏟️ 岡山運動中心 AI 客服\n高雄市岡山區中山南路169號 · 服務電話 (07) 625-0666")
        llm_history = gr.State([])
        with gr.Row():
            with gr.Column(scale=3):
                chatbot = _chatbot(value=[{"role": "assistant", "content": GREETING}], height=520, label="對話")
                with gr.Row():
                    msg = gr.Textbox(placeholder="請輸入您的問題，例如：週六早上租羽球場 2 小時多少錢？",
                                     show_label=False, scale=5)
                    send = gr.Button("送出", variant="primary", scale=1)
                gr.Markdown("**範例問題**（點一下直接發問）")
                example_buttons = [gr.Button(q, size="sm") for q in EXAMPLES]
                clear_btn = gr.Button("清除對話")
            with gr.Column(scale=2):
                gr.Markdown("### 🔧 Tools used")
                status = gr.Markdown(status_text())
                timing = gr.Markdown("")
                trace_view = gr.JSON(label="本輪工具軌跡（tool / args / result / 秒數）", value=[])

        outputs = [msg, chatbot, llm_history, trace_view, timing, status]
        send.click(respond, [msg, chatbot, llm_history], outputs)
        msg.submit(respond, [msg, chatbot, llm_history], outputs)
        for btn, q in zip(example_buttons, EXAMPLES):
            btn.click(_example_handler(q), [chatbot, llm_history], outputs)
        clear_btn.click(clear, None, [chatbot, llm_history, trace_view, timing, status])
    return demo


def detect_mode_at_startup():
    """TOOL_MODE=auto 時啟動就偵測一次，第一位客人不用等這個測試請求。"""
    if not agent.API_KEY or agent.TOOL_MODE != "auto":
        return
    try:
        print(f"TOOL_MODE=auto → detected: {agent.get_tool_mode()}")
    except agent.LLMError as e:  # 偵測失敗（例如網路問題）就延到第一題再試
        print(f"TOOL_MODE detection failed, will retry on first question: {e}")


if __name__ == "__main__":
    db.init_db()
    detect_mode_at_startup()
    build_ui().launch()
