# JCR Login Loop 問題分析與解決方案

## 問題描述

多所大學反映透過 HyProxy 存取 JCR (Journal Citation Reports) 時，會卡在登入頁面無限轉圈圈（login loop）。症狀包括：

- 瀏覽器在 `login.incites.clarivate.com` 和 `access.clarivate.com` 之間不斷跳轉
- 清除 Redis 並重啟 HyProxy 後暫時恢復，但一段時間後又壞掉
- 不經 HyProxy 直連 JCR 則完全正常

受影響的學校包括：雲科大、北科大、北醫、彰師、靜宜等。

---

## 技術背景

### HyProxy 架構

HyProxy 是 Go 語言開發的反向代理伺服器，為圖書館提供校外連線存取電子資源的功能。

兩種運作模式：
- **by Port（`proxy-by: 0`）**：每個目標站台分配不同的 port，所有站台共用同一個 hostname
  ```
  https://erm.must.edu.tw:3179/jcr/home      ← JCR
  https://erm.must.edu.tw:3861/login?app=jcr  ← access.clarivate.com
  ```
- **by Domain（`proxy-by: 1`）**：每個目標站台使用不同的 subdomain
  ```
  https://jcr-clarivate-com.libdb.yuntech.edu.tw:3001/jcr/home      ← JCR
  https://access-clarivate-com.libdb.yuntech.edu.tw:3001/login?app=jcr  ← access.clarivate.com
  ```

### Clarivate 認證流程

JCR 的認證 100% 在瀏覽器端由 Angular SPA 完成：

1. 瀏覽器載入 JCR 首頁 → JCR API 回 500（未認證）
2. JCR SPA 重導向到 `login.incites.clarivate.com`
3. `login.incites` 302 重導向到 `access.clarivate.com/login?app=jcr&detectSession=true`
4. `access.clarivate.com` 載入 Angular SPA
5. Angular SPA 進行 IP-based 認證（TrustedIPAuth）
6. 認證成功 → 設定 auth cookies（IC2_SID、PSSID 等）
7. 重導向回 JCR → API 回 200

`login.incites.clarivate.com` **不做 server-side IP 認證**，只是 302 重導向到 `access.clarivate.com`。

### HyProxy 的 Cookie 處理

HyProxy 設定 `cookie-domain: "1"` 時，會把所有 response 的 `Set-Cookie` domain 改寫到 HyProxy 的根域名（如 `.yuntech.edu.tw`）。這讓不同目標站台的 cookie 可以在 HyProxy 環境中跨域共用。

---

## 根因分析

### 根因一：Clarivate Angular App 的 Domain 檢測（by Domain 模式）

access.clarivate.com 的 Angular app（`main.js`）使用以下邏輯判斷是否在 access subdomain 上：

```javascript
l_access = ["access.", "access-"]

isAccessSubdomain(url) {
    // 檢查 URL 是否包含 "access." 或 "access-"
    return url.indexOf("access.") >= 0 || url.indexOf("access-") >= 0;
}
```

**by Port 模式**：hostname 是 `erm.must.edu.tw:3861`
- 不包含 `"access."` 或 `"access-"` → `isAccessSubdomain` = false
- Angular app 走 **default code path** → IP 認證正常完成 ✅

**by Domain 模式**：hostname 是 `access-clarivate-com.libdb.yuntech.edu.tw`
- 包含 `"access-"` → `isAccessSubdomain` = true
- Angular app 走 **access subdomain code path** → 觸發 `detectSession` 邏輯
- 但 hostname 不在 `APPS_SUB_DOMAINS` 的已知清單中
- → `detectSession` 失敗 → 重導向回 `login.incites` → **無限循環** ❌

Angular app 的環境配置只包含 Clarivate 自己的 domain：
```javascript
APPS_SUB_DOMAINS: {
    "access.clarivate.com": "apps.clarivate.com",
    "access.dev-stable.clarivate.com": "apps.dev-stable.clarivate.com",
    // ... 沒有任何 HyProxy 的 domain
}
```

### 根因二：CF Cookie 累積覆蓋

HyProxy 的 `cookie-domain: "1"` 把所有 cookie domain 改寫到根域名（如 `.yuntech.edu.tw`），包括 Cloudflare 的 `__cf_bm` cookie。

不同 Clarivate 子站台（jcr.clarivate.com、access.clarivate.com、login.incites.clarivate.com）各自有獨立的 `__cf_bm`，被改寫到同一個 domain 後互相覆蓋：

```
jcr.clarivate.com      的 __cf_bm → 改寫到 .yuntech.edu.tw
access.clarivate.com   的 __cf_bm → 改寫到 .yuntech.edu.tw → 覆蓋！
login.incites          的 __cf_bm → 改寫到 .yuntech.edu.tw → 再覆蓋！
```

Cloudflare 驗證時發現 `__cf_bm` token 不匹配 → 擋住請求 → 加劇 login loop。

這也是為什麼「清 Redis 重啟後暫時能用、過一段時間又壞」—— 清 Redis 後 `__cf_bm` 從零開始，短時間不衝突；隨著使用時間增長，不同站台的 `__cf_bm` 在 ShareCookie 中累積覆蓋，最終觸發 Cloudflare 封鎖。

### 根因三：HyProxy Profile 先到先贏

HyProxy 用 `host + scheme` 為 key 註冊 profile。當多個 Database（如 WoS 和 JCR）共用相同的 host profile（如 `jcr.clarivate.com`）時，**先載入的 Database 先註冊，後載入的被跳過**。

WoS 通常比 JCR 先載入。如果 WoS 沒有設定 `use-proxy: antibot`，即使 JCR 有設定，`jcr.clarivate.com` 的 profile 也不會走 proxy。

```
WoS (10F203100) 先載入 → 註冊 jcr.clarivate.com（沒有 proxy）
JCR (10F204L1Q) 後載入 → "Already in Register!" → use-proxy 被跳過
```

### 附加因素：NAT Pool（部分環境）

部分客戶主機（如雲科大 jumper）的出口 IP 不固定（NAT pool），不同 TCP 連線可能分到不同的 IP。在認證 redirect chain 中，不同步驟的 IP 不一致，導致 Clarivate 的 IP-bound session 失效。

---

## 解決方案

### 方案架構

在 HyProxy 和目標網站之間加入 bookstore-scraper 作為 forward proxy：

```
瀏覽器 → HyProxy → bookstore-scraper proxy (port 8102)
                     ├── curl_cffi MitM（TLS fingerprint impersonation）
                     ├── CF cookie (__cf_bm) 過濾
                     └── Angular app domain 檢測修正
                   → Clarivate (jcr.clarivate.com, access.clarivate.com, ...)
```

### 解法一：CF Cookie 過濾

Proxy 攔截 response 中的 `Set-Cookie` header，過濾掉所有 Cloudflare cookie：

```python
def _is_cf_cookie(set_cookie_value: str) -> bool:
    name = set_cookie_value.split("=", 1)[0].strip().lower()
    return name.startswith("__cf") or name == "cf_clearance"

# Response 處理時過濾
if k.lower() == "set-cookie" and _is_cf_cookie(v):
    logger.info("CF cookie filtered: %s from %s", cookie_name, url)
    continue  # 不回傳給 HyProxy
```

curl_cffi 的 session 內部仍保留各 domain 的 `__cf_bm`（正確處理 CF 驗證），但不讓 HyProxy 看到 → 不會被 `cookie-domain: "1"` 改寫 → 不會累積覆蓋。

### 解法二：Angular App Domain 檢測修正

Proxy 攔截 `access.clarivate.com` 回傳的 Angular main.js，修改 domain 檢測邏輯：

```python
# 原始
l_access=["access.","access-"]

# 修改後
l_access=["access.clarivate.com","__noop__"]
```

修改前後的效果：

| hostname | 原始判斷 | 修改後判斷 | 結果 |
|----------|---------|-----------|------|
| `access.clarivate.com`（直連）| true | true | 正常（真的在 access 上）|
| `access-clarivate-com.xxx.edu.tw`（by Domain）| true ❌ | false ✅ | 不再誤判 |
| `erm.must.edu.tw:3861`（by Port）| false | false | 正常（不受影響）|

修改後 by Domain 的行為等同 by Port — Angular app 走 default code path，IP 認證正常完成。

實作方式：

```python
if (domain == "access.clarivate.com"
        and "javascript" in content_type
        and b'l_access=["access.","access-"]' in resp_body):
    resp_body = resp_body.replace(
        b'l_access=["access.","access-"]',
        b'l_access=["access.clarivate.com","__noop__"]'
    )
    logger.info("Patched Angular domain detection for %s", url)
```

### 解法三：HyProxy Profile 先到先贏的處理

所有共用 Clarivate host profiles 的 Database 都必須設定 `use-proxy: antibot`，不只 JCR：

- WoS (10F203100)：`"use-proxy": "antibot"` ← 必須
- JCR (10F204L1Q)：`"use-proxy": "antibot"` ← 必須

**必須從 HyProxy 管理後台設定**，直接修改 JSON 檔案不會存入 BoltDB。

---

## 部署步驟

### 1. 安裝 bookstore-scraper

參考 `docs/install-guide.md`。

### 2. HyProxy 主設定（config.yml）

```yaml
proxys:
  - id: antibot
    address: 127.0.0.1:8102
```

### 3. HyProxy 站台設定（從管理後台）

WoS 和 JCR 都加：
```json
{
  "use-proxy": "antibot",
  "cookie-domain": "1"
}
```

### 4. 清除 Redis 並重啟

```bash
redis-cli -n <select> FLUSHDB
systemctl restart hyproxy
```

### 5. 驗證

```bash
# 看 proxy log
journalctl -u bookstore-scraper -f | grep -E "jcr.clarivate|Patched|CF cookie|session-details"

# 應該看到：
# CF cookie filtered: __cf_bm from ...        ← CF cookie 過濾
# Patched access.clarivate.com Angular...      ← JS 修正
# session-details → 200                        ← auth 成功
```

---

## 驗證結果

### 雲科大（by Domain + NAT pool）

| 狀態 | session-details | login loop | 備註 |
|------|----------------|------------|------|
| 純 HyProxy | 500 | ✅ loop | Angular domain 誤判 + CF cookie 累積 |
| HyProxy + proxy（修正前）| 500 | ✅ loop | Angular domain 誤判（NAT pool 加劇）|
| HyProxy + proxy（修正後）| **200** | **無 loop** ✅ | Angular 修正 + CF 過濾 |

### 明新科大（by Port）

| 狀態 | session-details | login loop | 備註 |
|------|----------------|------------|------|
| 純 HyProxy | 200 | 無 loop | by Port 不觸發 domain 檢測 |
| HyProxy + proxy | **200** | **無 loop** ✅ | CF cookie 過濾防止長期累積 |

---

## 注意事項

### Clarivate JS 更新

如果 Clarivate 更新 `access.clarivate.com` 的 Angular app（`main.js` hash 變化），需要確認：
1. `l_access` 變數是否仍存在
2. 字串格式是否改變
3. 如有變化需更新 proxy 的 replace 邏輯

### HyProxy by Port vs by Domain

| | by Port | by Domain |
|---|---------|-----------|
| 需要 proxy | 建議（防 CF 累積）| **必須**（解決 Angular domain 問題）|
| 防火牆需求 | 需開多 port | 不需要額外 port |
| Angular 問題 | 不受影響 | 需要 proxy 修正 |

### EZProxy 對照

EZProxy 的 Clarivate stanza 使用 `Option CookiePassThrough`，不改寫 cookie domain。HyProxy 沒有等效功能，需要透過 proxy 過濾來達到類似效果。

---

## 相關檔案

- `src/proxy/handler.py` — CF cookie 過濾 + Angular JS 修正
- `src/proxy/jcr_browser.py` — Browser fetch 模式（NAT pool 環境備用）
- `src/proxy/auth_cache.py` — Auth cookie 快取（備用）
- `tools/test_jcr_simple.sh` — JCR 快速診斷腳本
- `tools/test_nat_session.py` — NAT pool + session 測試
- `tools/test_jcr_auth.py` — JCR auth flow 測試
- `tools/analyze_access_js.sh` — Angular app API 分析
- `tools/monitor_cookies.sh` — Redis cookie 監控
- `docs/install-guide.md` — 安裝指南
