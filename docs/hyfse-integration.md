# HyFSE Python Driver 整合說明

## 概述

bookstore-scraper 提供 HTTP API 服務，作為 HyFSE 的 Python Driver，專門處理 Go 原生 curl 無法突破的網站（如 Cloudflare Bot Fight Mode、Managed Challenge、TLS 指紋檢測等）。

HyFSE 將組好的 HTTP 請求透過 API 發送給本服務，本服務**自動判斷**防護等級，選擇最適合的方式取得內容：

```
HyFSE (Go)
  ├── driver: curl       → Go 原生 HTTP（現行）
  ├── driver: selenium   → Chrome Driver（現行）
  └── driver: python     → POST http://{host}:{port}/request（新增）
        │
        ▼
  bookstore-scraper (Python/FastAPI)
        │
        ├── Layer 1: curl_cffi (TLS 指紋偽造)
        │     ↓ 偵測到 Cloudflare challenge 自動 fallback
        └── Layer 2: Playwright Browser Pool (真實瀏覽器)
```

### 與 HyFSE Selenium 的差異

| | HyFSE Selenium | bookstore-scraper |
|---|---|---|
| 搜尋頁 | 也開瀏覽器（浪費資源） | **curl_cffi 直接過，不開瀏覽器** |
| 詳細頁 | 能過 | 能過（自動 fallback 到 Playwright） |
| 資源管理 | Chrome 常駐 + 多 tab 累積 | **Browser Pool：共用一個進程，tab 用完即關** |
| Tab 數控制 | 無限制（可能 OOM） | **Semaphore 限制同時最多 N 個 tab** |
| 閒置時 | Chrome 持續佔用 | **閒置 5 分鐘自動關閉 Chromium** |

---

## 服務資訊

| 項目 | 說明 |
|---|---|
| 預設 Port | 8101（可在 `configs/settings.yaml` 修改） |
| API 文件 | `http://{host}:{port}/docs`（Swagger UI） |
| 健康檢查 | `GET http://{host}:{port}/` |
| 資源監控 | `GET http://{host}:{port}/monitor` |

---

## API 端點

### 1. `POST /request` — 主要呼叫端點

HyFSE 將完整的 HTTP 請求交給本服務發送。自動偵測 Cloudflare challenge 並 fallback 到瀏覽器。

#### Request

```json
{
    "url": "https://search.books.com.tw/search/query/key/python/cat/all",
    "method": "GET",
    "headers": {},
    "body": "",
    "timeout": 30,
    "impersonate": "chrome136",
    "session_id": ""
}
```

| 欄位 | 類型 | 必填 | 預設值 | 說明 |
|---|---|---|---|---|
| `url` | string | 是 | — | 完整的請求 URL（HyFSE 組好的，含編碼後的參數） |
| `method` | string | 否 | `"GET"` | HTTP 方法，支援 `GET` / `POST` |
| `headers` | object | 否 | `{}` | 自訂 HTTP headers（如 Cookie、User-Agent 等） |
| `body` | string | 否 | `""` | POST 時的 request body |
| `timeout` | int | 否 | `30` | 請求逾時秒數 |
| `impersonate` | string | 否 | `"chrome136"` | TLS 指紋偽造的瀏覽器版本 |
| `session_id` | string | 否 | `""` | Session ID，用於多步驟請求保持 Cookie（詳見 Session 管理章節） |

#### Response

```json
{
    "status_code": 200,
    "headers": {
        "Content-Type": "text/html; charset=utf-8",
        "Set-Cookie": "..."
    },
    "body": "<html>...",
    "url": "https://search.books.com.tw/...",
    "elapsed": 0.43,
    "exception": "",
    "session_id": ""
}
```

| 欄位 | 類型 | 說明 |
|---|---|---|
| `status_code` | int | HTTP 狀態碼，`0` 表示連線失敗 |
| `headers` | object | 回應 headers（含 Set-Cookie） |
| `body` | string | 回應 body（HTML/JSON/XML 原文） |
| `url` | string | 最終 URL（如有 redirect 會與請求 URL 不同） |
| `elapsed` | float | 請求耗時（秒） |
| `exception` | string | 錯誤訊息，空字串表示成功 |
| `session_id` | string | 使用的 Session ID（僅在請求有帶 session_id 時回傳） |

#### 自動 Fallback 行為

呼叫端不需要做任何處理，服務內部自動判斷：

```
POST /request {"url": "https://search.books.com.tw/..."}
  → curl_cffi 發送 → 200 OK → 直接回傳（~0.4s）

POST /request {"url": "https://www.books.com.tw/products/..."}
  → curl_cffi 發送 → 偵測到 Cloudflare challenge
  → 自動 fallback 到 Browser Pool → 等待 challenge 解決 → 回傳（~2-4s）
```

#### 呼叫範例

**curl（Linux / macOS）**

```bash
# 搜尋頁（curl_cffi 直接過）
curl -s -X POST http://localhost:8101/request \
  -H "Content-Type: application/json" \
  -d '{"url":"https://search.books.com.tw/search/query/key/python/cat/all","method":"GET"}'

# 詳細頁（自動 fallback 到 Browser）
curl -s -X POST http://localhost:8101/request \
  -H "Content-Type: application/json" \
  -d '{"url":"https://www.books.com.tw/products/0011043725","method":"GET"}'

# POST 請求（帶 body）
curl -s -X POST http://localhost:8101/request \
  -H "Content-Type: application/json" \
  -d '{"url":"https://example.com/api/search","method":"POST","body":"keyword=python&page=1"}'

# 帶自訂 headers
curl -s -X POST http://localhost:8101/request \
  -H "Content-Type: application/json" \
  -d '{"url":"https://example.com","method":"GET","headers":{"Cookie":"session=abc123","Referer":"https://example.com"}}'

# 有狀態請求（保持 Cookie，多步驟）
curl -s -X POST http://localhost:8101/request \
  -H "Content-Type: application/json" \
  -d '{"url":"https://example.com/login","method":"POST","body":"user=xxx&pass=yyy","session_id":"mysite_user1"}'

curl -s -X POST http://localhost:8101/request \
  -H "Content-Type: application/json" \
  -d '{"url":"https://example.com/search?q=test","method":"GET","session_id":"mysite_user1"}'
```

**PowerShell（Windows）**

```powershell
# 搜尋頁
Invoke-RestMethod -Uri http://localhost:8101/request -Method POST `
  -ContentType "application/json" `
  -Body '{"url":"https://search.books.com.tw/search/query/key/python/cat/all","method":"GET"}'

# 詳細頁
Invoke-RestMethod -Uri http://localhost:8101/request -Method POST `
  -ContentType "application/json" `
  -Body '{"url":"https://www.books.com.tw/products/0011043725","method":"GET"}'
```

**Python**

```python
import requests

# 搜尋頁
resp = requests.post("http://localhost:8101/request", json={
    "url": "https://search.books.com.tw/search/query/key/python/cat/all",
    "method": "GET",
})
data = resp.json()
print(data["status_code"], len(data["body"]), "bytes")

# 詳細頁（自動 fallback）
resp = requests.post("http://localhost:8101/request", json={
    "url": "https://www.books.com.tw/products/0011043725",
    "method": "GET",
})
data = resp.json()
print(data["status_code"], len(data["body"]), "bytes")
```

**Go（HyFSE 整合）**

```go
// 見下方「Go 端整合範例」章節
resp, err := PythonDriver(cfg, ProxyRequest{
    URL:    "https://www.books.com.tw/products/0011043725",
    Method: "GET",
})
```

**監控 API**

```bash
# 目前資源狀態
curl -s http://localhost:8101/monitor | python3 -m json.tool

# 最近 10 筆請求的資源紀錄
curl -s "http://localhost:8101/monitor/history?limit=10" | python3 -m json.tool

# 查看活躍 Session
curl -s http://localhost:8101/sessions | python3 -m json.tool

# 手動關閉 Session
curl -s -X DELETE http://localhost:8101/session/mysite_user1

# 健康檢查
curl -s http://localhost:8101/
```

### 2. `GET /monitor` — 目前資源使用狀態

```json
{
    "psutil_available": true,
    "cpu_percent": 5.1,
    "memory_mb": 72.3,
    "memory_percent": 0.5,
    "open_fds": 19,
    "threads": 5,
    "history_count": 1
}
```

### 3. `GET /monitor/history?limit=20` — 請求資源使用紀錄

每次 `/request` 呼叫都會記錄 before/after 的資源快照，最多保留最近 100 筆。

```json
{
    "records": [
        {
            "url": "https://www.books.com.tw/products/0011043725",
            "method": "GET",
            "driver": "browser",
            "status_code": 200,
            "elapsed": 2.569,
            "resources": {
                "before": {
                    "cpu_percent": 0.0,
                    "memory_mb": 59.5,
                    "memory_percent": 0.4,
                    "open_fds": 15,
                    "threads": 5
                },
                "after": {
                    "cpu_percent": 5.1,
                    "memory_mb": 72.3,
                    "memory_percent": 0.5,
                    "open_fds": 19,
                    "threads": 5
                },
                "delta_memory_mb": 12.8
            }
        }
    ]
}
```

### 4. `GET /sessions` — 查看活躍 Session

```json
{
    "sessions": [
        {"session_id": "33445_shared", "age": 12.5}
    ]
}
```

### 5. `DELETE /session/{session_id}` — 手動關閉 Session

```json
{"closed": true}
```

### 6. `GET /` — 健康檢查

```json
{"service": "bookstore-scraper", "version": "0.4.0"}
```

---

## Go 端整合範例

### 基本整合（無狀態）

適用於單次請求的場景，每次請求獨立，不保留 Cookie。

```go
package hyfse

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
)

// PythonDriverConfig 設定
type PythonDriverConfig struct {
	BaseURL string // e.g. "http://localhost:8101"
	Timeout int    // 預設 30
}

// ProxyRequest 請求結構
type ProxyRequest struct {
	URL         string            `json:"url"`
	Method      string            `json:"method"`
	Headers     map[string]string `json:"headers,omitempty"`
	Body        string            `json:"body,omitempty"`
	Timeout     int               `json:"timeout,omitempty"`
	Impersonate string            `json:"impersonate,omitempty"`
	SessionID   string            `json:"session_id,omitempty"`
}

// ProxyResponse 回應結構
type ProxyResponse struct {
	StatusCode int               `json:"status_code"`
	Headers    map[string]string `json:"headers"`
	Body       string            `json:"body"`
	URL        string            `json:"url"`
	Elapsed    float64           `json:"elapsed"`
	Exception  string            `json:"exception"`
	SessionID  string            `json:"session_id"`
}

// PythonDriver 呼叫 bookstore-scraper API
func PythonDriver(cfg PythonDriverConfig, req ProxyRequest) (*ProxyResponse, error) {
	if req.Timeout == 0 {
		req.Timeout = cfg.Timeout
	}

	payload, err := json.Marshal(req)
	if err != nil {
		return nil, fmt.Errorf("marshal request: %w", err)
	}

	resp, err := http.Post(cfg.BaseURL+"/request", "application/json", bytes.NewReader(payload))
	if err != nil {
		return nil, fmt.Errorf("call python driver: %w", err)
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("read response: %w", err)
	}

	var result ProxyResponse
	if err := json.Unmarshal(body, &result); err != nil {
		return nil, fmt.Errorf("unmarshal response: %w", err)
	}

	if result.Exception != "" {
		return &result, fmt.Errorf("python driver error: %s", result.Exception)
	}

	return &result, nil
}
```

### 使用範例

```go
cfg := PythonDriverConfig{
    BaseURL: "http://localhost:8101",
    Timeout: 30,
}

// GET 請求 — 搜尋頁（curl_cffi 直接過）或詳細頁（自動 fallback 到瀏覽器）
resp, err := PythonDriver(cfg, ProxyRequest{
    URL:    "https://www.books.com.tw/products/0011043725",
    Method: "GET",
})
if err != nil {
    log.Fatal(err)
}
// resp.Body 即為 HTML 內容，交給 HyFSE parser 處理
fmt.Println("Status:", resp.StatusCode)
fmt.Println("Body length:", len(resp.Body))
```

---

## Session 管理

### 何時需要 Session

當目標網站需要多步驟請求且依賴 Cookie 時（例如先登入再查詢），需要使用 Session 讓 Cookie 在多次請求間自動保持。

### 使用方式

在請求中帶入 `session_id`，同一個 `session_id` 的請求會共用同一個 Cookie Jar。

```go
// Step 1: 登入（取得 Cookie）
resp1, _ := PythonDriver(cfg, ProxyRequest{
    URL:       "https://example.com/login",
    Method:    "POST",
    Body:      "user=xxx&pass=yyy",
    SessionID: "33445_user1",
})

// Step 2: 搜尋（自動帶上 Step 1 的 Cookie）
resp2, _ := PythonDriver(cfg, ProxyRequest{
    URL:       "https://example.com/search?q=test",
    Method:    "GET",
    SessionID: "33445_user1",
})
```

### 對應 HyFSE sharecookie 設定

| HyFSE 設定 | session_id 建議格式 | 說明 |
|---|---|---|
| `sharecookie: false` | `{fid}_{userId}` | 每個使用者獨立 Session |
| `sharecookie: true` | `{fid}_shared` | 所有使用者共用 Session |
| 不需要 Cookie | 不帶 `session_id` | 每次請求獨立，用完即銷毀 |

### Session 生命週期

- Session 閒置超過 **5 分鐘**自動過期（可在 `configs/settings.yaml` 調整 `session.ttl`）
- 不需要主動呼叫 DELETE 關閉 Session，自動過期即可
- 如需立即釋放：`DELETE /session/{session_id}`

---

## HyFSE 設定檔範例

在 HyFSE 的設定檔中，將 `driver` 改為 `python` 即可使用本服務：

```json
{
    "search": {
        "driver": "python",
        "steps": [
            {
                "command": "open",
                "target": "https://search.books.com.tw/search/query/key/##searchkey##/cat/all",
                "value": "",
                "options": {}
            }
        ]
    }
}
```

HyFSE Go 端在 `driver == "python"` 時：

1. 照常進行參數編碼、欄位映射、條件組裝
2. 將組好的完整 URL 透過 `POST /request` 發送
3. 將回傳的 `body` 交給 parser 處理（goquery / json / jsonp）
4. 其餘流程（解析、輸出）不變

---

## 部署

### Linux (systemd)

```bash
# 一鍵部署（含 Playwright + Chromium）
sudo bash -c "$(curl -fsSL https://raw.githubusercontent.com/robert7528/bookstore-scraper/master/deploy/deploy-linux.sh)"

# 服務管理
systemctl status bookstore-scraper
systemctl restart bookstore-scraper
journalctl -u bookstore-scraper -f
```

### Windows (NSSM)

```powershell
# 雙擊 deploy/install.bat 或執行
powershell -ExecutionPolicy Bypass -File deploy\deploy-windows.ps1

# 服務管理
python -m src.cli service status
python -m src.cli service restart
```

### 設定檔

`configs/settings.yaml`：

```yaml
server:
  host: "0.0.0.0"
  port: 8101              # 服務 port

scraper:
  impersonate: "chrome136"  # TLS 指紋版本
  timeout: 30               # 請求逾時 (秒)

session:
  ttl: 300                  # Session 過期秒數

browser:
  max_tabs: 3               # 同時最多幾個 tab（防止 CPU/RAM 暴漲）
  idle_timeout: 300          # 瀏覽器閒置多久自動關閉 (秒)
```

修改後重啟服務即可生效。

---

## 資源監控

### 監控端點

| 端點 | 說明 |
|---|---|
| `GET /monitor` | 目前 CPU、Memory、Thread 狀態 |
| `GET /monitor/history?limit=20` | 最近 N 次請求的資源使用紀錄 |

### 實測數據（Linux Rocky 9, 16GB RAM）

| 請求類型 | Driver | 耗時 | Memory delta | CPU |
|---|---|---|---|---|
| 搜尋頁 | curl | ~0.4s | +4.8 MB | ~3% |
| 詳細頁（首次） | browser | ~2.6s | +12.8 MB | ~5% |
| 詳細頁（browser 已啟動） | browser | ~1-2s | +1-2 MB | ~3% |
| 閒置時 | — | — | ~72 MB 總佔用 | <1% |

### Browser Pool 資源控制

| 狀態 | Chromium 進程 | Tab 數 | 說明 |
|---|---|---|---|
| 無 challenge 請求 | 不啟動 | 0 | 僅 curl_cffi，零瀏覽器開銷 |
| 第一次 challenge | 啟動 | 1（用完關） | 首次啟動 ~1-2s |
| 連續 challenge | 已在跑 | 最多 N 個 | N = `browser.max_tabs` |
| 閒置超過設定時間 | 自動關閉 | 0 | `browser.idle_timeout` 秒 |

---

## 錯誤處理

### 判斷請求是否成功

```go
resp, err := PythonDriver(cfg, req)

// 1. 連線失敗（服務沒啟動、網路不通）
if err != nil {
    // fallback 到其他 driver 或回報錯誤
}

// 2. 請求失敗（目標網站有問題）
if resp.Exception != "" {
    // 連線逾時、DNS 解析失敗等
}

// 3. HTTP 錯誤
if resp.StatusCode == 403 {
    // 被目標網站封鎖（curl + browser 都無法通過）
}

// 4. 成功
if resp.StatusCode == 200 && resp.Exception == "" {
    // resp.Body 交給 parser
}
```

### 服務健康檢查

建議 HyFSE 啟動時檢查 Python Driver 是否可用：

```go
resp, err := http.Get("http://localhost:8101/")
if err != nil || resp.StatusCode != 200 {
    log.Warn("Python Driver not available, will use curl/selenium only")
}
```

---

## 支援的 impersonate 版本

| 值 | 說明 |
|---|---|
| `chrome116` | Chrome 116 |
| `chrome120` | Chrome 120 |
| `chrome123` | Chrome 123 |
| `chrome124` | Chrome 124 |
| `chrome131` | Chrome 131 |
| `chrome136` | Chrome 136（預設，推薦） |

如果目標網站對特定瀏覽器版本有更好的相容性，可在請求中指定 `impersonate` 參數。
