# Bookstore Scraper 安裝指南

## 系統需求

- **OS**: Rocky Linux 8/9、CentOS 8/9、Ubuntu 20.04+
- **Python**: 3.11+（Rocky/CentOS 用 Conda 提供）
- **Google Chrome**: 最新穩定版（browser fallback 用）
- **Xvfb**: Linux headless 環境下 Chrome 必須
- **RAM**: 最低 512MB，建議 1GB+
- **Port**: 8101 (fetch API)、8102 (forward proxy)

## SSL Inspection 環境（學校/企業常見）

很多學校、企業的網路會做 HTTPS inspection（中間人 SSL），導致 pip、conda、curl 全部 SSL 驗證失敗。
錯誤訊息：`SSL: CERTIFICATE_VERIFY_FAILED - self signed certificate in certificate chain`

**解法（部署腳本已自動處理）：**

```bash
# Conda：關閉 SSL 驗證
conda config --set ssl_verify false

# pip：加 --trusted-host
pip install --trusted-host pypi.org --trusted-host pypi.python.org --trusted-host files.pythonhosted.org <package>

# curl：加 -k
curl -k <url>
```

> 自動部署腳本 `deploy-linux.sh` 已內建以上處理，直接跑即可。

## 安裝步驟

### 1. 環境檢查

```bash
# 切 root
sudo -i

# 確認 OS
cat /etc/os-release | head -3

# 確認 Python 版本（需要 3.11+）
python3 --version

# 確認對外 IP 穩定性（JCR proxy 必須固定 IP）
for i in 1 2 3 4 5; do curl -s https://api.ipify.org; echo; done
```

> ⚠️ **如果 IP 不穩定（NAT pool），JCR proxy 功能無法使用。** Fetch API 不受影響。

### 2. 安裝 Python 3.11（Rocky/CentOS）

Ubuntu 20.04+ 如已有 Python 3.11 可跳過此步驟。

```bash
# 安裝 Miniconda（-k 跳過 SSL inspection）
curl -fsSLk https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh -o /tmp/miniconda.sh
bash /tmp/miniconda.sh -b -p /opt/miniconda3

# 關閉 SSL 驗證（校園 SSL inspection 環境必須）
/opt/miniconda3/bin/conda config --set ssl_verify false

# 接受 TOS（2025+ 新版 Conda 必須）
/opt/miniconda3/bin/conda tos accept --override-channels --channel https://repo.anaconda.com/pkgs/main
/opt/miniconda3/bin/conda tos accept --override-channels --channel https://repo.anaconda.com/pkgs/r

# 建立 Python 3.11 環境
/opt/miniconda3/bin/conda create -p /opt/miniconda3/envs/scraper python=3.11 -y

# 確認
/opt/miniconda3/envs/scraper/bin/python --version
```

### 3. 安裝 Google Chrome

```bash
# Rocky/CentOS
cat > /etc/yum.repos.d/google-chrome.repo << 'EOF'
[google-chrome]
name=Google Chrome
baseurl=https://dl.google.com/linux/chrome/rpm/stable/x86_64
enabled=1
gpgcheck=1
gpgkey=https://dl.google.com/linux/linux_signing_key.pub
EOF
dnf install -y google-chrome-stable

# 如果有 kubernetes repo 壞掉，加 --disablerepo=kubernetes
# dnf install -y google-chrome-stable --disablerepo=kubernetes

# Ubuntu
# wget -q -O - https://dl.google.com/linux/linux_signing_key.pub | apt-key add -
# echo "deb [arch=amd64] http://dl.google.com/linux/chrome/deb/ stable main" > /etc/apt/sources.list.d/google-chrome.list
# apt update && apt install -y google-chrome-stable
```

### 4. 安裝 Xvfb 及 Chrome 依賴

```bash
# Rocky/CentOS
dnf install -y xorg-x11-server-Xvfb dbus-x11 dbus-libs \
    mesa-libGL mesa-libEGL libXcomposite libXdamage libXrandr \
    libXi libXtst alsa-lib atk at-spi2-atk cups-libs libdrm \
    libxkbcommon pango nss nspr gtk3 xdg-utils

# Ubuntu
# apt install -y xvfb
```

### 5. 部署專案

```bash
cd /opt
git clone https://github.com/robert7528/bookstore-scraper.git
cd bookstore-scraper

# 建立 venv（用 Conda 的 Python 3.11）
/opt/miniconda3/envs/scraper/bin/python -m venv .venv

# 先升級 pip（舊版不支援 pyproject.toml editable mode）
.venv/bin/pip install --upgrade pip --trusted-host pypi.org --trusted-host pypi.python.org --trusted-host files.pythonhosted.org

# 安裝（含 undetected-chromedriver）
.venv/bin/pip install -e ".[undetected]" --trusted-host pypi.org --trusted-host pypi.python.org --trusted-host files.pythonhosted.org
```

### 6. 設定

```bash
vi configs/settings.yaml
```

**基本設定（fetch API only）：**
```yaml
server:
  host: "0.0.0.0"
  port: 8101

proxy:
  enabled: false
```

**含 HyProxy proxy 模式：**
```yaml
proxy:
  enabled: true
  host: "0.0.0.0"
  port: 8102
  rate_limit_interval: 0
  browser_fetch: false        # NAT pool 環境改 true
  transparent_domains: []     # 全走 MitM
```

### 7. 安裝 systemd 服務

```bash
cat > /etc/systemd/system/bookstore-scraper.service << 'SVCEOF'
[Unit]
Description=Bookstore Scraper API
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/bookstore-scraper
ExecStart=/usr/bin/xvfb-run --auto-servernum --server-args="-screen 0 1280x720x24" /opt/bookstore-scraper/.venv/bin/python -m uvicorn src.main:app --host 0.0.0.0 --port 8101
Restart=always
RestartSec=5
Environment=PYTHONPATH=/opt/bookstore-scraper

[Install]
WantedBy=multi-user.target
SVCEOF

systemctl daemon-reload
systemctl enable bookstore-scraper
systemctl start bookstore-scraper
```

### 8. 驗證

```bash
# 確認服務啟動
systemctl status bookstore-scraper
journalctl -u bookstore-scraper -n 15 --no-pager

# 測試 fetch API
curl -s http://127.0.0.1:8101/fetch/https://www.books.com.tw/ | head -5

# 如果有開 proxy，確認 proxy 啟動
journalctl -u bookstore-scraper | grep "Forward proxy listening"
```

## 防火牆設定

### 本機 iptables

```bash
# 開放 8101（fetch API，如需對外）
iptables -I INPUT -p tcp --dport 8101 -m state --state NEW -j ACCEPT

# 開放 8102（proxy，如需對外，通常不需要）
iptables -I INPUT -p tcp --dport 8102 -m state --state NEW -j ACCEPT
```

### 透過 nginx 反代（學校防火牆只開 443 時）

在 nginx 的 443 server block 加：
```nginx
location /fetch/ {
    proxy_pass http://127.0.0.1:8101;
    proxy_set_header Host $host;
}
```

## HyProxy 整合設定

### HyProxy 站台設定

```json
{
  "use-proxy": "antibot",
  "cookie-domain": "1"
}
```

### HyProxy antibot proxy 指向

- 同一台主機：`127.0.0.1:8102`
- 不同主機：`<bookstore-scraper-IP>:8102`

### 運作原理

```
瀏覽器 → HyProxy → bookstore-scraper proxy (8102)
                     ├── CF 保護的網站 → curl_cffi MitM（繞過 TLS 偵測）
                     ├── CF cookies (__cf_bm) → 過濾不回傳 HyProxy
                     └── 非 CF 請求 → 直接轉發
```

**CF cookie 過濾**防止 HyProxy 的 cookie-domain 改寫導致不同站台的 CF token 互相覆蓋。

## 更新部署（SOP）

> ⚠️ `git checkout configs/settings.yaml` 是**丟棄**本機改動讓 pull 能過，不是保留。必須先看差異、pull 後再 sed 改回。

### 當天第一次更新任一台客戶機 — 先看差異

```bash
cd /opt/bookstore-scraper
git diff configs/settings.yaml     # 看本機改了哪些（enabled、TTL、cookies 等）
```

把 diff 結果貼給維運/工程（或自己記下），**確認所有本地改動的 sed 寫法**後，才進行下面的完整更新。

### 完整更新流程（六步）

```bash
cd /opt/bookstore-scraper
git checkout configs/settings.yaml                                        # 1. 清本機改動，讓 pull 能過
git pull                                                                  # 2. 拿新版
sed -i 's/^  enabled: false/  enabled: true/' configs/settings.yaml       # 3. 改回 proxy enabled（依 diff 結果加其他 sed）
grep -A1 '^proxy:' configs/settings.yaml | head -5                       # 4. 驗證設定改回來了
ls -l tools/monitor_*.sh                                                  # 5. 驗證腳本 +x（commit a4daecd 後已入 repo）
systemctl restart bookstore-scraper                                       # 6. 重啟服務
```

**第 3 步依 diff 結果可能還要加：**
- `sed -i 's/ttl: 300/ttl: 1800/' configs/settings.yaml`（session.ttl）
- 其他 transparent_domains、managed_cookies、impersonate 等 customization

### 同一台當天第二次以後更新

直接跑完整六步即可，不用再 diff（假設本機沒有再被手動改）。

### 各站常見本地改動速查

| 站 | 改動 |
|----|----|
| 雲科大 jumper、北科大、北醫大、isearch、hyint | `proxy.enabled: false → true` |
| 所有有 proxy 的老部署 | `session.ttl: 300 → 1800`（04-16 後源頭已改） |
| 依站客製 | `managed_cookies`、`transparent_domains`、`impersonate` |

## 監控設定

### 安裝監控腳本

```bash
# 建立目錄
mkdir -p /opt/bookstore-scraper/logs

# Cookie 監控腳本
cat > /opt/bookstore-scraper/tools/monitor_cookies.sh << 'EOF'
#!/bin/bash
DATE=$(date '+%Y-%m-%d %H:%M')
LOGDIR=/opt/bookstore-scraper/logs
LOGFILE=$LOGDIR/cookie_monitor.$(date '+%Y%m').log
mkdir -p $LOGDIR

DB_SIZE=$(redis-cli -n 9 DBSIZE | awk '{print $2}')
JCR_KEYS=$(redis-cli -n 9 keys "*jcr*" 2>/dev/null | wc -l)

CF_COOKIE=0
SESSIONS=$(redis-cli -n 9 keys "session_*" 2>/dev/null)
if [ -n "$SESSIONS" ]; then
    for k in $SESSIONS; do
        if redis-cli -n 9 get "$k" 2>/dev/null | grep -q "__cf_bm"; then
            CF_COOKIE=$((CF_COOKIE+1))
        fi
    done
fi

echo "$DATE | Redis keys: $DB_SIZE | JCR cache: $JCR_KEYS | CF in session: $CF_COOKIE" >> $LOGFILE
[ "$CF_COOKIE" -gt 0 ] && echo "$DATE | WARNING: CF cookie in session!" >> $LOGFILE
find $LOGDIR -name "cookie_monitor.*.log" -mtime +90 -delete 2>/dev/null
EOF

# Proxy 監控腳本
cat > /opt/bookstore-scraper/tools/monitor_proxy.sh << 'EOF'
#!/bin/bash
DATE=$(date '+%Y-%m-%d %H:%M')
LOGDIR=/opt/bookstore-scraper/logs
LOGFILE=$LOGDIR/proxy_monitor.$(date '+%Y%m').log
mkdir -p $LOGDIR

if systemctl is-active bookstore-scraper &>/dev/null; then
    STATUS="running"
    MEM=$(systemctl status bookstore-scraper 2>/dev/null | grep Memory | awk '{print $2}')
else
    STATUS="stopped"
    MEM="0"
fi

LOGS=$(journalctl -u bookstore-scraper --since "1 hour ago" --no-pager 2>/dev/null)
CF_FILTERED=$(echo "$LOGS" | grep -c "CF cookie filtered")
JS_PATCHED=$(echo "$LOGS" | grep -c "Patched")
JCR_200=$(echo "$LOGS" | grep "session-details" | grep -c "200")
JCR_500=$(echo "$LOGS" | grep "session-details" | grep -c "500")
ERRORS=$(echo "$LOGS" | grep -c "ERROR")

echo "$DATE | status=$STATUS mem=$MEM | CF_filtered=$CF_FILTERED JS_patched=$JS_PATCHED JCR_200=$JCR_200 JCR_500=$JCR_500 errors=$ERRORS" >> $LOGFILE
[ "$STATUS" != "running" ] && echo "$DATE | ALERT: service not running!" >> $LOGFILE
find $LOGDIR -name "proxy_monitor.*.log" -mtime +90 -delete 2>/dev/null
EOF

chmod +x /opt/bookstore-scraper/tools/monitor_cookies.sh
chmod +x /opt/bookstore-scraper/tools/monitor_proxy.sh
```

### 設定排程

```bash
# 每小時執行一次
(crontab -l 2>/dev/null | grep -v "monitor_cookies" | grep -v "monitor_proxy"; \
 echo "0 * * * * /opt/bookstore-scraper/tools/monitor_cookies.sh"; \
 echo "0 * * * * /opt/bookstore-scraper/tools/monitor_proxy.sh") | crontab -

# 確認排程
crontab -l | grep monitor
```

### 驗證監控

```bash
# 手動執行一次
/opt/bookstore-scraper/tools/monitor_cookies.sh && /opt/bookstore-scraper/tools/monitor_proxy.sh

# 查看結果
tail -1 /opt/bookstore-scraper/logs/cookie_monitor.$(date '+%Y%m').log
tail -1 /opt/bookstore-scraper/logs/proxy_monitor.$(date '+%Y%m').log
```

Log 按月分檔，保留 3 個月：
```
logs/
├── cookie_monitor.202604.log    ← Redis 狀態 + CF cookie 檢查
├── proxy_monitor.202604.log     ← 服務狀態 + CF 過濾 + JCR auth 統計
└── ...（超過 90 天自動刪除）
```

## 驗證工具

```bash
# NAT + JCR session 測試（確認 IP 穩定性 + auth）
xvfb-run --auto-servernum .venv/bin/python3 tools/test_nat_session.py

# JCR auth flow 測試（curl_cffi + browser）
xvfb-run --auto-servernum .venv/bin/python3 tools/test_jcr_auth.py

# access.clarivate.com Angular app 分析
bash tools/analyze_access_js.sh
```

## 已知問題

| 問題 | 原因 | 解法 |
|------|------|------|
| JCR login loop（by Domain）| Angular app domain 誤判 + CF cookie 累積 | 部署 proxy（自動修正 Angular + 過濾 CF cookie）|
| JCR login loop（by Port）| CF cookie 長期累積 | 部署 proxy（過濾 CF cookie）|
| Chrome 啟動失敗 | 缺 X11/dbus 依賴 | 安裝 dbus-x11 + Xvfb |
| Conda TOS 錯誤 | 新版 Conda 需接受 TOS | `conda tos accept` |
| 學校防火牆擋 port | 只開放 80/443 | 用 nginx 反代 |
