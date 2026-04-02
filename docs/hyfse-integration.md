# HyFSE Python Driver 整合說明

## 概述

bookstore-scraper 提供 HTTP API 服務，作為 HyFSE 的 Python Driver，專門處理 Go 原生 curl 無法突破的網站（如 Cloudflare Bot Fight Mode、TLS 指紋檢測等）。

HyFSE 將組好的 HTTP 請求透過 API 發送給本服務，本服務使用 `curl_cffi` 進行 TLS 指紋偽造（模擬 Chrome 瀏覽器），再將原始 response 回傳給 HyFSE 做解析。

```
HyFSE (Go)
  ├── driver: curl       → Go 原生 HTTP（現行）
  ├── driver: selenium   → Chrome Driver（現行）
  └── driver: python     → POST http://{host}:{port}/request（新增）
        │
        ▼
  bookstore-scraper (Python/FastAPI)
  └── curl_cffi (TLS 指紋偽造，模擬 Chrome 136)
```

---

## 服務資訊

| 項目 | 說明 |
|---|---|
| 預設 Port | 8101（可在 `configs/settings.yaml` 修改） |
| API 文件 | `http://{host}:{port}/docs`（Swagger UI） |
| 健康檢查 | `GET http://{host}:{port}/` |

---

## API 端點

### 1. `POST /request` — 主要呼叫端點

HyFSE 將完整的 HTTP 請求交給本服務發送。

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

### 2. `GET /sessions` — 查看活躍 Session

```json
{
    "sessions": [
        {"session_id": "33445_shared", "age": 12.5}
    ]
}
```

### 3. `DELETE /session/{session_id}` — 手動關閉 Session

```json
{"closed": true}
```

### 4. `GET /` — 健康檢查

```json
{"service": "bookstore-scraper", "version": "0.3.0"}
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

// GET 請求
resp, err := PythonDriver(cfg, ProxyRequest{
    URL:    "https://search.books.com.tw/search/query/key/python/cat/all",
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
# 一鍵部署
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
  port: 8101            # 修改 port

scraper:
  impersonate: "chrome136"  # TLS 指紋版本
  timeout: 30               # 請求逾時

session:
  ttl: 300                  # Session 過期秒數
```

修改後重啟服務即可生效。

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
    // 被目標網站封鎖（TLS 偽造也沒用）
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
