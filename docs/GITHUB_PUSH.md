# 推到 GitHub — 操作單

## 先想清楚：這個 repo 有什麼

追蹤進 git 的檔案共 134 個、`.git` 僅 1.8 MB，**沒有任何信件、附件、PDF、投影片**（全部在 `.gitignore`）。
但有這些**個資**：

| 檔案 | 內容 |
|---|---|
| `students.yaml`、`alumni.yaml` | 學生／校友的中文姓名與 email |
| `students/*/wiki/*.md` | 逐週進度時間軸、停寄天數、`at-risk`／`off-track` 判定 |
| `students/*/goals.md` | 對個別學生的期待與「對照現況＝落後」 |
| `papers/index.jsonl` | 誰在某一天讀過哪篇論文（`sources`: slug + date） |

**這些是對具體學生的評價，屬個人資料。除非你確定要公開，否則用 private。**
`papers/files/`（1.24 GB PDF）已在 `.gitignore`，不會被推上去——這既是授權問題也是體積問題。

## 方案 A：private repo（建議）

```bash
# 1. 登入（你在瀏覽器完成，agent 不經手憑證）
gh auth login --hostname github.com --git-protocol https --web

# 2. 確認登入身分
gh auth status

# 3. 建立 private repo 並推上去（把 <你的帳號> 換掉）
cd /e/Hermes_projects/group_discussion
gh repo create <你的帳號>/group_discussion --private --source=. --remote=origin --push
```

若 repo 已存在，只要設定 remote 再推：

```bash
git remote add origin https://github.com/<你的帳號>/group_discussion.git
git branch -M master
git push -u origin master
```

## 方案 B：private 主庫 + 去識別化 public demo

主庫照方案 A；另外複製一份、把可辨識欄位換掉再公開：

- `students.yaml`／`alumni.yaml`：姓名 → `student_01`…、email → 移除（只留 slug）
- 各 wiki 的姓名 → slug；保留方法論、缺口分析、判定規則
- `papers/index.jsonl`：`sources[].slug` 已換掉即無從回推

這件事要另外做一支 `scripts/anonymize.py`，目前**尚未實作**。

## 每次推之前的三個檢查

```bash
git status --short                      # 不該出現 .eml / .pdf / .pptx
git ls-files | grep -cE '\.(eml|pdf|pptx|xlsx)$'   # 必須是 0
git count-objects -vH | grep size-pack             # 應該仍遠小於 10 MB
```

## 已知的坑

- **不要把 `papers/files/` 加進版控**：1.24 GB，且是出版社授權內容。
- **Windows 換行**：repo 內多次出現 `CRLF will be replaced by LF` 警告，commit 一律用
  `git -c core.autocrlf=false commit`，避免無謂的整檔差異。
- **`gh` 在 git-bash 下**可用；若 `gh auth login --web` 開不了瀏覽器，改執行
  `gh auth login --with-token`（token 由你自己貼，agent 不碰）。
