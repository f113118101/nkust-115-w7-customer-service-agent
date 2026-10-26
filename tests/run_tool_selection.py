"""工具選擇測試（需 LLM）：逐題跑 agent，記錄兩個指標與回應秒數。

指標 1（工具選擇）：「第一個」被呼叫的工具在 acceptable_tools 中（| 分隔；none = 不應呼叫工具）。
指標 2（計算正確）：有 expected_amount 的題目，計算工具（calculate_cost / compare_plans）回傳的金額
                    等於期望值，且回答中出現同一金額（代表金額來自工具，不是模型自己算）。

執行：python tests/run_tool_selection.py                     → docs/tool_selection_results.md
      python tests/run_tool_selection.py --cases tests/tool_selection_heldout.csv --out docs/tool_selection_heldout_results.md
      python tests/run_tool_selection.py --model Furen-std --out docs/perf/Furen-std_main.md
每題間 sleep 避免限流（sleep 不計入回應秒數）。預設繞過端點快取，量到的才是真實速度。
同時輸出同名 .json（完整 trace 與回答）供事後分析。
"""
import argparse
import csv
import json
import re
import sys
import time
import uuid
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import agent
import db

SLEEP_SEC = 4
CALC_TOOLS = ("calculate_cost", "compare_plans")


def amount_in_text(amount, text):
    plain = (text or "").replace(",", "").replace("，", "")
    return re.search(rf"(?<!\d){amount}(?!\d)", plain) is not None


def check_amount(expected, trace, reply):
    """回傳 (ok, 說明)。"""
    tool_amounts = []
    for t in trace:
        if t.get("tool") in CALC_TOOLS and isinstance(t.get("result"), dict):
            r = t["result"]
            for key in ("total", "cheapest_monthly_cost"):
                if isinstance(r.get(key), (int, float)):
                    tool_amounts.append(int(r[key]))
    if not tool_amounts:
        return False, "no calculation tool result"
    if expected not in tool_amounts:
        return False, f"tool returned {tool_amounts}, expected {expected}"
    if not amount_in_text(expected, reply):
        return False, f"tool returned {expected} but reply does not show it"
    return True, f"tool {expected} = reply"


def run_case(case):
    t0 = time.perf_counter()
    ttft, reply, trace = None, "", []
    for ev in agent.run_agent_stream(case["question"], []):
        if ev[0] == "delta" and ttft is None:
            ttft = time.perf_counter() - t0  # 第一段回答文字出現的時間
        elif ev[0] == "done":
            _, reply, trace, _ = ev
    sec = time.perf_counter() - t0
    return reply, trace, sec, sec if ttft is None else ttft


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", default=str(ROOT / "tests" / "tool_selection_cases.csv"))
    ap.add_argument("--out", default=str(ROOT / "docs" / "tool_selection_results.md"))
    ap.add_argument("--model", help="override MODEL_NAME for this run only")
    ap.add_argument("--thinking", choices=["default", "off_kwargs", "off_prompt"],
                    help="override THINKING for this run only")
    ap.add_argument("--use-cache", action="store_true",
                    help="allow the endpoint's response cache (timings become unrealistic)")
    opts = ap.parse_args()
    if opts.model:
        agent.MODEL_NAME = opts.model
    if opts.thinking:
        agent.THINKING = opts.thinking
    cases_path, out_path = Path(opts.cases), Path(opts.out)

    with open(cases_path, encoding="utf-8-sig", newline="") as f:
        cases = list(csv.DictReader(f))
    rows = []
    for i, case in enumerate(cases):
        if i:
            time.sleep(SLEEP_SEC)
        db.init_db()  # 每題從乾淨資料開始，避免前一題的預約影響結果
        if not opts.use_cache:
            agent.REQUEST_USER = f"bench-{uuid.uuid4().hex}"
        reply, trace, sec, ttft = run_case(case)
        called = [t["tool"] for t in trace if t.get("tool")]
        first = called[0] if called else "none"
        ok = first in case["acceptable_tools"].split("|")
        calc = check_amount(int(case["expected_amount"]), trace, reply) if case.get("expected_amount") else None
        llm_calls = len({t["step"] for t in trace if "llm_sec" in t})  # 同一 step 的多個工具共用一次 LLM 呼叫
        print(f"[{'PASS' if ok else 'FAIL'}] #{case['id']} {sec:5.1f}s (first text {ttft:5.1f}s) "
              f"{case['question']} -> {first} (all: {called})" + (f" calc: {calc[1]}" if calc else ""), flush=True)
        rows.append({"case": case, "first": first, "called": called, "ok": ok, "calc": calc, "reply": reply,
                     "trace": trace, "sec": sec, "ttft": ttft, "llm_calls": llm_calls})

    n = len(rows)
    passed = sum(r["ok"] for r in rows)
    calc_rows = [r for r in rows if r["calc"]]
    calc_passed = sum(r["calc"][0] for r in calc_rows)
    avg = sum(r["sec"] for r in rows) / n
    mx = max(r["sec"] for r in rows)
    avg_ttft = sum(r["ttft"] for r in rows) / n
    thinking = getattr(agent, "THINKING", "default")
    lines = [f"# Tool selection test results ({cases_path.name})\n",
             f"- Model: `{agent.MODEL_NAME}`, TOOL_MODE: `{agent.get_tool_mode()}`, THINKING: `{thinking}`",
             f"- Run at: {db.now():%Y-%m-%d %H:%M} (Asia/Taipei); endpoint cache "
             f"{'allowed' if opts.use_cache else 'bypassed (random user field per question)'}",
             f"- **Metric 1 – tool choice: {passed}/{n} = {passed / n:.0%}** (first tool called must be in acceptable_tools)"]
    if calc_rows:
        lines.append(f"- **Metric 2 – calculation: {calc_passed}/{len(calc_rows)}** (amount comes from "
                     f"calculate_cost / compare_plans, equals the expected amount, and is shown in the reply)")
    lines += [f"- **Response time: average {avg:.1f} s, max {mx:.1f} s; time to first answer text: average {avg_ttft:.1f} s**\n",
              "| # | Category | Question | Acceptable | First tool | All tools | LLM calls | Sec | First text | Tool choice | Calculation |",
              "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        c = r["case"]
        calc_cell = "" if not r["calc"] else ("✅ " if r["calc"][0] else "❌ ") + r["calc"][1]
        lines.append(f"| {c['id']} | {c['category']} | {c['question']} | {c['acceptable_tools'].replace('|', ' / ')} "
                     f"| `{r['first']}` | {', '.join(r['called']) or '-'} | {r['llm_calls']} | {r['sec']:.1f} "
                     f"| {r['ttft']:.1f} | {'✅' if r['ok'] else '❌'} | {calc_cell} |")
    lines.append("\n## Replies\n")
    for r in rows:
        c = r["case"]
        lines.append(f"**#{c['id']} {c['question']}** {'✅' if r['ok'] else '❌'}\n\n> "
                     + r["reply"].replace("\n", "\n> ") + "\n")
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    summary = {"model": agent.MODEL_NAME, "thinking": thinking, "cases": cases_path.name,
               "tool_choice": [passed, n], "calculation": [calc_passed, len(calc_rows)],
               "avg_sec": round(avg, 2), "max_sec": round(mx, 2), "avg_first_text_sec": round(avg_ttft, 2)}
    out_path.with_suffix(".json").write_text(
        json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(f"\nTool choice: {passed}/{n} = {passed / n:.0%}   Calculation: {calc_passed}/{len(calc_rows)}   "
          f"Average: {avg:.1f} s   Max: {mx:.1f} s   First text: {avg_ttft:.1f} s\nWrote {out_path}")


if __name__ == "__main__":
    main()
