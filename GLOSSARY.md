# MEL 研究進度系統

把實驗室群組信箱的師生討論，轉成每位學生可追蹤的研究進度記錄：一人一份 LLM 維護的
wiki、一人一份研究目標、一份可檢索的語料索引。存在的理由是「學生寄來的進度信」與
「我對他下一步的判斷」目前只散在信箱裡，無法累積。

## Language

**melgroup**（群組信箱）:
`<redacted-email>`，實驗室師生討論研究進度的管道，也是本系統唯一的資料來源。
_Avoid_: 實驗室信箱, 群組, mailing list

**Student**（學生）:
目前在學、且在最近四個月內寄過信到 melgroup 的寄件者；每位 Student 對應一個 slug、一個資料夾、一份 wiki、一份 goals。
_Avoid_: 成員, 研究生, user

**Sender**（寄件者）:
一封郵件的 From 位址，小寫正規化後的字串。Sender 是可觀察的證據，Student 是被判定的身分；多個 Sender 可以指向同一個 Student。
_Avoid_: 帳號, 作者, 寄件人

**Student slug**（學生代號）:
`students.yaml` 中指定的穩定英數識別，用作資料夾名、wiki 檔名與 git 歷史的錨點。
_Avoid_: email, 學號, 中文姓名

**Progress mail**（進度信）:
學生寄到 melgroup、內容含研究進展（數據、方法、遭遇的問題、下一步）的郵件；是 wiki 更新的輸入單位。
_Avoid_: 通知, 討論串, 信件

**LLM wiki**（LLM 維護的 wiki）:
每位學生一份、由 agent 增量修改而非重寫的 Obsidian 相容 markdown 子庫；條目記錄題目、方法、數據、結論與待辦，並回指來源郵件的 Message-ID 與日期。
_Avoid_: 筆記, summary, 週報

**Attachment**（附件）:
進度信附帶的原始檔案。原檔保存一份（SHA-256 去重），並抽取純文字供檢索。
_Avoid_: 檔案, 資料, 文件

**Ingestion run**（收信回合）:
一次完整的「拉新信 → 歸戶到 Student → 下載附件 → 更新 wiki 與索引 → git commit」執行。
_Avoid_: 同步, 掃描, update

**Ingestion window**（攝入窗口）:
某位 Student 的資料起點，記在 `students.yaml` 的 `window_start`。全域預設為 2026-07-01，已進入博士班多年的學生可各自往前延伸到入學時間；窗口之前的信件不攝入，且執行時會逐封印出 skip 原因。
_Avoid_: 時間範圍, 期限, cutoff

**Retrieval index**（檢索索引）:
`index/` 下由 SQLite FTS5 建立的全文索引，覆蓋郵件正文、附件萃取文字與 wiki 條目。
_Avoid_: 向量庫, RAG DB, 資料庫

**Agentic RAG**（主動檢索）:
由 agent 自行決定檢索策略（先查哪位 Student、哪段時間、哪些附件），並依中間結果多輪追問的檢索流程，而非單次向量相似度查詢。
_Avoid_: RAG, 向量檢索, 語意搜尋

**Research goal**（研究目標）:
由 agent 從該 Student 已寄來的資料生成、並在每次有新證據時動態修訂的具體可驗證目標；若學生提供了計畫書，目標以計畫書內容為準。每條目標記錄來源（Message-ID 或附件）與修訂日期，是進度分析的對照基準。
_Avoid_: 方向, 題目, plan, 承諾
