"""Phase 0：檢查學校 iAI 端點與模型是否支援 native tool calling。

執行：python check_model.py [--model Furen-std] [--thinking off_kwargs]
"""
import argparse
import json
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import agent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", help="model to check (default: MODEL_NAME in .env)")
    ap.add_argument("--thinking", choices=["default", "off_kwargs", "off_prompt"])
    opts = ap.parse_args()
    if opts.model:
        agent.MODEL_NAME = opts.model
    if opts.thinking:
        agent.THINKING = opts.thinking
    print(f"Endpoint: {agent.LLM_API_URL}")
    print(f"Model:    {agent.MODEL_NAME}  (THINKING={agent.THINKING})")
    if not agent.API_KEY:
        print("API_KEY is not set. Copy .env.example to .env and fill in API_KEY.")
        return 1

    # 1. 列出可用模型
    try:
        models = [m.id for m in agent.get_client().models.list()]
        print(f"[1] Available models: {models}")
        if agent.MODEL_NAME not in models:
            print(f"    WARNING: {agent.MODEL_NAME} is not in the list")
    except Exception as e:  # 有些端點不開放 models 列表，不影響後續測試
        print(f"[1] Could not list models: {type(e).__name__}: {e}")

    # 2. 帶 tools 的請求
    try:
        supported, detail = agent.probe_native_tools()
    except agent.LLMError as e:
        print(f"[2] Request failed: {e}")
        return 1
    if not supported:
        print(f"[2] {detail}")
        print("NOT SUPPORTED, use TOOL_MODE=json")
        return 0
    print(f"[2] tool_calls: {detail['tool']}({detail['arguments']})")
    print("NATIVE TOOL CALLING: OK")

    # 3. 第二輪：把工具結果以 role:tool 回傳，確認模型能產生最終回答
    msg = detail["message"]
    messages = [
        {"role": "user", "content": agent.PROBE_QUESTION},
        {"role": "assistant", "content": msg.content or "", "tool_calls": [
            {"id": tc.id, "type": "function",
             "function": {"name": tc.function.name, "arguments": tc.function.arguments}} for tc in msg.tool_calls]},
    ] + [{"role": "tool", "tool_call_id": tc.id,
          "content": json.dumps({"city": "台北", "weather": "晴", "temp_c": 26}, ensure_ascii=False)}
         for tc in msg.tool_calls]
    try:
        final = agent.chat(messages, [agent.PROBE_TOOL]).choices[0].message
    except agent.LLMError as e:
        print(f"[3] Second round failed: {e}")
        print("Native first round works but role:tool round failed -> use TOOL_MODE=json")
        return 0
    if final.tool_calls:
        print(f"[3] Model called a tool again instead of answering: {final.tool_calls[0].function.name}")
    else:
        print(f"[3] Final answer: {final.content}")
        print("SECOND ROUND (role:tool): OK -> TOOL_MODE=native")
    return 0


if __name__ == "__main__":
    sys.exit(main())
