# W7 作業執行計畫：岡山運動中心 AI 客服 Agent（給 Claude Code）

> 你（Claude Code）要依這份文件，在 `w7_work/` 內從零完成一個 Gradio 客服 Agent。
> 作業規則原文：`../作業規則/Homework-Guide-EN.md`（以英文版為準），中文對照：`../作業規則/(FYR)中文參考版/(FYR)作業說明-中文版.md`。動工前先讀過一次。
> README 範本：`../作業規則/(FYR) GitHub Guide and README Template/(FYR) README-Template.md`
> 模型參考：`../模型使用參考/`（iAI 端點用法）
> 每完成一個 Phase 就停下來回報結果，等使用者確認再進下一個 Phase。

## 工作目錄與參考檔（先讀這段）

- **Claude Code 在 `w7\` 啟動**（才讀得到參考資料夾）。
- **專案程式碼全部寫在 `w7\w7_work\`**；`git init` 只在 `w7_work` 執行，參考資料不能進 repo。
- 動工前必讀（唯讀，不要修改或複製進 repo）：

| 檔案 | 用途 |
|---|---|
| `作業規則/Homework-Guide-EN.md` | 作業規定（以此為準） |
| `作業規則/(FYR)中文參考版/(FYR)作業說明-中文版.md` | 中文對照 |
| `作業規則/.env.example` | `.env.example` 格式（複製 NKUST 區段到 w7_work） |
| `作業規則/(FYR) GitHub Guide and README Template/(FYR) README-Template.md` | README 範本 |
| `作業規則/(FYR) GitHub Guide and README Template/(FYR) GitHub-Guide-EN.md` | 上傳 GitHub 流程 |
| `作業規則/F1152345-Tom Lee-Report-Template-EN.docx` | 報告各節要求，`docs/report_notes_en.md` 依其章節撰寫 |
| `模型使用參考/services/llm_service.py`、`config.py` | iAI 端點與認證方式（Bearer token） |
| `模型使用參考/iAI 請求格式教學.docx` | iAI API 格式說明 |

- `模型使用參考` 裡的 MongoDB、embedding、rerank 是別的專案用的，本作業**不需要**，只參考 chat completions 的呼叫方式。

---

## 0. 專案定位

- **店家**：高雄市岡山運動中心（營運：舞動陽光），客服範圍 = 服務項目、價錢、場地規則、教練課預約。
- **服務對象**：岡山在地居民、上班族、學生、長者、家長（替小孩問泳池/兒童課）。
- **核心功能**：
  1. 回答場館資訊（時間、地址、交通、入場規定、租借規則）
  2. 查詢服務與價格（泳池、健身房、場地租借、課程）
  3. 程式計算費用（身分優待、尖離峰、方案比較）
  4. **教練課預約（加分項：會修改資料的工具）**
     - 私人教練：查教練空檔 → 預約 1 對 1 時段
     - 團體課：固定時段 + 最低開課人數 / 人數上限 + 候補
  5. 查不到 / 客訴 → 轉真人；非場館話題 → 婉拒

### 資料真實性原則
- **價格、營業時間、地址、電話、入場規定**：使用官網真實資料（見第 3 節，來源：舞動陽光官網岡山運動中心頁）。
- **教練、團課時段、課程價格、付款方式、停車細節**：官網未公開，使用**虛構示範資料**，README 與 `facility_info.csv` 相關列要標註「示範資料」。

---

## 1. 技術規格

| 項目 | 內容 |
|---|---|
| 語言 | Python 3.10+ |
| UI | Gradio（Blocks：左聊天、右「Tools used」軌跡面板） |
| LLM | 學校 iAI，OpenAI 相容端點，用 `openai` 套件 |
| DB | SQLite（`sqlite3` 標準庫），**每次啟動從 CSV 重建** |
| 套件 | `gradio`, `openai`, `python-dotenv`, `pytest` |

### `.env` / `.env.example`（沿用作業給的格式）
```
LLM_API_URL=https://www.iai.nkust.edu.tw/aihub/v1/
MODEL_NAME=Furen-large
API_KEY=
TOOL_MODE=auto        # auto | native | json
MAX_STEPS=6
```
- `.env.example` 的 `API_KEY=` 留空。**絕不把金鑰寫進程式、log、README、commit。**
- 使用者會自己建立 `.env` 填入金鑰；在那之前所有不需 LLM 的工作（DB、工具、單元測試）都要先完成。

### 目錄結構
```
w7_work/
├── app.py                 # Gradio 入口（老師執行這支）
├── agent.py               # Agent 迴圈 + LLM 介面（native / json 兩種模式）
├── tools.py               # 所有工具函式 + tool schema + 白名單 registry
├── db.py                  # CSV → SQLite 建表、查詢 helper
├── prompts.py             # System prompt
├── check_model.py         # Phase 0：驗證模型是否支援 tools
├── data/
│   ├── facility_info.csv  # 資訊表（topic, content）
│   ├── services.csv       # 紀錄表（服務與價格）
│   ├── coaches.csv
│   ├── coach_availability.csv
│   ├── group_classes.csv
│   ├── enrollments_seed.csv
│   └── aliases.csv        # 寬鬆名稱比對（加分項）
├── tests/
│   ├── test_tools.py               # 不需 LLM 的工具單元測試
│   ├── tool_selection_cases.csv    # 工具選擇測試題（≥12 題）
│   └── run_tool_selection.py       # 需 LLM，輸出準確率
├── docs/
│   ├── test_results.md             # 測試結果與修正紀錄
│   └── report_notes_en.md          # 報告英文草稿素材
├── images/                         # 截圖放這（使用者手動截）
├── requirements.txt
├── .env.example
├── .gitignore
└── README.md
```
`.gitignore` 至少包含：`.env`、`*.db`、`__pycache__/`、`venv/`、`.venv/`、`node_modules/`、`build/`、`dist/`。

---

## 2. Phase 0：驗證學校模型能不能用 tool calling（拿到金鑰後執行）

`check_model.py`：
1. `GET {LLM_API_URL}models` 列出可用模型。
2. 送一個帶 `tools=[get_weather(city)]` 的 chat completion，問「台北天氣如何？」。
3. 判斷回應的 `message.tool_calls` 是否存在：
   - 有 → 印出 `NATIVE TOOL CALLING: OK`
   - 沒有 / 400 錯誤 → 印出 `NOT SUPPORTED, use TOOL_MODE=json`
4. 同時測試第二輪：把 tool 結果以 `role: tool` 回傳，確認模型能產生最終回答。

`agent.py` 必須支援兩種模式，`TOOL_MODE=auto` 時啟動時自動偵測一次並快取結果：
- **native**：標準 OpenAI `tools` / `tool_calls` / `role:"tool"` 流程。
- **json（fallback）**：在 system prompt 末尾附上工具清單與說明，要求模型**只輸出一行 JSON**：
  - 要呼叫工具：`{"tool": "<name>", "args": {...}}`
  - 要回答：`{"answer": "<給客人的話>"}`
  - 程式解析 JSON（容錯：去掉 ```json 圍欄、取第一個 `{...}`），工具結果以 `role:"user"` 包成 `TOOL_RESULT <name>: <json>` 送回。解析失敗時重試一次並提醒格式。

> 若沒有金鑰，先跳過 Phase 0，從 Phase 1 開始，Phase 4 再回來。

---

## 3. Phase 1：資料（CSV）

### 3.1 `facility_info.csv`（資訊表，欄位 `topic,content`，至少 12 列）

| topic | content 要點 | 來源 |
|---|---|---|
| opening_hours | 週一至週日 06:00–22:00 | 真實 |
| address_transport | 高雄市岡山區中山南路169號；鄰近捷運紅線岡山高醫站（R24），步行可達 | 真實 |
| contact | 電話 (07) 625-0666；LINE 官方帳號 | 真實 |
| facilities | 1F 室內溫水游泳池（25m 七水道，含蒸氣室、烤箱、冷熱水池、SPA）、體適能中心、綜合球場、舞蹈瑜珈/多功能/有氧教室；2F 體適能中心、兒童銀髮舞蹈教室 | 真實 |
| gym_rules | 未滿16歲禁止入場；須攜帶毛巾、水壺，穿著運動服裝；禁穿拖鞋/涼鞋/牛仔褲 | 真實 |
| pool_rules | 10歲以下兒童須家長或年滿20歲成人陪同下水；須著泳衣泳帽（泳帽為示範） | 真實+示範 |
| discount_identity | 學生票須出示學生證；半票適用65歲以上、6歲以下 | 真實 |
| ticket_rules | 月卡 30 天；回數票 30 張 90 天，到期後補差額可使用 | 真實 |
| court_peak_hours | 尖峰：平日 17:00–22:00、假日 10:00–22:00；離峰：平日 06:00–17:00、假日 06:00–10:00 | 真實 |
| court_booking_cancel | 場地租借取消規定（示範：使用前 24 小時可免費取消） | 示範 |
| parking | 停車資訊（示範：周邊有公有停車場，建議搭捷運） | 示範 |
| payment | 付款方式（示範：現金、信用卡、行動支付） | 示範 |
| group_class_rules | 團課每堂有最低開課與人數上限；開課前 24 小時未達最低人數則停課並通知、退費；額滿可候補，有人取消依序遞補 | 示範 |
| private_coach_rules | 私人教練課 1 堂 60 分鐘；需提前至少 1 天預約；24 小時內取消扣堂 | 示範 |
| lost_and_found | 失物請洽櫃台，保留 30 天 | 示範 |

### 3.2 `services.csv`（紀錄表，至少 18 列）

欄位：`service_id,name,name_en,category,price,unit,peak_type,identity,available,note`
- `category`：`pool` / `gym` / `court` / `private_coach` / `group_class`
- `peak_type`：`peak` / `offpeak` / `none`
- `identity`：`adult` / `student` / `half` / `any`
- `available`：`1` / `0`（至少放 1 筆 `0`，例如「籃球場租借：本館暫不開放」，用來測「查得到但不供應」）

必須包含（真實價格）：
- 泳池：全票 100/次、學生票 80/次、半票 50/次、月卡 1250/30天、回數票 2400/30張
- 健身房：全票 50/時、月卡 1250/30天、回數票 2400/30張、身體組成分析 200/次
- 羽球場：尖峰 350/面/時、離峰 250/面/時
- 桌球：尖峰 150/面/時、離峰 100/面/時
- 匹克球：尖峰 350/面/時、離峰 250/面/時

示範價格：
- 私人教練單堂 1200、私人教練 10 堂方案 10800
- 團體課單堂 250、團體課 10 堂方案 2200

### 3.3 教練與課程（虛構，示範資料）

`coaches.csv`：`coach_id,name,specialty,private_price,does_private,does_group,bio`（4–5 位，例如重訓、瑜珈、游泳、舞蹈）

`coach_availability.csv`：`coach_id,weekday(1-7),start_time,end_time`（私教可預約時段）

`group_classes.csv`：
`class_id,name,name_en,coach_id,weekday,start_time,duration_min,room,min_students,max_students,price,available`
建議 7–8 堂，涵蓋：空中瑜珈（min 4 / max 8）、Zumba（min 6 / max 20）、TRX（min 3 / max 8）、有氧舞蹈、兒童游泳班（min 3 / max 6）、銀髮律動、皮拉提斯。**團課時段不可與同教練的私教空檔重疊**。

`enrollments_seed.csv`：`class_id,session_date,member_name,phone,status`
- 啟動時載入，示範三種狀態：某堂「差 1 人成團」、某堂「已額滿（含 1 位候補）」、某堂「已成團仍有名額」。
- `session_date` 用**程式啟動當週/下週的日期**動態產生更好：CSV 存 `week_offset` 與 `weekday`，`db.py` 換算實際日期，避免示範資料過期。

### 3.4 `aliases.csv`（加分：寬鬆名稱比對）
`alias,canonical`，例如：`游泳→溫水游泳池`、`健身→體適能中心`、`gym→體適能中心`、`空瑜→空中瑜珈`、`aerial yoga→空中瑜珈`、`羽毛球→羽球場`、`PT→私人教練`、`學生→student`。

---

## 4. Phase 2：資料庫與工具（不需 LLM）

### 4.1 `db.py`
- 啟動時刪除舊 `center.db`，從 `data/*.csv` 建立所有表（參考作業 Step 3 做法）。
- 另建執行期資料表：
  - `group_enrollments(id, class_id, session_date, member_name, phone, status[enrolled|waitlist|cancelled], created_at)`
  - `private_bookings(id, coach_id, date, start_time, member_name, phone, status, created_at)`
  - `pending_actions(token, action_type, payload_json, expires_at)`（預約兩段式確認用）
- 所有查詢都用參數化 SQL（`?` 佔位符），**模型永遠不寫 SQL**。
- 名稱比對 helper：先查 `aliases`，再 `LIKE %kw%` 比 `name` / `name_en`。

### 4.2 工具清單（`tools.py`）

工具原則（作業規定，必須遵守）：
1. 回傳 **dict / list 資料**，不回傳句子
2. 查不到 → 回 `{"found": false, "reason": "..."}`，不可空白
3. 所有金額、人數判斷都在程式算
4. 參數都有型別與 enum / 格式說明（日期一律 `YYYY-MM-DD`，時間 `HH:MM`）

| # | 工具 | 類型 | 參數 | 回傳重點 |
|---|---|---|---|---|
| 1 | `get_center_info(topic)` | 查資訊 | topic（enum：facility_info 所有 topic） | content；不存在 → found:false + 可用 topic 列表 |
| 2 | `search_services(keyword, category?)` | 查紀錄 | 關鍵字、類別 | 符合的服務、價格、單位、是否供應 |
| 3 | `list_group_classes(keyword?, date?, weekday?)` | 查紀錄 | 課名、日期 | 每堂：時間、教練、已報名數、min/max、狀態（未成團尚差 N 人 / 已成團 / 額滿可候補 N 人）、剩餘名額 |
| 4 | `find_private_slots(date, coach_keyword?, specialty?)` | 查紀錄 | 日期、教練/專長 | 教練當天可預約的整點時段（扣掉已預約與團課） |
| 5 | `calculate_cost(service_keyword, quantity, identity?, date?, start_time?, hours?)` | 計算 | 見下 | 明細與總額 |
| 6 | `compare_plans(category, visits_per_month, hours_per_visit?)` | 計算 | pool/gym、每月次數 | 單次 vs 月卡 vs 回數票，每月花費、最省方案 |
| 7 | `book_group_class(class_id, session_date, member_name, phone, confirm_token?)` | 寫入（加分） | 見 4.3 | 預覽或結果 |
| 8 | `book_private_session(coach_id, date, start_time, member_name, phone, confirm_token?)` | 寫入（加分） | 見 4.3 | 預覽或結果 |
| 9 | `cancel_booking(booking_type, booking_id, phone)` | 寫入（加分） | 類型、編號、電話驗證 | 取消結果；團課取消後自動遞補候補第一位並回報 |
| 10 | `transfer_to_human(reason)` | 轉真人 | 原因 | 電話 (07) 625-0666、LINE、服務時間、案件摘要 |

`calculate_cost` 計算規則（程式實作）：
- 依 identity 選票價（adult/student/half）；身分不適用該服務時回錯誤（例如健身房沒有學生票）。
- 場地類依 `date` 判斷平日/假日（週六日為假日），再依 `start_time` + `hours` **逐小時**判斷尖峰/離峰（跨 17:00 或 10:00 要拆段計價）。
- 課程方案：自動比較「N 堂單買」vs「10 堂方案」並附上較省的選擇。
- 回傳範例：`{"items":[{"desc":"羽球場 平日 16:00-17:00 離峰","unit_price":250,"qty":1,"subtotal":250},...],"total":600}`

### 4.3 預約邏輯（重點）

**兩段式確認（作業加分要求：Agent 先摘要、客人確認後才寫入）**
1. 第一次呼叫不帶 `confirm_token` → 驗證所有條件，**不寫入**，回傳 `{"status":"preview", "summary":{...}, "confirm_token":"abc123", "expires_in_min":10}`。
2. Agent 把摘要講給客人，客人明確說「確認/好/對」後，Agent 再呼叫一次並帶 `confirm_token` → 程式比對 `pending_actions` 中 payload 完全一致才寫入。
3. token 錯誤、過期、payload 不一致 → 拒絕。這樣即使模型想跳過確認也寫不進去。

**團體課規則**
- `session_date` 的星期必須等於該課的 `weekday`，且不可是過去日期。
- 同一電話不可重複報名同一堂（同 class_id + date）。
- `enrolled < max_students` → status `enrolled`；已滿 → status `waitlist`（回傳候補序號）。
- 回傳時一併給：目前人數、`min_students`、是否已成團、尚差幾人成團。
- 取消時：若取消的是 enrolled，自動把最早的 waitlist 升為 enrolled，回傳被遞補者資訊（遮罩電話）。
- 開課前 24 小時未成團的停課規則只需在 `list_group_classes` 狀態中標示「若 X 前未達 N 人將停課」，不必做排程。

**私人教練規則**
- 日期至少為明天（依 `private_coach_rules`）。
- 時段必須落在 `coach_availability` 內、整點、60 分鐘。
- 不可與該教練的私教預約或團課時間重疊；同一電話同時段也不可重複。
- 預覽時附價格（單堂 private_price）。

**通用**
- `phone` 驗證台灣手機格式 `09\d{8}`；姓名不可空。
- 預約成功回傳 `booking_id`，取消時要 `booking_id + phone` 都對。
- 回傳給模型的資料中電話一律遮罩（`0912***678`）。

### 4.4 白名單 registry
`TOOLS = {name: (function, schema)}`。Agent 只執行 registry 內的名稱；參數用 schema 驗證必要欄位，錯誤回 `{"error": "..."}` 給模型而非丟例外。

### 4.5 `tests/test_tools.py`（pytest，Phase 2 必須全綠）
至少涵蓋：
- 每個 info topic 查得到；不存在的 topic 回 found:false
- 別名比對（「游泳」「gym」「空瑜」）
- 羽球場平日 16:00 起 2 小時 = 250 + 350 = 600（跨尖峰）
- 假日 09:00 起 2 小時 = 250 + 350
- 學生泳票 ×3 = 240；健身房要求學生票 → 錯誤
- compare_plans：每月 20 次健身 1 小時 → 月卡最省
- 團課：差 1 人成團的課報名後變成已成團；額滿課報名進候補；取消後候補遞補
- 不帶 token 不寫入；錯 token 拒絕；重複報名拒絕；星期不符拒絕
- 私教：空檔外時段拒絕；與團課衝突拒絕；今天日期拒絕

---

## 5. Phase 3：Agent 與 System Prompt

### 5.1 `prompts.py` — 角色 + 四類規則（英文撰寫，回覆語言跟隨客人）

動態注入：**今天日期與星期（Asia/Taipei）**，讓模型能把「這週五」換算成 `YYYY-MM-DD`。

- **Role**：Customer service assistant of Gangshan Sports Center (Kaohsiung).
- **Grounding（接地）**：Answer only from tool results. Never invent prices, times, coaches or rules. If a tool returns found:false or the data does not cover the question, say you don't have that information and call `transfer_to_human`.
- **Scope（範圍）**：Only discuss this sports center: facilities, prices, rules, classes, bookings. Politely decline anything else (homework, news, medical diagnosis, other gyms) and steer back to the center.
- **Routing（分派）**：
  - hours/address/parking/rules/payment → `get_center_info`
  - ticket, rental, course prices → `search_services`
  - "how much for X" with quantity/time/identity → `calculate_cost`（never do math yourself）
  - which plan is cheaper → `compare_plans`
  - group class schedule / full / enough people → `list_group_classes`
  - private coach availability → `find_private_slots`
  - booking → collect name, phone, class/coach, date first; call book_* without token, read the summary back, and only after the customer clearly confirms, call again with the confirm_token
  - complaints, refunds disputes, injuries, manager → `transfer_to_human`
- **Language & format**：Reply in the customer's language (Traditional Chinese by default). Keep answers within 3–4 sentences; list items with bullets when showing schedules. Show money as NT$.

### 5.2 `agent.py` — 迴圈
```
def run_agent(user_msg, history) -> (reply, trace):
    messages = [system] + history + [user]
    for step in range(MAX_STEPS):
        resp = llm(messages, tools)          # native 或 json 模式
        if 有 tool call:
            for 每個 call:
                if name not in TOOLS: result = {"error": f"unknown tool {name}"}  # 拒絕
                else: result = 執行(參數驗證後)
                trace.append({"step", "tool", "args", "result"})
                把結果加回 messages
            continue
        return resp.text, trace
    return "抱歉，系統忙碌，請改撥 (07) 625-0666。", trace   # 超過 max_steps
```
- 每次呼叫 LLM 加錯誤處理：429 → 退避重試（2s、4s、8s），其他錯誤回友善訊息並寫入 trace。
- 呼叫 LLM 之間不需 sleep；測試腳本才需要 `time.sleep()`。

### 5.3 `app.py` — Gradio
- 左：`gr.Chatbot`（type="messages"），開場招呼語：介紹自己能做什麼（查價格、場館規則、預約教練課）。
- 下方：6–8 個範例問題按鈕（涵蓋四類問題 + 預約）：
  - 「學生游泳一次多少錢？」
  - 「平日晚上 6 點租羽球場 2 小時多少？」
  - 「我每週去健身房 4 次，買月卡划算嗎？」
  - 「這週六的空中瑜珈還有名額嗎？成團了嗎？」
  - 「我想預約明天下午的私人教練」
  - 「你們有賣運動護具嗎？」（資料沒有 → 轉真人）
  - 「幫我寫一篇作文」（非客服 → 婉拒）
- 右：「Tools used」面板（`gr.JSON` 或 Markdown），逐步顯示 tool 名稱、args、result；另顯示目前 TOOL_MODE 與模型名。
- 每次提問都帶完整對話歷史（作業要求）。
- 「清除對話」按鈕。
- 啟動：`python app.py` → `db.init_db()` → 啟動 Gradio。

---

## 6. Phase 4：用真模型測試（需要金鑰）

1. 執行 `check_model.py`，決定 TOOL_MODE。
2. 手動在 Gradio 測四類問題，各至少一段：
   | 類型 | 範例 | 預期 |
   |---|---|---|
   | 資料裡有 | 營業時間？學生泳票多少？ | get_center_info / search_services，數值正確 |
   | 需要計算 | 平日 16:00 租羽球場 2 小時？ | calculate_cost，600 元 |
   | 資料裡沒有 | 有沒有賣運動飲料？可以帶寵物嗎？ | 說沒有資料 + transfer_to_human |
   | 不是客服的事 | 今天股市如何？ | 婉拒並帶回場館 |
   另測完整預約流程（預覽 → 確認 → 查詢人數變化 → 取消 → 候補遞補）。
3. `tests/tool_selection_cases.csv`（欄位 `id,question,acceptable_tools,category`，**至少 12 題**，可接受多個工具用 `|` 分隔，`none` 表示不應呼叫工具）。
4. `tests/run_tool_selection.py`：逐題跑 agent，記錄**第一個**被呼叫的工具，比對是否在可接受清單中，每題間 `time.sleep(4)`，輸出準確率與每題結果到 `docs/test_results.md`。
5. 答錯的題：修改**工具 description 或 routing 規則**（客人常問但資料沒有的事 → 補進 CSV，不是補規則），在 `docs/test_results.md` 記錄「錯在哪、改了什麼、重測結果」。重跑直到準確率 ≥ 90% 或回報使用者。

---

## 7. Phase 5：交付文件

1. `README.md`：依範本填寫（工具表、資料表與筆數、安裝執行步驟、截圖位置 `images/`、「教練與課表為示範資料」聲明、價格資料來源）。學生資訊留 `[Student ID] Wenxin Shen` 讓使用者填學號。
2. `requirements.txt`、`.env.example`、`.gitignore` 確認。
3. `docs/report_notes_en.md`：給報告用的英文素材
   - Section 1 情境描述（150–250 英文字：服務誰、解決什麼、為何比打電話 / FAQ 頁 / 通用聊天機器人 / LINE 自動回覆更好）
   - Section 2 資料表欄位與筆數
   - Section 3 每個工具類型、參數、範例問句；計算工具怎麼算（2–3 句）
   - Section 4 system prompt 四類規則與理由
   - Section 5 測試準確率與修正
   - Section 7 加分項：預約寫入工具（兩段式確認）、團課成團/候補邏輯、別名比對、中英雙語
   - 列出使用者需要手動截的圖（四類對話 + 預約流程，**都要看得到右側工具軌跡**）
4. Git：`git init`、確認 `git status` 中沒有 `.env` 與 `*.db` 才 commit。**推送到 GitHub（帳號 wenxin322，Public repo）前先詢問使用者。**

---

## 8. 驗收清單（每項都要打勾才算完成）

- [ ] `python app.py` 可在全新環境（只裝 requirements）直接啟動
- [ ] 資訊表 ≥ 8 topics（目標 15）、紀錄表 ≥ 15 筆（目標 18+），皆由 CSV 建立
- [ ] 至少 4 類工具：查資訊、查紀錄、計算、轉真人 ✔ 另有預約寫入工具
- [ ] 工具回傳資料不回句子；查不到明確回 found:false；模型不寫 SQL
- [ ] max_steps 生效；未知工具名被拒絕
- [ ] UI 有開場語、範例問題、工具軌跡面板、對話歷史
- [ ] System prompt 含角色 + 接地/範圍/分派/語言格式四類規則，並注入今天日期
- [ ] 預約必須經過 preview → confirm_token 才寫入
- [ ] 團課 min/max/候補/遞補邏輯正確；私教衝突檢查正確
- [ ] `pytest` 全綠
- [ ] 工具選擇測試 ≥ 12 題，準確率與修正紀錄寫入 docs
- [ ] repo 內無金鑰、無 .db、無 __pycache__
- [ ] README 完整，含示範資料聲明

## 9. 注意事項
- 不要把 API 金鑰貼進對話或任何檔案；需要測試時請使用者自行建立 `.env`。
- 學校端點可能限流：測試腳本題目間要 sleep，遇 429 要退避。
- 時區一律 Asia/Taipei。
- 不可直接交課堂速食店範例；所有工具名稱、說明、程式都是本專案自行撰寫。
