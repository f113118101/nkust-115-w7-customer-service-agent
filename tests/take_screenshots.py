"""自動截圖（需 LLM 與 Playwright）：啟動 app.py，用瀏覽器依截圖清單提問並存到 images/。

Playwright 只裝在本機（不在 requirements.txt）：
    pip install playwright && playwright install chromium
執行：
    python tests/take_screenshots.py                      # 全部截圖
    python tests/take_screenshots.py --only conv2,booking # 只重截某幾組
    python tests/take_screenshots.py --log-image docs/perf/x.log images/tool-choice-test.png
                                                          # 把終端機輸出的 log 轉成截圖

每題等「本輪總耗時」出現（回答完成、工具軌跡已更新）才截圖；每組之間清除對話並 sleep 4 秒。
每一步會檢查工具軌跡是否符合預期，不符合就整組重來（最多重試 2 次）。
"""
import argparse
import html
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parent.parent
IMAGES = ROOT / "images"
PORT = 7870
URL = f"http://127.0.0.1:{PORT}/"
SLEEP_SEC = 4
ANSWER_TIMEOUT_MS = 150_000
MAX_RETRIES = 2

# 每組：(組名, [(客人訊息, 截圖檔名或 None, 軌跡中必須出現的字串)])；同一組在同一段對話中進行
GROUPS = [
    ("conv1", [("學生游泳一次多少錢？", "conv1-in-data.png", ["80"])]),
    ("conv2", [("下週二下午 4 點租羽球場 2 小時多少錢？", "conv2-maths.png", ["calculate_cost", "600"])]),
    ("conv3", [("你們有賣運動飲料嗎？", "conv3-not-in-data.png", ["found", "transfer_to_human"])]),
    # 不該呼叫工具：軌跡裡只有「產生回答」那一步（tool 為 null），見 NO_TOOL_CHECK
    ("conv4", [("幫我寫一篇作文", "conv4-not-our-job.png", ["answer"])]),
    ("booking", [
        ("這週六的空中瑜珈還有名額嗎？成團了嗎？", "booking-0-check.png", ["list_group_classes", "G01"]),
        ("我要報名，我叫王小明，電話 0912345678", "booking-1-preview.png", ["book_group_class", "preview", "confirm_token"]),
        ("確認", "booking-2-confirmed.png", ["book_group_class", "booked", "booking_id"]),
        ("現在空中瑜珈報名幾個人了？成團了嗎？", "booking-3-count.png", ["list_group_classes"]),
        ("另外，我朋友周建宏要取消他這週二 TRX 的報名，報名編號 4，電話 0922000001",
         "booking-4-cancel-preview.png", ["cancel_booking", "preview", "confirm_token"]),
        ("確認", "booking-5-waitlist-promotion.png", ["cancel_booking", "cancelled", "promoted_from_waitlist"]),
    ]),
]
STREAMING_QUESTION = "這週有哪些團體課？"
TOOL_NAMES = ["get_center_info", "search_services", "list_group_classes", "find_private_slots", "calculate_cost",
              "compare_plans", "book_group_class", "book_private_session", "cancel_booking", "transfer_to_human"]
NO_TOOL_CHECK = {"conv4"}  # 這些組的軌跡中不可出現任何工具名稱


def start_app():
    env = dict(os.environ, GRADIO_SERVER_PORT=str(PORT), PYTHONIOENCODING="utf-8")
    log = open(ROOT / "docs" / "perf" / "screenshot_app.log", "w", encoding="utf-8")
    proc = subprocess.Popen([sys.executable, "app.py"], cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
    for _ in range(120):
        try:
            if urllib.request.urlopen(URL, timeout=2).status == 200:
                return proc
        except OSError:
            time.sleep(1)
    proc.kill()
    raise RuntimeError("app.py did not start")


def stop_app(proc):
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def text_of(page, selector):
    return page.locator(selector).first.inner_text()


def ask(page, question):
    """送出問題，等回答完成後回傳軌跡文字。

    兩段式等待：先等「本輪總耗時」消失（新的一輪已開始，app 第一次 yield 會清空 timing），
    再等它重新出現（這一輪完成）。只比對「文字是否改變」不可靠：Gradio 重畫同一內容時
    innerText 可能不同，會在新一輪開始前就誤判完成，截到上一輪的軌跡。
    """
    box = page.locator("#msg-box textarea").first
    box.fill(question)
    box.press("Enter")
    page.wait_for_function("() => !document.querySelector('#timing').innerText.includes('本輪總耗時')",
                           timeout=ANSWER_TIMEOUT_MS)
    page.wait_for_function("() => document.querySelector('#timing').innerText.includes('本輪總耗時')",
                           timeout=ANSWER_TIMEOUT_MS)
    page.wait_for_timeout(1500)  # 讓串流的最後一段與 JSON 畫完
    return text_of(page, "#trace") + "\n" + text_of(page, "#timing") + "\n" + text_of(page, "#chat")


def clear(page):
    page.locator("#clear-btn").first.click()
    page.wait_for_function("() => !document.querySelector('#timing').innerText.includes('本輪總耗時')")
    time.sleep(SLEEP_SEC)


def expand_trace(page):
    """工具軌跡與對話框預設有固定高度與捲軸；截圖前拿掉限制，整段 JSON 與整段對話才會入鏡。"""
    page.add_style_tag(content="#trace, #trace * { max-height: none !important; height: auto !important; "
                               "overflow: visible !important; }"
                               "#chat, #chat > div, #chat [class*='wrap'], #chat [role='log'] { max-height: none !important; "
                               "height: auto !important; overflow: visible !important; }")


def shoot(page, name, full_page=True):
    if full_page:
        expand_trace(page)
    path = IMAGES / name
    page.screenshot(path=str(path), full_page=full_page)
    print(f"  saved {path.relative_to(ROOT)}", flush=True)


def run_group(page, name, turns):
    for question, image, expect in turns:
        print(f"  ask: {question}", flush=True)
        trace = ask(page, question)
        reply_tail = text_of(page, "#chat").strip().replace("\n", " ")[-160:]
        print(f"  reply ends: …{reply_tail}", flush=True)
        missing = [e for e in expect if e not in trace]
        if name in NO_TOOL_CHECK:
            missing += [f"no tool expected, found {t}" for t in TOOL_NAMES if t in trace]
        if missing:
            debug = f"_debug_{name}_{turns.index((question, image, expect))}.png"
            shoot(page, debug)
            return f"turn '{question}': trace check failed {missing} (see images/{debug})"
        if image:
            shoot(page, image)
        if name == "conv1":
            shoot(page, "screenshot-main.png", full_page=False)
        time.sleep(SLEEP_SEC)
    return None


def shoot_streaming(page):
    """問一題會呼叫工具的問題，在「⏳ 正在查詢…」狀態出現時截圖。"""
    box = page.locator("#msg-box textarea").first
    box.fill(STREAMING_QUESTION)
    box.press("Enter")
    page.wait_for_function("() => document.querySelector('#chat').innerText.includes('正在查詢團課名額')",
                           timeout=ANSWER_TIMEOUT_MS)
    shoot(page, "streaming-status.png", full_page=False)
    page.wait_for_function("() => document.querySelector('#timing').innerText.includes('本輪總耗時')",
                           timeout=ANSWER_TIMEOUT_MS)


def render_log(log_path, out_path):
    """把終端機輸出（例如工具選擇測試）畫成像終端機的圖片。"""
    from playwright.sync_api import sync_playwright
    text = Path(log_path).read_text(encoding="utf-8")
    page_html = ("<html><body style='margin:0;background:#1e1e1e'><pre style='color:#d4d4d4;"
                 "font:14px Consolas,\"Microsoft JhengHei\",monospace;padding:16px;margin:0;white-space:pre'>"
                 f"$ python tests/run_tool_selection.py\n{html.escape(text)}</pre></body></html>")
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1600, "height": 400})
        page.set_content(page_html)
        page.screenshot(path=str(out_path), full_page=True)
        browser.close()
    print(f"saved {out_path}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="comma-separated groups: conv1,conv2,conv3,conv4,booking,streaming")
    ap.add_argument("--log-image", nargs=2, metavar=("LOG", "PNG"))
    opts = ap.parse_args()
    if opts.log_image:
        render_log(*opts.log_image)
        return
    from playwright.sync_api import sync_playwright

    only = set(opts.only.split(",")) if opts.only else None
    IMAGES.mkdir(exist_ok=True)
    failures = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        jobs = [(n, t) for n, t in GROUPS if not only or n in only]
        if not only or "streaming" in only:
            jobs.append(("streaming", None))
        for name, turns in jobs:
            for attempt in range(1, MAX_RETRIES + 2):
                print(f"[{name}] attempt {attempt}", flush=True)
                proc = start_app()  # 每組重啟 app：資料庫從 CSV 重建，預約流程從乾淨的資料開始
                try:
                    page = browser.new_page(viewport={"width": 1680, "height": 1050})
                    page.goto(URL)
                    page.wait_for_selector("#msg-box textarea")
                    if name == "streaming":
                        shoot_streaming(page)
                        error = None
                    else:
                        error = run_group(page, name, turns)
                    page.close()
                except Exception as e:  # 逾時等錯誤也算失敗，重試
                    error = f"{type(e).__name__}: {e}"
                finally:
                    stop_app(proc)
                if not error:
                    break
                print(f"  FAILED: {error}", flush=True)
                time.sleep(SLEEP_SEC)
            else:
                failures.append((name, error))
        browser.close()
    print("\nDone." if not failures else f"\nFailed groups: {failures}")


if __name__ == "__main__":
    main()
