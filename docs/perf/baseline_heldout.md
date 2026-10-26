# Tool selection test results (tool_selection_heldout.csv)

- Model: `Furen-large`, TOOL_MODE: `native`
- Run at: 2026-10-26 15:56 (Asia/Taipei)
- **Accuracy: 5/5 = 100%** (first tool called must be in acceptable_tools)
- **Average response time: 27.5 s** per question (max 43.4 s); endpoint cache bypassed (random user field per question)

| # | Category | Question | Acceptable | First tool | All tools | LLM calls | Sec | Result |
|---|---|---|---|---|---|---|---|---|
| H1 | maths | 我和我媽媽週日下午 3 點想租匹克球場 2 小時，總共多少錢？ | calculate_cost | `calculate_cost` | calculate_cost | 2 | 23.6 | ✅ |
| H2 | booking | 我想約下週三早上 10 點陳雅婷教練的一對一課，我叫林美君，電話 0955123456 | find_private_slots / book_private_session | `find_private_slots` | find_private_slots, book_private_session | 3 | 43.4 | ✅ |
| H3 | not_in_data | 你們有提供置物櫃嗎？要另外收費嗎？ | get_center_info / search_services / transfer_to_human | `search_services` | search_services, transfer_to_human | 3 | 24.1 | ✅ |
| H4 | out_of_scope | 幫我解這題微積分：x 平方的積分是多少？ | none | `none` | - | 1 | 22.0 | ✅ |
| H5 | mixed_language | 請問 aerial yoga 這週六還有 spot 嗎？ | list_group_classes | `list_group_classes` | list_group_classes | 2 | 24.5 | ✅ |

## Replies

**#H1 我和我媽媽週日下午 3 點想租匹克球場 2 小時，總共多少錢？** ✅

> 您和媽媽在 2026-11-01（週日）下午 3 點至 5 點租用 1 面匹克球場 2 小時，假日時段每小時 NT$350，總計 **NT$700**。若需要預訂或有其他問題，請告訴我！

**#H2 我想約下週三早上 10 點陳雅婷教練的一對一課，我叫林美君，電話 0955123456** ✅

> 以下是您預約的私人教練課程資訊，請確認無誤後回覆「確認」或「好」：
> 
> - 教練：陳雅婷（瑜珈專長）
> - 日期：2026-11-04（週三）
> - 時間：10:00～11:00
> - 費用：NT$1,200
> - 姓名：林美君
> - 電話：0955***456
> - 取消政策：課程前 24 小時內取消視為已使用
> 
> 請確認是否要預約此時段？確認後我將完成預約並為您提供預約編號。

**#H3 你們有提供置物櫃嗎？要另外收費嗎？** ✅

> 我目前沒有關於置物櫃是否提供或是否收費的資訊。已將您的問題轉交給人工客服協助處理。
> 
> 您可以撥打岡山運動中心電話：(07) 625-0666，或透過 LINE 官方帳號聯繫，服務時間為每日 06:00-22:00。案件編號：H202610261555-4e8a。祝您運動愉快！

**#H4 幫我解這題微積分：x 平方的積分是多少？** ✅

> 抱歉，我只能協助高雄市岡山運動中心的相關問題，如場地、票價、課程或私人教練預約。您有其他關於運動中心的問題嗎？

**#H5 請問 aerial yoga 這週六還有 spot 嗎？** ✅

> 這週六（2026-10-31）的空中瑜珈課程（G01）目前有 5 個名額可報名。  
> 課程需至少 4 人才能開班，目前已有 3 人報名，尚差 1 人即可成團。  
> 若您想參加，建議盡快報名以確保名額並協助課程成班。

