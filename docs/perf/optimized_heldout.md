# Tool selection test results (tool_selection_heldout.csv)

- Model: `Furen-large`, TOOL_MODE: `native`
- Run at: 2026-10-26 16:09 (Asia/Taipei)
- **Accuracy: 5/5 = 100%** (first tool called must be in acceptable_tools)
- **Average response time: 20.0 s** per question (max 29.3 s); endpoint cache bypassed (random user field per question)
- **Average time to first answer text: 16.4 s**

| # | Category | Question | Acceptable | First tool | All tools | LLM calls | Sec | First text | Result |
|---|---|---|---|---|---|---|---|---|---|
| H1 | maths | 我和我媽媽週日下午 3 點想租匹克球場 2 小時，總共多少錢？ | calculate_cost | `calculate_cost` | calculate_cost, transfer_to_human | 3 | 29.3 | 25.7 | ✅ |
| H2 | booking | 我想約下週三早上 10 點陳雅婷教練的一對一課，我叫林美君，電話 0955123456 | find_private_slots / book_private_session | `find_private_slots` | find_private_slots, book_private_session | 3 | 21.9 | 16.4 | ✅ |
| H3 | not_in_data | 你們有提供置物櫃嗎？要另外收費嗎？ | get_center_info / search_services / transfer_to_human | `get_center_info` | get_center_info, search_services, transfer_to_human | 4 | 23.4 | 18.6 | ✅ |
| H4 | out_of_scope | 幫我解這題微積分：x 平方的積分是多少？ | none | `none` | - | 1 | 10.5 | 9.0 | ✅ |
| H5 | mixed_language | 請問 aerial yoga 這週六還有 spot 嗎？ | list_group_classes | `list_group_classes` | list_group_classes | 2 | 14.9 | 12.5 | ✅ |

## Replies

**#H1 我和我媽媽週日下午 3 點想租匹克球場 2 小時，總共多少錢？** ✅

> 我已將您的問題轉接給人工客服，案件編號：H202610261607-6f74。  
> 您可於服務時間內聯絡：電話 (07) 625-0666，LINE 岡山運動中心官方帳號，服務時間 06:00-22:00。  
> 如需其他有關岡山運動中心的資訊，我很樂意協助。

**#H2 我想約下週三早上 10 點陳雅婷教練的一對一課，我叫林美君，電話 0955123456** ✅

> 以下是您預約的私人教練課程資訊，請確認無誤後回覆「確認」或「好」：
> 
> - 教練：陳雅婷（瑜珈專長）
> - 日期：2026-10-28（週三）
> - 時間：10:00 ～ 11:00
> - 費用：NT$1200
> - 姓名：林美君
> - 電話：0955***456
> - 取消政策：課程前 24 小時內取消視為已使用
> 
> 請確認以上資訊正確嗎？若無誤，請回覆「確認」或「好」以完成預約。

**#H3 你們有提供置物櫃嗎？要另外收費嗎？** ✅

> 我已將您的問題轉交給人工客服處理，案件編號為 H202610261608-7eda。您可於服務時間內（06:00‑22:00）撥打電話 (07) 625-0666 或透過「岡山運動中心 LINE 官方帳號」與我們聯繫，工作人員將為您說明置物櫃的使用情況及收費細節。如有其他關於場館設施、課程或預約的問題，我也很樂意協助。祝您運動愉快！

**#H4 幫我解這題微積分：x 平方的積分是多少？** ✅

> 抱歉，我只能處理與高雄市岡山運動中心相關的問題。如果您有場館設施、票價、課程或預約方面的需求，我很樂意協助。

**#H5 請問 aerial yoga 這週六還有 spot 嗎？** ✅

> 這週六（2026-10-31）的空中瑜珈課程（G01）目前有 **5 個空位**，已報名 3 人，尚需再有 1 人才能達到開班最低人數（4 人）。若在 2026-10-30 上午 10:00 前仍未達標，課程將會取消。您可以直接報名，報名後若班額滿會自動加入候補名單。需要我幫您預約嗎？

