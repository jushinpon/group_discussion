# papers/ — 實驗室論文全文庫

信件（在學學生與校友）附的研究論文 PDF 會自動集中到這裡，**同一篇只存一份**。

```
papers/
├── files/<sha8>_<title>.pdf   全文（不進 git）
├── index.jsonl                目錄：sha256、DOI、來源（誰／哪一天）（進 git）
└── README.md                  本檔
```

## 為什麼放在這裡

同一批文獻會被不同屆、不同學生重複寄來。若留在 `students/<slug>/attachments/`，就只有當初那位學生找得到，畢業後更是無聲消失。放進這裡之後：

- 同一篇 paper 不論誰寄，只佔一份空間
- `sources` 記錄了誰在什麼時候讀過它，這是可查詢的事實
- 新學生可以問「實驗室有沒有這篇」而不必先知道誰讀過

## 用法

```bash
python scripts/paper_db.py --stats            # 總量、有 DOI 的篇數
python scripts/paper_db.py --scan students    # 回填既有附件（會去重）
python scripts/paper_db.py --enrich           # 從 PDF 內文補 DOI
python scripts/search.py "high-entropy" --kind paper
```

新信不需要手動處理：`scripts/ingest.py` 與 `scripts/ingest_alumni.py` 存附件時會自動寫入。

## 什麼會進來、什麼不會

| 會 | 不會 |
|---|---|
| 期刊／會議論文 PDF | meeting 投影片（`.pptx`／匯出的 PDF） |
| 預印本（arXiv 等） | 進度報告 `.xlsx` |
| 學位論文（若以附件形式寄來） | 申請表、收據、海報、簽到表 |
| 抽得到 400 字以上內文的 PDF | 掃描出來只有圖、幾乎沒有文字的 PDF |

投影片與 Excel 留在 `students/<slug>/attachments/` 或 `alumni/<slug>/attachments/`，因為那些是「某人做了什麼」的證據，不是文獻。

## 授權

`files/` 不進 git，也不上傳任何 remote：論文 PDF 受出版社授權限制。版控只追 `index.jsonl`。
