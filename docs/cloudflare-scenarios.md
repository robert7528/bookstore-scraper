# Bookstore-Scraper 完整情境與方案對照

## IP 信譽層級 vs 方案效果

| CF 防護層級 | 觸發條件 | curl_cffi | UC headless | UC + Xvfb | Playwright |
|-------------|----------|-----------|-------------|-----------|------------|
| 無挑戰 | 乾淨 IP、正常頻率 | 通過 (0.03-0.1s) | 不需要 | 不需要 | 不需要 |
| JS Challenge (五秒盾) | 輕度可疑 | 通過 (有 cookie) | 通過 | 通過 | 通過 |
| Managed Challenge | 中度可疑、機房 IP | 失敗 | 通過 | 通過 | 通過 |
| Turnstile 互動驗證 | 高度可疑、頻繁請求 | 失敗 | 失敗 | **通過 (3-7s)** | 失敗 |
| hCaptcha / reCAPTCHA | 極高風險 | 失敗 | 失敗 | 失敗 | 失敗 |
| IP 封鎖 (Block) | 黑名單 | 失敗 | 失敗 | 失敗 | 失敗 |

> UC = undetected-chromedriver

---

## 自動 Fallback 流程

```
請求進來
  |
  v
Layer 1: curl_cffi + session 重用 (0.03-0.5s)
  |-- 通過 --> 回傳
  |-- 失敗 --> 繼續
  v
Layer 2: undetected Chrome + Xvfb (3-7s)
  |-- 通過 --> 回傳
  |-- 失敗 --> 回傳 curl 原始結果（錯誤）
```

---

## 各方案為何成功/失敗

| 方案 | Turnstile 結果 | 原因 |
|------|---------------|------|
| curl_cffi | 失敗 | 無 JS 執行，無法解 challenge |
| Playwright + headless | 失敗 | CDP 協議被 Turnstile 偵測 |
| Playwright + Xvfb (非 headless) | 失敗 | CDP 協議仍被偵測，跟 headless 無關 |
| Playwright + Chrome (非 Chromium) | 失敗 | 還是 CDP 控制 |
| Playwright 點擊 Turnstile checkbox | 失敗 | CDP 控制的點擊被 Turnstile 拒絕 |
| undetected Chrome + headless | 失敗 | headless 模式被 Turnstile 偵測 |
| **undetected Chrome + Xvfb** | **通過** | **修補 CDP 標記 + 非 headless + 虛擬顯示** |

---

## 部署設定對照

| 客戶端情況 | settings.yaml 設定 | 需安裝 |
|-----------|-------------------|--------|
| 乾淨 IP（大多數客戶） | `engine: "undetected"`, `headless: false` | Python + pip 套件 + Chrome + Xvfb |
| 確定不會被 challenge | `engine: "undetected"`, `headless: true` | Python + pip 套件（Chrome 不會啟動） |
| 最小安裝 | 無 browser fallback | Python + `pip install -e .`（只有 curl_cffi） |

---

## 資源使用

| 狀態 | 記憶體 | CPU |
|------|--------|-----|
| 只有 curl（正常情況） | ~65MB | <5% |
| Chrome 啟動中 | ~95-100MB | 10-20% |
| Chrome idle 5 分鐘後 | 自動關閉，回到 ~65MB | <1% |
| Chrome 最長存活 | 2 小時強制重啟 | - |

---

## 已驗證環境

| 環境 | IP 狀態 | curl | browser fallback | 結果 |
|------|---------|------|-----------------|------|
| 10.30.0.73（公司測試機） | 正常 | 直接過 0.03s | 不觸發 | 完美 |
| tnode1（客戶測試機） | 冷卻後 | 直接過 | 偶爾觸發，3-7s 解決 | 正常 |
| tnode1（密集測試時） | 高度標記 | 失敗 | Turnstile，UC+Xvfb 通過 | 正常 |

---

## settings.yaml 完整參考

```yaml
scraper:
  # TLS 指紋: "auto" (自動偵測 Chrome 版本) 或 "chrome136"
  impersonate: "auto"
  timeout: 30
  # 同一 domain 最小請求間隔（秒）
  rate_limit_interval: 0.5

session:
  ttl: 300

browser:
  # 引擎: "undetected" (推薦) | "playwright" (備用)
  engine: "undetected"
  # 非 headless 才能過 Turnstile（需搭配 Xvfb）
  headless: false
  # Playwright 用: "auto" | "chrome" | "chromium"
  channel: "auto"
  max_tabs: 3
  idle_timeout: 300
  # 強制重啟 Chrome 防記憶體洩漏（秒）
  max_lifetime: 7200
  challenge_timeout: 15
```

---

## 安裝指令

### 完整安裝（建議）

```bash
git clone https://github.com/robert7528/bookstore-scraper.git /opt/bookstore-scraper
cd /opt/bookstore-scraper

# Python 3.11（Ubuntu 20.04 用 conda）
/opt/miniconda3/bin/conda create -y -p .venv python=3.11

# Python 套件
.venv/bin/pip install -e ".[undetected]"

# Google Chrome
wget -q -O /tmp/chrome.deb https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
apt install -y /tmp/chrome.deb && rm /tmp/chrome.deb

# Xvfb
apt install -y xvfb

# 啟動服務
.venv/bin/python -m src.cli service install
.venv/bin/python -m src.cli service start
```

### 最小安裝（只有 curl）

```bash
.venv/bin/pip install -e .
```

### Playwright（備用，需要時才裝）

```bash
.venv/bin/pip install playwright
.venv/bin/python -m playwright install chromium
# 約 756MB
```

---

## 管理指令

```bash
# 服務管理
systemctl status bookstore-scraper
systemctl restart bookstore-scraper
journalctl -u bookstore-scraper -f

# 更新部署
cd /opt/bookstore-scraper && git pull && systemctl restart bookstore-scraper

# 監控
curl -s http://localhost:8101/monitor

# Chrome 進程檢查
ps aux | grep "google-chrome" | grep -v grep | wc -l      # bookstore-scraper
ps aux | grep "/usr/lib/chromium" | grep -v grep | wc -l   # HyFSE (rod)
ps aux | grep chrome | grep -v grep | awk '$8=="Z" {c++} END {print c}' # zombie
```
