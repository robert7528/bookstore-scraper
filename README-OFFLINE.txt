HyPass (bookstore-scraper) 離線安裝包
=====================================

用途:封網 / 無網路的 Windows 機器,免 git、免 PyPI、免預先裝 Python。
適用:Windows Server 2012 R2 以上 / fetch-only。

安裝步驟
--------
1. 把整個 bookstore-scraper-offline 資料夾解壓 / 複製到目標機,建議放成:
       D:\bookstore-scraper
   (放別的路徑也能跑,但 SOP 慣例是 D:\bookstore-scraper)

2. 以「系統管理員」雙擊  install-offline.bat
   (會自動提權,跑 7 步:檢查 → 離線裝 Python → venv → 離線裝套件 →
    放 WinSW → 裝並啟動服務 → 驗證 8101)

3. 看到「離線安裝完成!」+ Fetch API: HTTP 200 (listening) 即成功。

驗證(系統管理員 PowerShell)
-----------------------------
   Invoke-WebRequest "http://127.0.0.1:8101/fetch/https://www.books.com.tw/products/0011012422" -UseBasicParsing | Select StatusCode
   回 200 = OK。

服務管理
--------
   cd D:\bookstore-scraper
   .venv\Scripts\python -m src.cli service status
   .venv\Scripts\python -m src.cli service start
   .venv\Scripts\python -m src.cli service stop

移除 / 重裝
-----------
   以「系統管理員」雙擊  uninstall-offline.bat
   (停服務 → 移除服務 → 刪 .venv / logs / WinSW;Python 預設保留,重裝會沿用)
   要連 Python 一起移除(完整清除):
       powershell -ExecutionPolicy Bypass -File .\uninstall-offline.ps1 -RemovePython
   注意:移除請用這支,別只手動刪資料夾 —— 只刪資料夾會留下 Python 註冊殘留,
         之後重裝可能被擋。

注意事項
--------
- Server 2012 R2:Python 3.12 需要 UCRT。若安裝過程中 python 跑不起來、噴
  api-ms-win-crt-*.dll,請先裝 KB2999226 或跑 Windows Update,再重跑 install-offline.bat。
- 本包是 fetch-only。curl_cffi(Layer 1)在離線環境正常運作。
  瀏覽器 fallback(undetected-chromedriver 過 CF)在封網環境無法下載 chromedriver,
  不會動作 —— fetch-only 抓博客來 / ISBN 這類無 CF 的站不需要它。
- 內含 Python 3.12.10、WinSW v2.12.0、所有相依套件 wheels(cp312/win_amd64),
  全部離線安裝,過程不連任何外網。

內容物
------
  install-offline.bat / install-offline.ps1   離線安裝腳本
  uninstall-offline.bat / uninstall-offline.ps1 移除腳本
  offline\python-3.12.10-amd64.exe            Python 安裝檔
  offline\WinSW.NET4.exe                       服務包裝器
  offline\wheels\*.whl                         離線相依套件
  src\ configs\ deploy\ pyproject.toml ...     應用程式本體
