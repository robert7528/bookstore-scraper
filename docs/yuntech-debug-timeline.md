# 雲科大 JCR Login Loop 除錯時間線

## 環境資訊
- 主機：jumper (140.125.246.53)
- HyProxy 模式：by Domain
- HyProxy domain：libdb.yuntech.edu.tw:3001
- 出口 IP：NAT pool（59.125.78.141 / 78.142 / 76.248 / 76.249 不固定）

---

## 除錯歷程

| # | 日期 | 懷疑的問題 | 測試方式 | 結果 | 結論 |
|---|------|-----------|---------|------|------|
| 1 | 04/10 | CF cookie (`__cf_bm`) 累積覆蓋 — `cookie-domain: "1"` 把不同域名的 CF cookie 合併到同一 domain | 檢查瀏覽器 cookies，比對 `__cf_bm` 數量和 domain | 發現多個 `__cf_bm` 在 `.yuntech.edu.tw` | ✅ 確認是問題之一，但不是唯一原因 |
| 2 | 04/10 | `cookie-domain: "1"` 拿掉後能解決 | 拿掉 `cookie-domain`，測試 JCR | ❌ 拿掉後 auth cookie 無法跨域共用，也 loop | ❌ 不能拿掉，auth 需要它 |
| 3 | 04/10 | Proxy rate limit 干擾 | 檢查 log，發現 0.5s rate limit 序列化 API 請求 | API 被延遲 ~2 秒 | ✅ 已修正：proxy rate limit 獨立設定，預設 0 |
| 4 | 04/10 | HyProxy 的 login flow 沒帶 auth cookie | 加 debug log 看 request Cookie header | Cookie 只有 HYSID + tracking，沒有 Clarivate auth cookie | ✅ 確認 HyProxy 沒有 Clarivate auth |
| 5 | 04/10 | Transparent domain 設定問題 | 測試 access/login 走 transparent vs MitM | MitM 和 transparent 都無法完成 auth | ❌ 不是 transparent 設定問題 |
| 6 | 04/10 | Login.incites 做 server-side IP auth | curl 直打 `login.incites.clarivate.com` | 只回 302 → access.clarivate.com，沒有 authCode | ✅ 確認 login.incites 不做 server-side auth |
| 7 | 04/10 | Clarivate auth 是 server-side | 分析 Angular JS 原始碼 | 100% client-side Angular SPA 處理 | ✅ 確認是純 client-side auth |
| 8 | 04/10 | Jumper 的 IP 被 Clarivate 封鎖 | Windows (59.125.76.249) vs jumper (59.125.78.141) 比較 | 兩台 login.incites 都回 302 without authCode，行為一樣 | ❌ 沒有被封鎖 |
| 9 | 04/10 | Jumper 的 IP 不在 Clarivate 白名單 | `curl -s access.clarivate.com` 看 `globalIpAddress` | 偵測到 59.125.76.249，在白名單內 | ❌ IP 有被認可 |
| 10 | 04/10 | NAT pool 導致 auth session 失效 | `for i in 1..5; curl ifconfig.me` + browser auth test | 系統 curl 每次不同 IP，browser auth 的 IP 跟 API 請求 IP 不一致 | ✅ NAT pool 確認存在 |
| 11 | 04/10 | 用 browser fetch 繞過 NAT pool | 在 proxy 實作 browser fetch（Chrome 發 API 請求） | 瀏覽器內 IP 穩定 (78.141)，但 auth IP 是 76.248 → session-details 401 | ❌ 同一個 Chrome 裡不同 domain 連線也會走不同 NAT IP |
| 12 | 04/10 | 用 auth cookie 轉移繞過 NAT pool | 瀏覽器拿 IC2_SID/PSSID → 注入 curl_cffi | curl_cffi 帶 cookies 但 IP 不同 → 401 | ❌ Cookie 綁定 auth 時的 IP |
| 13 | 04/10 | HyProxy code 的 LoginURL 機制能解決 | 分析 HyProxy auth.go 原始碼 | LoginURL 只登入 HyProxy 自己（HYSID），不登入 Clarivate | ✅ 確認 LoginURL 不處理 Clarivate auth |
| 14 | 04/10 | HyProxy ShareCookie 有殘留有效 cookie | 檢查 Redis session_* keys | Session 裡沒有 CF cookie，也沒有 Clarivate auth cookie | ✅ ShareCookie 是空的（已被清除） |
| 15 | 04/10 | WoS 先註冊 jcr.clarivate.com profile（先到先贏） | HyProxy debug mode：`grep "Already in Register"` | `jcr-clarivate-com Already in Register!` — JCR 的 use-proxy 被跳過 | ✅ 確認 WoS 先註冊，JCR 的 proxy 設定無效 |
| 16 | 04/10 | WoS 加 use-proxy 後能解決先到先贏 | 從 HyProxy 管理後台加 WoS use-proxy: antibot | JCR 流量開始走 proxy（`CONNECT jcr.clarivate.com:443`）| ✅ 修復先到先贏問題 |
| 17 | 04/10 | Redis 快取阻止 JCR 走 proxy | `redis-cli keys "*jcr*"` 發現快取的 response | 清 Redis 後第一次 JCR 有走 proxy，之後從快取回應 | ⚠️ Redis 快取的是 URL 改寫後的 body，不含 Set-Cookie |
| 18 | 04/10 | by Port vs by Domain 是關鍵差異 | 比對明新 (by Port) 和雲科 (by Domain) 的 HAR | 明新 auth 正常，雲科 loop；兩者 JCR config 完全相同 | ✅ by Domain 是造成 loop 的根因 |
| 19 | 04/12 | Angular app 的 `isAccessSubdomain` 檢查觸發誤判 | 分析 main.js：`l_access=["access.","access-"]` | `access-clarivate-com.xxx.edu.tw` 包含 `"access-"` → true → 走錯 code path | ✅ 確認這是 by Domain loop 的根本原因 |
| 20 | 04/12 | 修改 `l_access` 解決 domain 誤判 | Proxy MitM 攔截 main.js，改 `l_access=["access.clarivate.com","__noop__"]` | session-details 500 → 200，搜尋正常，WoS 也正常 | ✅ **問題解決！** |

---

## 最終結果

```
修改前：login.incites ↔ access.clarivate.com 無限 loop
修改後：session-details → 200 ✅ | product-details → 200 ✅ | JCR 搜尋正常 ✅ | WoS 正常 ✅
```

## 根因總結

JCR login loop 由三個問題疊加造成：

1. **Angular domain 誤判**（主因）— access.clarivate.com 的 Angular app 用 `l_access=["access.","access-"]` 判斷 subdomain，HyProxy by Domain 的 hostname `access-clarivate-com.xxx` 被誤判為 access subdomain → detectSession 失敗 → loop
2. **CF cookie 累積覆蓋**（加劇因素）— `cookie-domain: "1"` 把不同域名的 `__cf_bm` 合併 → Cloudflare 驗證失敗
3. **NAT pool**（雲科特有）— 出口 IP 不固定 → redirect chain 中 IP 不一致 → session 失效

## 解決方案

1. Proxy MitM 修改 Angular JS 的 domain 檢測邏輯 → 解決 by Domain 的 loop
2. Proxy 過濾 CF cookie → 防止 `__cf_bm` 累積覆蓋
3. WoS 和 JCR 都設 `use-proxy: antibot` → 解決 profile 先到先贏問題
