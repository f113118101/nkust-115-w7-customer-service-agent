"""Phase 4 情境測試（需 LLM）：四類問題 + 完整預約流程，逐輪記錄工具軌跡。

執行：python tests/run_scenarios.py   → 輸出 docs/scenario_transcripts.md
      python tests/run_scenarios.py --model Furen-std --only B1,E,G --out docs/perf/C_Furen-std_scenarios.md
每輪之間 sleep，避免學校端點限流。
"""
import argparse
import json
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
OUT = ROOT / "docs" / "scenario_transcripts.md"

# (情境名稱, 類型, [依序送出的客人訊息])；同一情境共用對話歷史
SCENARIOS = [
    ("A1 營業時間", "In the data", ["營業時間是幾點到幾點？"]),
    ("A2 學生泳票", "In the data", ["學生游泳一次多少錢？"]),
    ("B1 羽球場跨尖峰", "Needs maths", ["這週二下午 4 點租羽球場 2 小時多少錢？"]),
    ("B2 健身房方案比較", "Needs maths", ["我每週去健身房 4 次、每次 2 小時，買月卡划算嗎？"]),
    ("C1 運動飲料", "Not in the data", ["你們有賣運動飲料嗎？"]),
    ("C2 寵物", "Not in the data", ["可以帶寵物進場嗎？"]),
    ("D1 股市", "Not our job", ["今天股市如何？"]),
    ("D2 作文", "Not our job", ["幫我寫一篇作文"]),
    ("E 團課預約→確認→查人數→候補→取消", "Booking flow", [
        "這週六的空中瑜珈還有名額嗎？成團了嗎？",
        "我要報名，我叫王小明，電話 0912345678",
        "確認",
        "現在空中瑜珈報名幾個人了？成團了嗎？",
        "我也想報名這週二的 TRX，額滿的話候補也可以，一樣是王小明 0912345678",
        "確認",
        "我要取消剛剛空中瑜珈的報名",
        "確認",
    ]),
    ("F 取消已報名者 → 候補遞補", "Booking flow", [
        "我是周建宏，電話 0922000001，我要取消這週二 TRX 的報名，報名編號 4",
        "確認",
        "TRX 現在還有人在候補嗎？",
    ]),
    ("G 私人教練預約", "Booking flow", [
        "我想預約明天下午的私人教練",
        "14:00 好了，我叫陳大文，電話 0987654321",
        "對，確認",
    ]),
]


def short(obj, limit=600):
    s = json.dumps(obj, ensure_ascii=False)
    return s if len(s) <= limit else s[:limit] + " …"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model")
    ap.add_argument("--thinking", choices=["default", "off_kwargs", "off_prompt"])
    ap.add_argument("--only", help="comma-separated scenario ids, e.g. B1,E,G")
    ap.add_argument("--out", default=str(OUT))
    opts = ap.parse_args()
    if opts.model:
        agent.MODEL_NAME = opts.model
    if opts.thinking:
        agent.THINKING = opts.thinking
    agent.REQUEST_USER = f"scenario-{uuid.uuid4().hex}"  # 繞過端點快取
    only = set(opts.only.split(",")) if opts.only else None
    out = Path(opts.out)

    db.init_db()
    lines = [f"# Scenario transcripts\n", f"- Model: `{agent.MODEL_NAME}`, TOOL_MODE: `{agent.get_tool_mode()}`, "
             f"THINKING: `{agent.THINKING}`, MAX_STEPS: {agent.MAX_STEPS}",
             f"- Run at: {db.now():%Y-%m-%d %H:%M} (Asia/Taipei)\n"]
    first = True
    for title, kind, turns in SCENARIOS:
        if only and title.split()[0] not in only:
            continue
        lines.append(f"\n## {title} ({kind})\n")
        print(f"\n=== {title} ===")
        history = []
        for user_msg in turns:
            if not first:
                time.sleep(SLEEP_SEC)
            first = False
            reply, trace, history = agent.run_agent(user_msg, history)
            print(f"客人: {user_msg}")
            lines.append(f"**客人：** {user_msg}\n")
            tool_steps = [t for t in trace if t.get("tool")]
            for t in tool_steps:
                print(f"  [tool] {t['tool']} {short(t['args'], 200)}")
                lines.append(f"- 🔧 `{t['tool']}` args=`{short(t['args'], 300)}`\n  - result: `{short(t['result'])}`")
            if not tool_steps:
                lines.append("- 🔧 (no tool called)")
            print(f"客服: {reply}")
            lines.append(f"\n**客服：** {reply}\n")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nWrote {out}")


if __name__ == "__main__":
    main()
