# B3 台股策略儀表板

網頁：https://chanting0904.github.io/tw-rsi-dashboard/

每週最後交易日早上產生收盤調倉計畫，使用前一交易日收盤資料。使用者在該日收盤調倉；未成交部分在下週第一個交易日補單。詳見 [每週調倉流程](s5_execution_sop.md)。

策略管理自己的模擬持倉，未連接券商帳戶。股票配置盡量維持 90–95%；交易限制與有效標的不足會列為提醒。

網站和 Telegram 均讀取 `output/latest_signals.json`。收盤前計畫標記為 `PRE_CLOSE_PLAN`，試算價格日期另有欄位；回測績效只包含已完成交易週。

## 本機

Python 3.12：`pip install -r requirements.txt`。

FinLab 認證使用 `FINLAB_TOKEN` 環境變數；本機也可用未提交的 `config.json`。Telegram 使用 `TG_TOKEN` 和 `TG_CHAT_ID`。

`python preflight.py` 執行離線檢查，不發送訊息或改寫模擬持倉。

## 自動化

GitHub Actions 於台北週一至週五 06:00 執行；只有該週最後交易日建立本週計畫。候選結果在暫存資料夾通過發布檢查後才更新正式資料。機器人提交後明確要求 GitHub Pages 重建。

`Python pipeline checks` 於 PR 與正式版程式更新時執行語法檢查和離線測試。

## 每週狀態與重試

頁首狀態依台北當日及休市表計算，每分鐘重新確認；完成狀態必須同時符合本週調倉日、前日資料基準及驗證通過。舊清單會明確標成最近一期模擬資料。

價格更新、訊號產生、網頁重建及 Pages 建置失敗時，最多執行三次：第一次失敗後等 60 秒，第二次後等 180 秒。每次仍須通過原本資料驗證。Telegram 傳送不整段重試，避免部分成功後重複發送。

最終失敗時，流程嘗試將失敗狀態發布到網站，保留已有訊號，再發告警。若 GitHub 或網站本身故障，失敗狀態也可能無法上網；頁面仍會依日期判斷舊清單，不能把上一週資料顯示為本週已完成。

## 引擎與資金試算

`b3_canonical.py` 是指令入口，計算在 `b3_engine/`：

- `config.py` / `context.py`：參數與資料容器。
- `market.py` / `selection.py`：資料與候選名單。
- `execution.py` / `simulation.py`：先賣後買、整股與現金、配置區間。
- `performance.py`：全部成交的盈虧、以去年年底為基準的年度報酬。
- `canonical.py` / `validation.py` / `pipeline.py`：Web/TG 輸出、驗證、發布。
- `allocation.py` / `capital_preview.js`：目標整股配置與模型費用試算，Python/JavaScript 有一致性檢查。

研究入口 `my_backtest_v7f_final_candidate.py` 使用相同引擎，輸出到 `research_output/`（可用 `--output-dir` 指定）；保留 `daily_trade_plan.csv`、`_final_candidate_report.txt` 與績效 JSON，沒有獨立的第二套成交或績效邏輯。

`python b3_canonical.py --report-only --capital 200000` 可模擬不同起始本金的完成期間績效；使用 `B3_OUTPUT_DIR` 將研究結果放在獨立目錄。非預設本金只能產生報表，不能替換共用週訊號。

網頁輸入的是目前用於目標配置的總資金。目標股數、預估現金與費用依實際整股試算；高價股票不足一股或目標權重本身不足時會明列提醒，不保證任何本金都能達到 90–95%。模型費率由參數提供，實際券商費用與成交另計。

盈虧比以已完成週期的實際模擬獲利金額／虧損金額计算；沒有已完成虧損時，JSON 使用 `null`，不填造假的 `999`。年度報酬包含跨年的第一週，並可與整段資產成長核對。
