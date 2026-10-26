# Report notes (English): material for Report-Template-EN.docx

These notes follow the sections of the report template. Copy what you need into the Word file and rewrite it in your own
words where the template asks for that (sections 1 and 6 especially). Numbers come from `docs/test_results.md`.

---

## Basic Information

| Item | Answer |
|---|---|
| Student ID | [Student ID] |
| Name | Wenxin Shen |
| Your shop or office | Gangshan Sports Center (高雄市岡山運動中心, operated by 舞動陽光), Kaohsiung |
| Model you used | `Furen-large` on the NKUST iAI endpoint (OpenAI-compatible, native tool calling) |
| How you built the chat screen | Gradio (Blocks), Python |
| GitHub link | https://github.com/f113118101/nkust-115-w7-customer-service-agent |

---

## 1. Where Your Agent Is Used: Who It Serves and Why It Fits Them

*(About 200 words. Rewrite it in your own words.)*

My agent serves the people who use Gangshan Sports Center every week: local residents and office workers who swim or go to
the gym after work, students and seniors who want the discount price, badminton and pickleball players who rent courts, and
parents who ask about the kids' swimming class.

Their questions are short but the answers are tricky. A court costs a different amount before and after 17:00 on weekdays and
10:00 at weekends, so "4 to 6 pm" needs two prices. Customers also ask whether a monthly pass is worth it, whether Saturday's
aerial yoga class is full or has enough people to run, and they want to book a coach.

Today they call the front desk, which is busy, or read the website's price table and do the maths themselves. A general
chatbot like ChatGPT does not know this center's prices or today's class places and may invent them. A LINE auto-reply only
matches keywords and cannot calculate or book.

My agent answers from the center's own database, calculates every price in code, checks live class numbers and the waitlist,
and books a session only after the customer confirms a summary. When the data does not cover a question, it says so and hands
the customer over to staff instead of guessing.

---

## 2. Your Database: the Tables, Their Columns, and Their CSV Files

| Table | CSV file | Columns | Number of rows |
|---|---|---|---|
| `facility_info` (information table) | `data/facility_info.csv` | topic, content | 15 topics |
| `services` (records table) | `data/services.csv` | service_id, name, name_en, category, price, unit, peak_type, identity, available, note | 20 rows (1 not available: basketball court) |
| `coaches` | `data/coaches.csv` | coach_id, name, specialty, private_price, does_private, does_group, bio | 5 rows |
| `coach_availability` | `data/coach_availability.csv` | coach_id, weekday, start_time, end_time | 20 rows |
| `group_classes` | `data/group_classes.csv` | class_id, name, name_en, coach_id, weekday, start_time, duration_min, room, min_students, max_students, price, available | 8 rows (1 paused) |
| `group_enrollments` (seed) | `data/enrollments_seed.csv` | class_id, week_offset, weekday, member_name, phone, status | 25 rows |
| `aliases` | `data/aliases.csv` | alias, canonical | 67 rows |

Built at start-up, not from CSV: `private_bookings`, `pending_actions` (preview tokens for the confirmation step).
The seed bookings use `week_offset` (0 = the next session of that class), so the sample dates never go out of date.

Real data vs demo data: prices, hours, address, phone, facilities, rules and peak hours come from the operator's website.
Coaches, timetable, sample bookings, coaching and class prices, payment, parking, cancellation and lost-and-found are demo data
and are marked「（示範資料）」.

---

## 3. Your Tools: What Each One Does, and How a Customer Might Ask

### 3.1 Your Tools

| Tool name | Kind | What it reads or calculates | Parameters | How a customer might ask |
|---|---|---|---|---|
| `get_center_info` | Look up information | `facility_info` table | topic (one of the 15 topics) | 「健身房可以穿拖鞋嗎？」「有停車場嗎？」 |
| `search_services` | Look up records | `services` and `group_classes`, with aliases | keyword, category (optional) | 「學生游泳一次多少錢？」「可以租籃球場嗎？」 |
| `list_group_classes` | Look up records | `group_classes` + live enrollment counts | keyword, date, weekday (all optional) | 「這週六的空中瑜珈還有名額嗎？成團了嗎？」 |
| `find_private_slots` | Look up records | `coach_availability` minus bookings and group classes | date, coach_keyword, specialty | 「明天下午有私人教練可以約嗎？」 |
| `calculate_cost` | Calculate (in code) | prices from `services`, peak hours, identity, 10-session pack | service_keyword, quantity, identity, date, start_time, hours | 「平日下午 4 點租羽球場 2 小時多少？」 |
| `compare_plans` | Calculate (in code) | single / monthly pass / ticket book per month | category, visits_per_month, hours_per_visit, identity | 「我每週去健身房 4 次、每次 2 小時，買月卡划算嗎？」 |
| `book_group_class` | Change data (two steps) | writes `group_enrollments` (enrolled or waitlist) | class_id, session_date, member_name, phone, confirm_token | 「我要報名週六空中瑜珈，王小明 0912345678」 |
| `book_private_session` | Change data (two steps) | writes `private_bookings` after conflict checks | coach_id, date, start_time, member_name, phone, confirm_token | 「明天 14:00 林志豪教練」 |
| `cancel_booking` | Change data (two steps) | cancels; promotes the first waitlisted person | booking_type, booking_id, phone, confirm_token | 「我要取消報名編號 4」 |
| `transfer_to_human` | Hand over to a person | staff phone, LINE, hours, case summary | reason | 「我要客訴」「我在泳池滑倒受傷了」 |

### 3.2 How Your Calculation Tool Works

*(Two or three sentences.)*

`calculate_cost` finds the service by a forgiving name (aliases + partial match) and takes all prices from the `services`
table. For a court it decides weekday or weekend from the date and checks every hour on its own, so 16:00–18:00 on a weekday
becomes one off-peak hour (NT$250) plus one peak hour (NT$350) = NT$600. Tickets use the price for the identity (adult /
student / half), and coaching or classes compare buying single sessions with the 10-session pack and return the cheaper
combination. If the name is unclear it returns `{"error": ..., "options": [...]}`; if nothing matches it returns
`{"found": false}`; and if a service is not offered (the basketball court) it returns `available: false` with no price, so the
model has nothing to invent from.

---

## 4. Your System Prompt: the Role, the Four Kinds of Rules, and Why

(Full prompt: `prompts.py`. Today's date and the next 7 days are added at every call.)

| Kind of rule | Your rule (from the prompt) | Why you wrote it |
|---|---|---|
| Role | "You are the customer service assistant of Gangshan Sports Center … You help local residents, office workers, students, seniors and parents …" | Tells the model whose prices and rules it speaks for and who it is talking to. |
| Grounding | "Answer ONLY from tool results. Never invent prices, times, coaches, class schedules, rules or booking results." | Prices and class numbers change; the model must not use guesses or general knowledge. |
| Grounding | "If a tool returns found:false … say you do not have that information and call transfer_to_human … found:false means 'not in our data', not 'the center does not have it'." | In testing the model said "we don't sell sports drinks" and suggested a convenience store; we do not know that. |
| Grounding | "Do not add procedures, documents, notifications (SMS, email), check-in steps or policies that are not in a tool result." | In testing the model made up "bring your ID and the SMS" and reversed the cancellation rule. |
| Grounding | "Seats, enrollment counts, waitlists and free slots change after every booking: call the tool again …" | The model answered "is anyone still waiting?" from an old result. |
| Grounding | "When a tool result contains assumptions, add one short sentence …" / "Report compare_plans results honestly … Do not push the monthly pass." | The plan comparison assumes 30 days and 1 ticket per visit; the customer should know, and the agent must not upsell. |
| Scope | "Only discuss this sports center …" and "Any question about what THIS center sells, offers, rents or allows (goggles, drinks, lockers, pets …) is in scope … Do not decline it." | The first version refused "do you sell goggles?" as off-topic; that is a center question we simply have no data for. |
| Scope | "Politely decline topics unrelated to the center (homework, essays, poems, news, stocks, weather, medical diagnosis, other gyms) in one sentence, then offer help with the center." | Keeps the agent on its job and polite. |
| Routing | One line per tool, e.g. "'How much for …' with a quantity, identity, date/time or number of sessions -> calculate_cost. Never do arithmetic yourself." and "'N times a week' = N x 4 visits per month." | The model chooses tools better when every kind of question has a clear target, and maths must come from code. |
| Routing | Booking steps: "Call … WITHOUT confirm_token. Read the summary back … Only after the customer clearly says yes … call the same tool again … plus the confirm_token." | Matches the two-step design; the code also refuses any write without a valid token. |
| Language and format | "Reply in the customer's language; default to Traditional Chinese … 3-4 sentences … Show money as NT$ …" | Customers write in Chinese, English or a mix; short answers are easier to read on a phone. |
| Language and format | "Never show tool names, JSON field names or raw values such as is_confirmed or true/false." | In testing the model printed `is_confirmed：false` to the customer. |

---

## 5. Your Agent in Action

### 5.1 Four Conversations

| # | Title | The situation (who asks, what a good answer is) | Question to type | What the trace must show |
|---|---|---|---|---|
| 1 | In the data: "A student asks for the pool price" | A university student wants to swim. Good answer: the student price NT$80 per visit, needs a student ID, taken from the database. | 學生游泳一次多少錢？ | `search_services` (or `calculate_cost`) with the result row `溫水游泳池 單次學生票`, price 80 |
| 2 | Needs maths: "An office worker books a court across the 17:00 peak" | Two colleagues want to play badminton after work. Good answer: NT$600 = 250 off-peak + 350 peak, calculated by the tool. | 下週二下午 4 點租羽球場 2 小時多少錢？ | `calculate_cost` with date / 16:00 / 2 hours, items 250 + 350, total 600 |
| 3 | Not in the data: "A customer asks about sports drinks" | Good answer: "I don't have that information", no guessing, and a hand-over with the phone number. | 你們有賣運動飲料嗎？ | `search_services` → `found: false` → `transfer_to_human` |
| 4 | Not our job: "A student asks for an essay" | Good answer: one polite sentence of refusal, then an offer to help with the center. | 幫我寫一篇作文 | no tool called (the trace panel shows `no tool called`) |

### 5.2 Tool-Choice Test

| Item | Answer |
|---|---|
| Number of questions | 24 (+ 5 held-out questions) |
| First score | 23/24 = 96% (missed #20 「你們有賣泳鏡嗎？」: no tool called, treated as off-topic) |
| Change | Scope rule: questions about what the center sells / offers / allows are in scope and must be looked up; not found → hand over |
| Score after the change | 24/24 = 100%; held-out 5/5 = 100% |
| Later runs | 22–24/24. Misses were the model looking up peak hours or the price list *before* `calculate_cost`; the amounts were still correct and came from the tool (calculation metric 4/4). The old code makes the same choice 1 time in 8, so this is run-to-run variation. |

Also worth one or two sentences (from `docs/test_results.md` sections 2 and 5):
- Scenario tests found five problems in the first run (no tool for "sports drinks", invented SMS / ID steps, the cancellation rule reversed, JSON field names shown, stale waitlist numbers); each was fixed with a rule or a tool hint, and the final run had none.
- A shorter version of the tool descriptions made #13 fail 3 times in 4 (the model asked "adult or student?" instead of using the default), so we went back to the full descriptions.

---

## 6. How You Coworked with AI, and the Problems You Met

### 6.1 Your AI Tools

| Item | Answer |
|---|---|
| Which AI tools | Claude Code (Anthropic) |
| Where | In the console (command line), inside the project folder |
| About how many hours | [fill in, e.g. about N hours] |
| What for | Turning my written plan (`docs/CLAUDE_CODE_PLAN.md`) into CSV data, tools, the agent loop and the Gradio page; writing unit tests and test scripts; running the tests against the real model; finding and fixing problems; writing the README and these notes. I reviewed each phase and made the decisions (data rules, booking rules, which model, what to stop). |

### 6.2 Problems You Met, and How You Solved Them

*(Pick the ones you want and tell them in your own words.)*

1. **Bookings across two turns.** The customer confirms in the *next* message, but the confirm_token was in the tool result of
   the previous turn. We now keep the full model history (with tool calls and results) in the Gradio state, and shorten long
   tool results before they go into the history while always keeping `confirm_token`.
2. **The model invented things.** The first scenario run showed made-up procedures ("bring your ID and the SMS"), a reversed
   cancellation rule, JSON field names in replies, and "we don't sell X" without any tool call. Each became a specific rule
   in the prompt (section 4), and the tool's `found:false` result now says what to do next.
3. **Scope vs not-in-data.** 「你們有賣泳鏡嗎？」 was refused as off-topic. We made the scope rule say that questions about what
   the center offers are in scope, and the score went from 23/24 to 24/24.
4. **The endpoint caches identical requests.** The first speed test showed 0.1 s answers. We found that the school endpoint
   returns the same request from a cache (0.03 s against 6.5 s), so the test scripts now add a random `user` field to measure
   real speed.
5. **A "speed" change hurt accuracy.** Shorter tool descriptions made one question fail 3 times out of 4; we compared old and
   new descriptions 4 times each, found the cause and reverted it.
6. **Environment problems.** Python 3.9 on the computer was too old for Gradio 5+, so we used Python 3.13 in a `.venv`.
   Gradio 6 removed `Chatbot(type="messages")`, so the code checks for that parameter. Chinese text was garbled in the
   Windows terminal until the scripts set UTF-8 output.
7. **A moment when the plan / the AI was wrong:**
   - The plan expected "20 gym visits of 1 hour a month → the monthly pass is cheapest". The tool said single entry is
     cheaper (20 × NT$50 = NT$1,000 < NT$1,250). We checked the maths by hand: the tool was right and the plan was wrong.
   - Claude Code first wrote that our changes made the agent 36% faster. When I looked at it again, most of that came from the
     endpoint being less busy at that time (every test question starts with an empty history, so the history change could not
     help). The test report now says this honestly.
   - Claude Code deleted a finished set of test results by mistake when I asked it to stop the model comparison; it told me,
     and the numbers in the report did not depend on them.

---

## 7. Extra Credit

| Feature | Did you do it? | How, and where to see it |
|---|---|---|
| A tool that changes data | Yes | `book_group_class`, `book_private_session`, `cancel_booking` in `tools.py`. The first call returns a preview and a `confirm_token` (stored in `pending_actions`, 10-minute expiry). The data is written only when the call is repeated with the same arguments and the token after the customer confirms; a wrong, expired or reused token, or changed details, is refused. Group classes handle min / max students, a waitlist, and automatic promotion from the waitlist on cancellation; private sessions check the coach's hours, group class clashes and double bookings. Tests: `tests/test_tools.py`; screenshots: booking flow. |
| Two languages | Yes | `name_en` columns in `services.csv` and `group_classes.csv` are used to match English names (「aerial yoga」「gym」「pickleball」), and the prompt makes the agent answer in the customer's language. Held-out question H5 「請問 aerial yoga 這週六還有 spot 嗎？」 passed. |
| Forgiving names | Yes | `data/aliases.csv` (67 aliases, e.g. 游泳 → 溫水游泳池, 空瑜 → 空中瑜珈, 羽毛球 → 羽球場, 敬老 → half price) plus partial matching and word scoring in `db.py`. 「學生游泳票 ×3」 finds the student ticket and gives NT$240. |
| Something else | Yes | Live tool trace with the seconds for each step; streamed answers with a status line (「正在查詢團課名額…」); a JSON fallback mode for models without tool calling; 107 unit tests including the agent loop with a fake LLM. |

---

## 8. Checklist (before you hand in)

- [ ] PDF named `[Student ID]_Wenxin Shen_CustomerService.pdf` and uploaded
- [ ] GitHub repository is public, the link is in Basic Information, and it opens in a private (incognito) window
- [ ] On the GitHub page, `.env` and `center.db` are **not** there
- [ ] Information table 15 topics (≥ 8), records table 20 rows (≥ 15), 10 tools covering all four kinds
- [ ] Part 5 has four screenshots, each with the tool trace visible
- [ ] Tool-choice test ≥ 10 questions (24) with the score, and part 6 filled in

---

## Screenshots to take by hand

Start the app with `python app.py` and open <http://127.0.0.1:7860>. **Restart the app before the booking screenshots** so the
sample data is fresh (the database is rebuilt at every start). Save the files in `images/` with these names (the README uses
them). In every screenshot, the **right-hand "Tools used" panel must be visible**: the timing table and the JSON trace with
`tool`, `args` and `result`. Click open the trace items you want to show.

| # | File name | What to type | What must be visible in the trace | Used in |
|---|---|---|---|---|
| 1 | `images/screenshot-main.png` | (after shot 2) the whole page | greeting, sample-question buttons, the chat, and the right panel with Model / TOOL_MODE / timing table | README top |
| 2 | `images/conv1-in-data.png` | 學生游泳一次多少錢？ | `search_services` (or `calculate_cost`) with args `{"keyword": "游泳"…}` and the result row with `"price": 80` | Report 5.1 #1, README |
| 3 | `images/conv2-maths.png` | 下週二下午 4 點租羽球場 2 小時多少錢？ | `calculate_cost` with `date` (a Tuesday), `start_time: "16:00"`, `hours: 2`; result items 250 (離峰) and 350 (尖峰), `"total": 600` | Report 5.1 #2, README |
| 4 | `images/conv3-not-in-data.png` | 你們有賣運動飲料嗎？ | `search_services` → `"found": false`, then `transfer_to_human` with the phone number | Report 5.1 #3, README |
| 5 | `images/conv4-not-our-job.png` | 幫我寫一篇作文 | the trace shows `{"note": "no tool called"}` and the timing table has only 「產生回答」 | Report 5.1 #4, README |
| 6 | `images/booking-1-preview.png` | 這週六的空中瑜珈還有名額嗎？成團了嗎？ then 我要報名，我叫王小明，電話 0912345678 | second turn: `book_group_class` **without** `confirm_token`; result `"status": "preview"`, the summary, and a `confirm_token` | Report 7 |
| 7 | `images/booking-2-confirmed.png` | 確認 | `book_group_class` **with** the same `confirm_token`; result `"status": "booked"`, `booking_id`, and `"is_confirmed": true` (3 → 4 people, the class now runs) | Report 7, README |
| 8 | `images/booking-3-waitlist-promotion.png` | (new chat) 我是周建宏，電話 0922000001，我要取消 TRX 的報名，報名編號 4 then 確認 | `cancel_booking` preview, then `cancel_booking` with the token; result `"status": "cancelled"` and `"promoted_from_waitlist": {"member_name": "曾*琪", …}` | Report 7 |
| 9 | `images/tool-choice-test.png` | in a terminal: `python tests/run_tool_selection.py` (takes about 8–10 minutes) | the PASS / FAIL lines and the final `Tool choice: …/24` line | Report 5.2 |
| 10 (optional) | `images/streaming-status.png` | 這週有哪些團體課？ (take the shot while it is working) | the chat bubble shows 「⏳ 正在查詢團課名額…」 and the trace already has `list_group_classes` | Report 7 "something else" |

Tips: the trace panel shows the **current turn only**, so take each booking screenshot right after its turn. If the model asks
a question first (for example "do you want to join the waitlist?"), answer it and take the screenshot on the turn where the
tool shown above is called.
