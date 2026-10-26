# Tool selection test results (tool_selection_heldout.csv)

- Model: `Furen-large`, TOOL_MODE: `native`
- Run at: 2026-10-26 16:20 (Asia/Taipei)
- **Accuracy: 5/5 = 100%** (first tool called must be in acceptable_tools)
- **Average response time: 13.9 s** per question (max 23.2 s); endpoint cache bypassed (random user field per question)
- **Average time to first answer text: 11.9 s**

| # | Category | Question | Acceptable | First tool | All tools | LLM calls | Sec | First text | Result |
|---|---|---|---|---|---|---|---|---|---|
| H1 | maths | 我和我媽媽週日下午 3 點想租匹克球場 2 小時，總共多少錢？ | calculate_cost | `calculate_cost` | calculate_cost | 2 | 23.2 | 22.0 | ✅ |
| H2 | booking | 我想約下週三早上 10 點陳雅婷教練的一對一課，我叫林美君，電話 0955123456 | find_private_slots / book_private_session | `find_private_slots` | find_private_slots, book_private_session | 3 | 12.4 | 10.9 | ✅ |
| H3 | not_in_data | 你們有提供置物櫃嗎？要另外收費嗎？ | get_center_info / search_services / transfer_to_human | `search_services` | search_services, transfer_to_human | 3 | 11.5 | 6.1 | ✅ |
| H4 | out_of_scope | 幫我解這題微積分：x 平方的積分是多少？ | none | `none` | - | 1 | 7.6 | 7.0 | ✅ |
| H5 | mixed_language | 請問 aerial yoga 這週六還有 spot 嗎？ | list_group_classes | `list_group_classes` | list_group_classes | 2 | 14.7 | 13.5 | ✅ |

## Replies

**#H1 我和我媽媽週日下午 3 點想租匹克球場 2 小時，總共多少錢？** ✅

> 根據查詢結果，2026 年 10 月 31 日（週六）下午 3 點至 5 點租用 1 面匹克球場 2 小時，假日時段的單價為 NT$350／面／時，總計 **NT$700**。若您想確認或進行預訂，請提供您的姓名與手機號碼，我可為您進行下一步操作。

**#H2 我想約下週三早上 10 點陳雅婷教練的一對一課，我叫林美君，電話 0955123456** ✅

> 您好，以下是您預約的私人教練課程資訊：
> 
> - 教練：陳雅婷（瑜珈專長）  
> - 日期：2026-10-28（週三）  
> - 時間：10:00～11:00  
> - 費用：NT$1,200  
> - 姓名：林美君  
> - 電話：0955***456  
> - 取消政策：課程前 24 小時內取消視為已使用  
> 
> 請確認以上資訊無誤後，回覆「確認」或「好」以完成預約。

**#H3 你們有提供置物櫃嗎？要另外收費嗎？** ✅

> 我目前沒有關於置物櫃是否提供或收費的資訊。已為您轉接人工服務，您可以聯繫以下管道：
> 
> - 案號：H202610261619-b77e  
> - 電話：(07) 625-0666  
> - LINE：岡山運動中心 LINE 官方帳號  
> - 服務時間：06:00-22:00（每日）
> 
> 如需其他關於場館設施、票價或課程的問題，我很樂意協助。

**#H4 幫我解這題微積分：x 平方的積分是多少？** ✅

> 抱歉，我無法解答微積分問題，我的服務僅限於高雄市岡山運動中心的場地、票價、課程及私人教練預約等相關事項。如果您有關於運動中心的任何問題，我很樂意協助。

**#H5 請問 aerial yoga 這週六還有 spot 嗎？** ✅

> 這週六（2026-10-31）上午10:00‑11:00的空中瑜珈課程目前有5個空位，但課程尚未達到開班最低人數（需4人，目前已報名3人），還差1人才能成團。若在2026-10-30上午10:00前仍未達標，將會取消。建議您盡快報名以確保開班。

