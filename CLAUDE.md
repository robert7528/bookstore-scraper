# Bookstore Scraper — HyFSE Anti-Bot Fetch Proxy

## 專案定位
獨立 microservice，透過 HTTP API (`/fetch/{url}`) 供 HyFSE 呼叫，繞過 Cloudflare/WAF 保護。

## 架構
```
HyFSE (Go, port 8900, K8s pod)
  → http://<host-ip>:8101/fetch/{url}   (pod 內不能用 127.0.0.1)
    → bookstore-scraper (Python, port 8101, host)
      → Layer 1: curl_cffi + session 重用 (0.03-0.5s)
      → Layer 2: undetected-chromedriver + Xvfb (3-7s, Turnstile bypass)
```

## 關鍵檔案
- `src/main.py` — FastAPI 主程式，`/fetch/` endpoint
- `src/scraper/curl_scraper.py` — curl_cffi TLS fingerprint impersonation（auto 偵測 Chrome 版本 + fallback）
- `src/scraper/undetected_browser.py` — undetected-chromedriver (Turnstile bypass, max_lifetime, zombie 清理)
- `src/scraper/browser_pool.py` — Playwright browser pool（備用，目前不使用）
- `src/scraper/engine.py` — challenge 偵測邏輯（`<title>Just a moment...</title>` + body size < 15KB）
- `src/scraper/session_manager.py` — curl session 管理（per domain 重用，TTL 300s）
- `src/service.py` — Linux: systemd + Xvfb / Windows: WinSW
- `configs/settings.yaml` — 所有可調參數
- `docs/cloudflare-scenarios.md` — CF 防護層級 vs 方案對照表

## 開發注意事項
- 語言：用繁體中文回覆
- 改完程式碼要 push 到 GitHub
- 部署指令：`cd /opt/bookstore-scraper && git pull && systemctl restart bookstore-scraper`
- settings.yaml 在伺服器上可能有本地修改，git pull 前先 `git checkout configs/settings.yaml`
- impersonate: "auto" 會自動偵測 Chrome 版本，不支援時 fallback 到 chrome136
- 每次請求先 try curl，失敗才走 browser fallback（不做 domain cache）
- Playwright 已移除（省 756MB），程式碼保留，需要時 `pip install playwright`

## 不要做的事
- 不要改 HyFSE 的 Go 程式碼（David 維護）
- 不要把 challenge 偵測閾值設太大（15KB，150KB 會誤判正常頁面含 CF script）
- 不要用 Playwright 處理 Turnstile（CDP 會被偵測，用 undetected-chromedriver）
- 403 status code 不代表 challenge，要看內容
- 不要用 NSSM（Windows 改用 WinSW）

## 部署環境
- 10.30.0.73 — 公司測試機（HyFSE + fetch proxy）
- tnode1 (172.19.1.4) — 客戶測試機 typl（Ubuntu 20.04, conda Python 3.11）
- node1 (140.125.246.14) — 客戶端（Rocky Linux 8, conda Python 3.11）

## 系統需求
- Python 3.11+（Ubuntu 20.04 / Rocky 8 用 conda）
- Google Chrome（browser fallback 用）
- Xvfb（Linux headless: false 時，過 Turnstile 必須）
- `pip install -e ".[undetected]"`
- Rocky/CentOS 的 kubernetes repo 可能壞掉，dnf 加 `--disablerepo=kubernetes`
