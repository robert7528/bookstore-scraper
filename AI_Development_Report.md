# AI 輔助開發工作報告

> 報告日期：2026-04-09
> 使用工具：Claude Code (Anthropic Claude Opus 4)
> 報告人：吳榮豐 (Robert Wu)

---

## 目錄

1. [Bookstore Scraper — Anti-Bot Fetch Proxy](#1-bookstore-scraper--anti-bot-fetch-proxy)
2. [FOLIO Migration Web — 圖書館系統遷移平台](#2-folio-migration-web--圖書館系統遷移平台)
3. [HySP — 圖書館服務平台](#3-hysp--圖書館服務平台)

---

## 1. Bookstore Scraper — Anti-Bot Fetch Proxy

### 1.1 專案概述

獨立 microservice，透過 HTTP API 供 HyFSE / HyProxy 呼叫，繞過 Cloudflare / WAF 保護，解決學術資料庫（JCR、WOS）因反爬蟲機制無法正常存取的問題。

**技術棧：** Python 3.11, FastAPI, curl_cffi, undetected-chromedriver, asyncio

### 1.2 系統架構

```
┌─────────────────────────────────────────────────────────┐
│                     使用者瀏覽器                          │
└───────────────┬─────────────────────────────────────────┘
                │
┌───────────────▼─────────────────────────────────────────┐
│              HyProxy (Go, EZProxy-like)                  │
│         URL Rewrite + Session 管理 + Cookie 管理          │
└───────────┬───────────────────────┬─────────────────────┘
            │ use-proxy: antibot    │ 一般網站
            │ (CF 保護網站)          │
┌───────────▼───────────────┐  ┌───▼───────────────────┐
│  Bookstore Scraper Proxy  │  │   Squid (port 7070)   │
│      (port 8102)          │  │                       │
│                           │  └───────────────────────┘
│  ┌─ MitM 模式 ──────────┐│
│  │ curl_cffi TLS 偽裝    ││
│  │ CF cookie 內部管理     ││
│  └───────────────────────┘│
│  ┌─ Transparent 模式 ───┐│
│  │ Auth 域名直接轉發      ││
│  │ 保留原始 TLS/Cookie    ││
│  └───────────────────────┘│
└───────────────────────────┘
```

```
┌─────────────────────────────────────────────────────────┐
│         HyFSE (Go, port 8900, K8s pod)                   │
└───────────────┬─────────────────────────────────────────┘
                │ /fetch/{url}
┌───────────────▼─────────────────────────────────────────┐
│         Bookstore Scraper API (port 8101)                 │
│                                                           │
│  Layer 1: curl_cffi + TLS fingerprint (0.03-0.5s)        │
│      ↓ challenge detected                                 │
│  Layer 2: undetected-chromedriver + Xvfb (3-7s)          │
│           Turnstile bypass                                │
└─────────────────────────────────────────────────────────┘
```

### 1.3 AI 協助開發內容

#### (A) Anti-Bot Fetch Service 開發

| 項目 | 說明 |
|------|------|
| 架構設計 | 設計兩層反爬蟲策略：curl_cffi TLS 偽裝 → undetected-chromedriver fallback |
| Session 管理 | per-domain session 重用（TTL 300s），避免重複建立連線 |
| Challenge 偵測 | 自動偵測 CF challenge 頁面（title + body size），觸發 browser fallback |
| 部署自動化 | systemd + Xvfb（Linux）/ WinSW（Windows）服務化 |

#### (B) Forward Proxy 開發（HyProxy 介接）

| 項目 | 說明 |
|------|------|
| HTTP CONNECT MitM | asyncio TCP server，攔截 TLS 連線，用 curl_cffi 重新發請求 |
| MitM / Transparent 分流 | CF 保護域名走 MitM，Auth 域名走 Transparent |
| 自簽憑證自動產生 | openssl 自動產生 MitM 用的 CA 憑證 |
| Set-Cookie 多值保留 | 使用 multi_items() 保留重複 Set-Cookie header |
| Session closed 自動重試 | 捕捉 TTL 過期 race condition，自動重建 session |

#### (C) HyProxy JCR 無限轉圈問題排查

這是跨多所大學的長期問題（北科、雲科、北醫、彰師、靜宜），原開發者之前表示無法解決。

```
問題流程：

  使用者 ──→ HyProxy ──→ JCR (jcr.clarivate.com)
                              │
                    ┌─────────▼──────────┐
                    │ cookie-domain="1"  │
                    │ 所有 cookie 改寫到  │
                    │ .yuntech.edu.tw    │
                    └─────────┬──────────┘
                              │
        ┌─────────────────────┼─────────────────────┐
        │                     │                     │
   jcr 的 __cf_bm      access 的 __cf_bm    login 的 __cf_bm
   → .yuntech.edu.tw   → .yuntech.edu.tw   → .yuntech.edu.tw
        │                     │                     │
        └─────────────────────┼─────────────────────┘
                              │
                     同名同 domain
                      互相覆蓋！
                              │
                    ┌─────────▼──────────┐
                    │  CF 驗證失敗        │
                    │  → 無限轉圈 loop   │
                    └────────────────────┘
```

| 分析步驟 | 成果 |
|----------|------|
| 問題重現與 log 分析 | 追蹤 auth redirect chain，定位 login loop 模式 |
| HyProxy 原始碼 review | 分析 Go 原始碼（profile.go, auth.go, service.go），找到 cookie-domain rewrite 邏輯 |
| 根因確認 | `cookie-domain: "1"` 把 90+ profile 的 cookie 全部改寫到上層 domain，CF cookie 互相覆蓋 |
| 測試工具開發 | 3 個 Python 測試工具驗證 cookie 衝突 |
| 解法實作 | CF cookie 由 curl_cffi 內部管理，不回傳給 HyProxy |
| 瀏覽器實測 | Chrome 直接走 proxy 訪問 JCR，auth + 搜尋 + API 全部正常 |

#### (D) Bug 修復清單

| Bug | 影響 | 修復方式 |
|-----|------|----------|
| Session closed race condition | 並發請求時連續 502 錯誤 | `_curl_request()` 捕捉 closed error，自動重建 session |
| Set-Cookie header 遺失 | `dict()` 丟失重複 Set-Cookie → auth cookie 遺失 | 改用 `multi_items()` 保留所有 header |
| 3xx redirect 誤判 challenge | auth redirect 觸發 browser fallback → loop | 3xx 跳過 challenge detection |
| CF cookie 覆蓋 | HyProxy cookie-domain 合併不同站的 __cf_bm | 過濾 CF cookie，不回傳給 HyProxy |
| SSL 憑證驗證失敗 | Rocky Linux CA 憑證不完整 | `verify=False` |

---

## 2. FOLIO Migration Web — 圖書館系統遷移平台

### 2.1 專案概述

Web 管理平台，用於 HyLib 圖書館系統遷移到 FOLIO 開源圖書館系統。

**技術棧：** Python 3, FastAPI, SQLAlchemy, SQLite, Jinja2, httpx

### 2.2 系統架構

```
┌──────────────────────────────────────┐
│          Migration Web Portal         │
│           (FastAPI + Jinja2)          │
├──────────────────────────────────────┤
│  客戶管理  │  Mapping 編輯  │  執行追蹤  │
│  憑證管理  │  資料轉換      │  驗證刪除  │
├──────────────────────────────────────┤
│        folio_migration_tools          │
│      (HyLib CSV → FOLIO 格式轉換)     │
├──────────────────────────────────────┤
│     FOLIO Okapi Gateway API           │
│        (Token Auth)                   │
└──────────────────────────────────────┘
```

### 2.3 AI 協助開發內容

| 項目 | 說明 |
|------|------|
| 客戶管理 CRUD | 多客戶管理、憑證加密儲存（Fernet） |
| Mapping 檔編輯器 | per-client 設定版控 |
| 資料轉換流程 | HyLib CSV → FOLIO TSV（feefines, loans, requests） |
| MARC 095 解析 | 從 MARC 記錄擷取 holdings/items |
| 背景任務執行 | FastAPI + threading 背景執行轉換 |
| Okapi API 整合 | 自動 token 認證、資料上傳、驗證 |

---

## 3. HySP — 圖書館服務平台

### 3.1 專案概述

國家級圖書館服務平台，採用微服務架構、多租戶設計、零信任安全模型。

**技術棧：** Go (Gin, GORM, uber-go/fx), React 19, Vite 6, PostgreSQL, Podman/K8S

### 3.2 平台架構

```
┌─────────────────────────────────────────────────────────┐
│                    nginx (反向代理)                       │
│   /hyadmin/ → hyadmin-ui    /hyadmin-api/ → hyadmin-api │
│   /hycert/  → hycert-ui     /hycert-api/  → hycert-api  │
└──────────┬──────────────────────────────────┬────────────┘
           │                                  │
┌──────────▼──────────┐          ┌────────────▼───────────┐
│   Frontend Layer     │          │    Backend Layer        │
│                      │          │                         │
│  hyadmin-ui (Shell)  │          │  hyadmin-api            │
│   └─ wujie 微前端    │          │   ├─ 租戶管理           │
│      ├─ hycert-ui   │          │   ├─ 模組管理           │
│      └─ ...         │          │   └─ 系統管理           │
│                      │          │                         │
│  React 19 + Vite 6  │          │  hycert-api             │
│  Shadcn/ui + TW CSS │          │   ├─ 憑證解析/驗證      │
│                      │          │   ├─ CSR 產生           │
│                      │          │   └─ Agent 管理         │
└──────────────────────┘          │                         │
                                  │  hycore (共用模組)      │
                                  │   ├─ config / logger    │
                                  │   ├─ database (GORM)    │
                                  │   ├─ middleware          │
                                  │   │  (JWT, RBAC, tenant)│
                                  │   └─ auditlog           │
                                  └─────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│                 hycert-agent (Go binary)                  │
│         部署到客戶端，自動更新 SSL 憑證                     │
│   支援：nginx / apache / haproxy / hyproxy                │
│   認證：Agent Token (SHA-256)                             │
│   平台：Linux amd64 / Windows amd64                       │
└─────────────────────────────────────────────────────────┘
```

### 3.3 AI 協助開發內容

#### 3.3.1 hysp — 平台規格書

| 項目 | 說明 |
|------|------|
| 架構設計 | 多租戶、零信任、微服務架構規格書撰寫 |
| 模組規劃 | Platform / Business / Shared 三層模組設計 |
| 演進路線 | Phase 1-4 開發路線圖 |

#### 3.3.2 hycore — 共用 Go 模組

| 項目 | 說明 |
|------|------|
| Config 管理 | Viper 設定載入、環境變數覆蓋 |
| Logger | zap + lumberjack 日誌輪替 |
| Database | GORM + DBManager 多租戶 DB 管理 |
| Middleware | JWT 認證、Casbin RBAC、租戶切換、Recovery |
| Audit Log | 操作紀錄 |

#### 3.3.3 hyadmin-api — 管理控制台 API

| 項目 | 說明 |
|------|------|
| 租戶管理 | 多租戶 DB（per-tenant DSN / schema-based） |
| 模組管理 | 動態模組註冊與設定 |
| DB 版本控制 | Atlas + GORM 自動 migration |
| 部署 | Podman Quadlet + systemd |

#### 3.3.4 hyadmin-ui — 管理控制台前端

| 項目 | 說明 |
|------|------|
| SPA Shell | React 19 + React Router v7 |
| 微前端架構 | wujie-react iframe-based 子應用載入 |
| 動態模組 | 從 API 載入模組列表，動態生成導航 |
| UX 功能 | 角色導航、閒置自動登出 |

#### 3.3.5 hycert-api — 憑證管理 API

| 項目 | 說明 |
|------|------|
| 憑證解析 | PEM / DER 格式解析、驗證 |
| CSR 產生 | 自動產生 Certificate Signing Request |
| Chain 建構 | AIA chaining + AKID/SKID 匹配 |
| 安全設計 | 不儲存/不記錄私鑰 |

#### 3.3.6 hycert-agent — 憑證部署 Agent

| 項目 | 說明 |
|------|------|
| 跨平台 | Go 交叉編譯 Linux/Windows |
| 自動部署 | 下載憑證 → 備份 → 寫檔 → reload 服務 |
| 多服務支援 | nginx, apache, haproxy, hyproxy |
| 安全 | Agent Token 認證、私鑰 0600 權限 |

---

## 4. 資安事件應對

### 4.1 Trivy 供應鏈攻擊（2026-03-19~22）

2026 年 3 月 19-22 日期間，Aqua Security 旗下的 Trivy 遭到供應鏈攻擊（TeamPCP），`trivy-action` 的 76 個版本標籤被植入惡意程式碼。

**AI 協助檢查內容：**

| 檢查項目 | 結果 |
|----------|------|
| 受影響 repo | hyadmin-api、hycert-api（使用 `@master`） |
| 攻擊期間 CI 執行次數 | hyadmin-api: 9 次、hycert-api: 10 次 |
| 實際下載的 SHA | `57a97c7e`（v0.35.0，安全版本） |
| tpcp-docs 倉庫 | 未發現 |
| 結論 | **未被入侵** — SHA 在攻擊前已鎖定 |

**修復措施：**
- `@master` → 固定 SHA `57a97c7e7821a5776cebc9bb87c984fa69cba8f1`（v0.35.0）
- 已 push 到 hyadmin-api 和 hycert-api

---

## 5. AI 使用效益總結

| 面向 | 效益 |
|------|------|
| **開發速度** | 從架構設計到實作部署，大幅縮短開發週期 |
| **問題排查** | 跨系統（Go + Python）原始碼分析，找到多校長期無法解決的 JCR 問題根因 |
| **程式碼品質** | 自動處理 edge case（race condition, header 遺失, cookie 衝突） |
| **測試工具** | 快速開發診斷工具，用數據驗證問題假設 |
| **文件化** | 架構規格書、CLAUDE.md、memory 系統，確保知識傳承 |
| **跨語言能力** | 同時處理 Go（HyProxy）和 Python（bookstore-scraper）的整合問題 |
