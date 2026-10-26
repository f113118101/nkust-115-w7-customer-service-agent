# Test results and fix log

Model: `Furen-large` (NKUST iAI), native tool calling (`check_model.py`: `NATIVE TOOL CALLING: OK`, second round with `role: tool` OK).
Test date: 2026-10-26 (Asia/Taipei).

## 1. Unit tests (no LLM)

`python -m pytest tests -v` → **100 passed**

| File | Tests | Covers |
|---|---|---|
| `tests/test_tools.py` | 83 | every info topic, aliases (游泳 / gym / 空瑜 / 重訓), unavailable service (S16) has no price and cannot be priced, court peak/off-peak split (weekday 16:00 ×2h = 600, holiday 09:00 ×2h = 600), identity prices and errors, plan comparison, group class min/max/waitlist/promotion, two-step confirm for book and cancel (no token / wrong token / expired / changed payload / reused / token of another action), private coach availability, conflicts and date rules, registry (unknown tool, missing / wrong-type args) |
| `tests/test_agent.py` | 17 | agent loop with a fake LLM: native and JSON modes, unknown tool refused, `max_steps` stops the loop, 429 back-off (2 s, 4 s) and give-up, invalid tool arguments, confirm_token carried to the next turn through history, history trimming at turn boundaries, auto mode detection, system prompt contains date and the four rule groups |

## 2. Scenario tests with the real model

Script: `tests/run_scenarios.py` (same code path as the Gradio UI, 4 s sleep between turns).
Latest transcript with full tool trace: [`scenario_transcripts.md`](scenario_transcripts.md); first run: [`scenario_transcripts_run1.md`](scenario_transcripts_run1.md).

| Scenario | Kind | Tools called (final run) | Result |
|---|---|---|---|
| A1 營業時間是幾點到幾點？ | In the data | `get_center_info(opening_hours)` | 06:00–22:00 ✅ |
| A2 學生游泳一次多少錢？ | In the data | `search_services(游泳)` | NT$80, needs student ID ✅ |
| B1 這週二下午 4 點租羽球場 2 小時多少錢？ | Needs maths | `calculate_cost(羽球場, 2026-10-27, 16:00, 2h)` | 250 off-peak + 350 peak = NT$600 ✅ |
| B2 每週健身房 4 次、每次 2 小時，買月卡划算嗎？ | Needs maths | `compare_plans(gym, 16, 2)` | single 1,600 / ticket book 1,280 / monthly 1,250 → monthly pass, with the assumptions sentence ✅ |
| C1 你們有賣運動飲料嗎？ | Not in the data | `search_services` → found:false → `transfer_to_human` | "no information" + hand-over ✅ |
| C2 可以帶寵物進場嗎？ | Not in the data | `get_center_info` ×3 → `transfer_to_human` | "no information" + hand-over ✅ |
| D1 今天股市如何？ | Not our job | (none) | polite refusal, back to the center ✅ |
| D2 幫我寫一篇作文 | Not our job | (none) | polite refusal ✅ |
| E group class booking flow (8 turns) | Booking | `list_group_classes` → `book_group_class` preview → confirm (booking #26, 3→4 people, class now runs) → `list_group_classes` (4 people) → TRX full → waitlist preview → confirm (waitlist #2) → `cancel_booking` preview → confirm (aerial yoga back to 3, short by 1) | ✅ |
| F cancel an enrolled member → waitlist promotion | Booking | `cancel_booking` preview → confirm → `曾*琪` moved from waitlist to enrolled → `list_group_classes` shows 8/8 with 1 waiting | ✅ |
| G private coach booking | Booking | `find_private_slots(2026-10-27)` → `book_private_session` preview → confirm (booking #1, NT$1,200) | ✅ |

### Problems found in the scenario runs and what we changed

| Run | Problem | Cause | Change | Result |
|---|---|---|---|---|
| 1 | C1 "sports drinks": answered "no information" **without calling any tool** and without hand-over | Routing did not cover "do you sell X" | Routing rule + `search_services` description: also use it to check whether the center sells or offers something; grounding rule: never say "we don't have X" without a tool result | Run 2: called `search_services` ✅ but still no hand-over |
| 1 | E/G: invented procedures ("bring ID and the SMS", "we will notify you by SMS", "check in with the booking number") | Model filled in typical booking text | Grounding rule: do not add procedures, documents, notifications or policies that are not in a tool result; keep the exact meaning of any rule | Run 2/3: no invented SMS / ID steps ✅ |
| 1 | G: cancellation policy restated with the opposite meaning ("contact us within 24 hours") | Same as above | Same rule ("keep its exact meaning") | Run 3: "課程前24小時內取消視為已使用" ✅ |
| 1 | E: raw field names shown to the customer (`is_confirmed：false`, `waitlist_will_be_promoted：false`) | Model copied JSON keys | Language/format rule: never show tool names, JSON field names or true/false; say 已成團 / 尚差 1 人成團 / 取消後會由候補遞補 | Run 2/3: no field names ✅ |
| 1 | F: "is anyone still waiting?" answered from the previous turn's result without a tool call | Model reused old data | Grounding rule: seats, counts, waitlists and slots change; call the tool again for each new status question | Run 2/3: calls `list_group_classes` again ✅ |
| 2 | C1: `search_services` found:false, but the reply said "我們目前沒有販售" (we don't sell it) and suggested a convenience store, with no hand-over | found:false read as "the center has none" | `found:false` results of `search_services` / `calculate_cost` now carry `next_step` ("not in our data … call transfer_to_human"); grounding rule: found:false means "not in our data", never guess alternatives | Run 3: `search_services` → `transfer_to_human` ✅ |
| 2 | E: TRX step drifted by one turn (model checked the class and asked before the preview, so the scripted "確認" only produced the preview) | Model asked for consent first (acceptable behaviour) | Test message made explicit: "額滿的話候補也可以" | Run 3: preview → confirm → waitlist #2 ✅ |

## 3. Tool selection test

Script: `tests/run_tool_selection.py`, cases: `tests/tool_selection_cases.csv` (24 questions, 4 s sleep between questions, fresh database for each question). The **first** tool called must be in `acceptable_tools` (`none` = no tool should be called).

| Run | Accuracy | Failed cases |
|---|---|---|
| 1 | **23/24 = 96%** | #20 你們有賣泳鏡嗎？ → no tool called, answered "沒有販售泳鏡的資訊" and offered other help (treated as out of scope) |
| 2 | **24/24 = 100%** | none |

Full table of the latest run: [`tool_selection_results.md`](tool_selection_results.md); first run: [`tool_selection_results_run1.md`](tool_selection_results_run1.md).

### Fix after run 1

- **What was wrong:** #20 "do you sell swimming goggles?" was treated as an out-of-scope question, so no tool was called and there was no hand-over. The scope rule ("decline anything else") and the grounding rule ("not found → hand over") overlapped for questions about things the center might sell.
- **What we changed:** the Scope rules now say that any question about what this center sells, offers, rents or allows (goggles, drinks, lockers, pets …) is in scope and must be looked up; only topics unrelated to the center are declined. We did not add goggles to the data, because we have no real information about a shop at the center: this is a "not in the data" question and the right answer is a hand-over.
- **Result:** run 2 scored 24/24. #20 now calls `search_services` (found:false) and then `transfer_to_human`, and the reply says "我目前沒有關於泳鏡銷售的資訊" and points to staff. The three real out-of-scope questions (#22–#24) are still declined without tools.

## 4. Held-out test (5 new questions)

Five questions that never appeared in any earlier test or prompt fix, one per kind: maths, booking, not in the data, not our job, mixed Chinese/English. File: `tests/tool_selection_heldout.csv`. They were written before the speed work and were **not** used to tune the prompt.

| # | Kind | Question | Accepted first tool |
|---|---|---|---|
| H1 | maths | 我和我媽媽週日下午 3 點想租匹克球場 2 小時，總共多少錢？ (expected NT$700) | calculate_cost |
| H2 | booking | 我想約下週三早上 10 點陳雅婷教練的一對一課，我叫林美君，電話 0955123456 | find_private_slots / book_private_session |
| H3 | not in the data | 你們有提供置物櫃嗎？要另外收費嗎？ | get_center_info / search_services / transfer_to_human |
| H4 | not our job | 幫我解這題微積分：x 平方的積分是多少？ | none |
| H5 | mixed language | 請問 aerial yoga 這週六還有 spot 嗎？ | list_group_classes |

**Held-out accuracy: 5/5 (100%)** before the speed work, and **5/5** after it (details in section 5).

## 5. Response speed optimisation

### 5.1 How we measured

- Every LLM call and every tool call is timed. The seconds are stored in the tool trace (`llm_sec`, `tool_sec`) and shown per step in the UI ("Tools used" panel).
- **The school endpoint caches identical requests.** The same request came back in 0.03 s instead of 6.5 s. Our first baseline was therefore wrong: questions whose request was identical to an earlier run took 0.1 s. The test scripts now put a random value in the OpenAI `user` field for each question. This bypasses the cache and gives the same answer. The app itself does not do this.
- Two metrics are reported:
  - **Metric 1 – tool choice:** the *first* tool called is in the accepted list (unchanged from section 3).
  - **Metric 2 – calculation:** for questions with a known amount (#10–#13, H1), a calculation tool (`calculate_cost` / `compare_plans`) returned the expected amount *and* the reply shows the same amount. For the runs before this metric existed, it was checked from the recorded tool list and the reply text.
- "First text" = seconds until the customer sees the first words of the answer. Without streaming this equals the full response time.

### 5.2 What we changed

| # | Change |
|---|---|
| 1 | Timing of each LLM / tool call in the trace and in the UI |
| 2 | `TOOL_MODE=auto` is detected once when `app.py` starts, not on the first question |
| 3a | Date list in the system prompt: today + 7 days (was 14) |
| 3b | History sent to the model: last 6 customer turns (was 10); tool results longer than 600 characters are shortened before they go into the history (key fields such as `confirm_token`, `booking_id`, `class_id`, `slots` are kept) |
| 3c | `name_en` removed from tool results |
| 3d | Shorter tool descriptions → **reverted** (see 5.4) |
| 4 | UI is a generator: the customer's message appears at once, the status changes while tools run (for example 「正在查詢團課名額…」), the tool trace updates step by step, and the final answer streams (`stream=True`) |
| 5 | LLM timeout 30 s → friendly message with the phone number (07) 625-0666 |

Fixed request size (system prompt + tool definitions): 13,266 characters before → 11,361 with 3d → **13,116** after reverting 3d (only the shorter date list remains).

### 5.3 Results (Furen-large, cache bypassed)

| Run | Main: tool choice | Main: calculation | Main avg / max (s) | Main first text (s) | Held-out: tool choice | Held-out: calculation | Held-out avg / max (s) | Held-out first text (s) |
|---|---|---|---|---|---|---|---|---|
| Baseline (before) | 24/24 | 4/4 | **20.1** / 52.8 | 20.1 | 5/5 | 1/1 | **27.5** / 43.4 | 27.5 |
| Optimised v1 (with 3d) | 23/24 | – | 19.7 / 84.1 | 15.4 | 5/5 | – | 20.0 / 29.3 | 16.4 |
| **Optimised v2 (3d reverted)** | 22/24 | **4/4** | **12.8** / 44.4 | **10.2** | **5/5** | **1/1** | **13.9** / 23.2 | **11.9** |

**How to read these numbers (honest attribution):**
- v2 is 36% faster on the main set (20.1 → 12.8 s) and 49% faster on held-out (27.5 → 13.9 s), but **most of this is probably the endpoint's load, not our changes.** In this test every question starts with an empty history, so 3b (shorter history) does nothing here. The fixed request size is almost the same (13,266 → 13,116 characters). Streaming makes the first words appear earlier but does not shorten the total. The baseline ran at about 15:45 and v2 at about 16:20; the same question took anywhere from 8 to 53 s across runs.
- **What the changes clearly give:** (a) streaming: customers see the first words 2–3 s before the full answer is done (v2: 10.2 s first text against 12.8 s total); (b) the status line and step-by-step trace, so the wait no longer looks like a frozen page; (c) no probe request on the first question; (d) shorter requests in long conversations (3b), which this single-question test cannot show; (e) a 30 s cap with a friendly message instead of a hanging page.

### 5.5 Not done: model comparison and thinking mode (future work)

**Comparing other models and turning off the thinking mode are listed as future improvements and were not completed.** The app keeps **Furen-large** with the v2 code above.

What we found before stopping:
- Furen-large returns hidden reasoning (`reasoning_content`) before each answer. Sending `extra_body={"chat_template_kwargs": {"enable_thinking": False}}` removed it in a quick probe (completion tokens 112 → 69, tool calls still worked). Starting the system prompt with `/no_think` had no effect. The code supports `THINKING=off_kwargs`, but the default stays `THINKING=default`, because accuracy with thinking turned off was never measured.
- Furen-std (same base model as Furen-large) and Furen-omni (a smaller 30B model) both passed `check_model.py` (native tool calling and the `role: tool` round). Their accuracy and speed on the 24 + 5 questions were not measured.

### 5.4 Accuracy checks and what we reverted

- **v1, #13 failed** (我每週游泳 3 次，買月卡還是回數票比較省？): the model asked "成人、學生還是半票？" instead of calling `compare_plans`. We ran #13 four times with each version of the tool descriptions: **new short descriptions 1/4, original descriptions 4/4.** The short `identity` description no longer said "Default adult". **Change 3d was reverted**: tool descriptions are byte-for-byte the originals again.
- **v2, #10 and #11 failed metric 1**: the model first looked up `get_center_info(court_peak_hours)` or `search_services`, *then* called `calculate_cost`. The amounts were still correct and came from the tool (metric 2: 4/4). To see whether this was caused by the optimisation, we ran #10 and #11 four times each with the old and the new code: **old code 7/8, new code 8/8.** The old code makes the same mistake now and then, so this is run-to-run variation, not a regression. Nothing else was reverted.

## 6. Screenshot run (Playwright) and problems still open

The screenshots in `images/` were taken by `tests/take_screenshots.py` (restart app → ask → wait until the turn is finished → check the trace → save). Two findings:

- **A bug in the first version of the screenshot script, not in the agent.** It decided a turn was finished when the timing text "changed", which sometimes happened before the new turn had started, so it captured the *previous* turn's trace. The booking step 「確認」 then looked as if the model had made a second preview. Logging the real requests showed that the confirm_token was in the history and the booking was written. The script now waits until "本輪總耗時" disappears and then appears again.
- **Problems the screenshots showed, and how we fixed them:**

  | Problem | Seen in | Fix | Result |
  |---|---|---|---|
  | After a booking, the reply added a check-in sentence that is not in our data (「請憑預訂編號準時參加」「請憑報名編號…前往報到」) | 3 of 3 runs | The `booked` result now carries `next_step`: give the booking_id and session details only, and do not mention any check-in, ID or arrival procedure. The first wording ("our data has no check-in procedure") made the model say 「無需額外的簽到或證件程序」, which is also not in the data, so the hint now says not to mention it either way | Final run: 「您已成功報名…報名編號為 26…尚餘 4 個名額」, no procedure |
  | Raw field names in replies: 「（is_confirmed: true）」「（enrolled）」「（waitlist_will_be_promoted: true）」 | 2 of 3 runs, despite the prompt rule | `agent.strip_field_names()` removes such brackets from the final reply in code (unit tests added) | Final run: none |
  | Masked names and phones lost their `*` in the chat (「曾*琪」 showed as 「曾琪」, 「0922***009」 as 「0922**009」), because the chat renders Markdown and `*` means italics | every run since the start | Mask with `xxx` and `○` instead (`0912xxx678`, `曾○琪`) | Final run: 「曾○琪（電話 0922xxx009）」 |

  Unit tests after these fixes: **109 passed**.

### Final tool-choice run (used for the report screenshot)

`python tests/run_tool_selection.py` → [`tool_selection_results_final.md`](tool_selection_results_final.md), output image `images/tool-choice-test.png`:
**22/24 = 92%, calculation 4/4, average 23.3 s, max 125.8 s.**
- #10: looked up peak hours first, then `calculate_cost` (amount 950 correct) – the same run-to-run variation as in section 5.4.
- #19 (「我在泳池滑倒受傷了，要找負責人」): **not a routing error.** The LLM call went over the 30 s timeout added in section 5 (the endpoint was very slow during this run; one question took 125.8 s), so the customer got the timeout message with the phone number and no tool was called.
- This run used the code before the three fixes above; those fixes only change booking replies and masking, not tool choice.

## 7. Date words: 「下週X」 and 「這週X」

- **Problem:** in the conv2 screenshot, 「下週二下午 4 點…」 asked on Monday 2026-10-26 was read as 2026-10-27 (this Tuesday). In Taiwan 下週二 means the Tuesday of the next Monday–Sunday week, 2026-11-03. The system prompt only listed the next 7 days, so 11-03 was not even in the list.
- **Change (`prompts.py`):** the date list now runs from today to the end of next week, each date marked "this week" or "next week", plus rules: 「這週X」 = this week, 「下週X」 = next week even if this week's one is still ahead, with the 10-26 → 11-03 example. Unit tests: the calendar for Monday 2026-10-26 and for a Sunday (2026-11-01, where next week starts tomorrow); **110 passed**.
- **Check with the real model** (cache bypassed, each question twice): 「下週二…羽球場」 → 2026-11-03 ×2, 「這週二…TRX」 → 2026-10-27 ×2, 「下週六的空中瑜珈」 → 2026-11-07 ×2, 「明天下午…私人教練」 → 2026-10-27 ×2 (8/8). The conv2 screenshot was taken again and now shows date 2026-11-03, NT$250 + NT$350 = NT$600; the screenshot script checks for next week's Tuesday.
