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
