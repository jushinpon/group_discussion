# ADR-0008：信件裡的論文集中到 `papers/`（單一全文庫）

- 狀態：已接受
- 日期：2026-11-01
- 決策者：Pon

## 背景

group meeting 信件附的論文 PDF 目前留在 `students/<slug>/attachments/<YYYY-MM>/`。
這有三個問題：

1. **同一篇 paper 會被重複保存**：跨屆、跨校友、同一組學生常讀同一批文獻，每個人的資料夾各存一份。
2. **畢業就找不到**：學生離開後，他讀過的文獻埋在`students/`底下，新學生不會知道那裡有東西。
3. **不能當檢索目標**：`--kind attachment` 只回答「誰讀過」，不能回答「實驗室有沒有這篇 paper」。

## 決策

### 1. 所有信件附的論文 PDF 複製到 `papers/`，sha256 去重

```
papers/files/<sha8>_<title>.pdf     位元組，全域只存一份
papers/text/<sha8>.txt              抽出的全文（檢索用；與 PDF 同樣不進 git）
papers/index.jsonl                  每篇一筆（sha256、檔名、DOI、來源、size、text_file）
```

- 主鍵是 **sha256**；同一篇論文再從別人信箱抵達時，只追加 `sources`（誰、哪一天、原檔名），不再存第二份。
- 次要鍵是 **DOI**：位元組不同但 DOI 相同的版本（出版社版／作者版）也視為同一篇。
- `sources` 讓「誰讀過這篇」變成可查詢的事實，而不是靠 wiki 記敘。

### 2. 什麼算「論文」

`papers/` 只收 PDF，且檔名不含 `meeting／進度／投影片／簡報／slide／deck／報告／海報／申請表` 等實驗室文書字樣。抽不到 400 字以上的 PDF（掃描匯出、單頁講義）也排除。

投影片、Excel、程式碼**不進** `papers/`，它們留在 `students/<slug>/attachments/` 或 `alumni/<slug>/attachments/`——那些是「某人做了什麼」的證據，與論文庫的用途不同。

論文 PDF **不再留在任何個人的資料夾**（Pon 2026-11-01）。校友收信時，PDF 先交給 `paper_db` 判定：只要是論文（new／linked／dup 任一），就只存在 `papers/files/`；只有 `paper_db` 拒收的（投影片匯出、抽不到文字的掃描件）才退回 `alumni/<slug>/attachments/`。

判定不能只看檔名：學生常把論文存成 `20240924_alumnus-37_meeting_paper.pdf`，若以 `meeting` 就排除會漏掉整批論文。因此檔名只排除明確的實驗室文書（投影片／簡報／報告／海報／申請表／作業／進度），真正的判定交給抽取後的**內文字數**（<400 字視為非論文）。這也是 `papers/text/` 存在的原因：全文只解析一次，之後檢索不必再碰 PDF。

### 3. 新信自動納入

`scripts/ingest.py`（在學學生，cron 每天 21:00）與 `scripts/ingest_alumni.py` 在存附件時呼叫 `paper_db.add()`。因此**不需要額外排程**：新信進來的那一刻論文就進庫。

校友的信箱政策（ADR-0007：大於 3 MB 的 PDF 不留在 `alumni/`）與本 ADR 不衝突：那類 PDF 改為**只進 `papers/`**，校友資料夾保持精簡，論文庫保留全文。

### 4. 版控邊界

| 進 git | 不進 git |
|---|---|
| `papers/index.jsonl`（可檢索目錄） | `papers/files/*.pdf`（授權與體積） |
| `papers/README.md`、ADR | |

## 後果

- `scripts/paper_db.py` 是唯一入口：`--scan <dir>` 回填既有附件、`--enrich` 從 PDF 內文補 DOI、`--stats` 看總量。
- 既有資料已回填：**201 篇、1.24 GB**（`students/` 底下 237 個 PDF 中 36 個判定為非論文）。
- 論文庫可用 `search.py --kind paper` 檢索（索引由 ingest 建立時一併寫入）。
- 未經 Pon 確認不得把 `papers/files/` 上傳到任何雲端或 git remote。
