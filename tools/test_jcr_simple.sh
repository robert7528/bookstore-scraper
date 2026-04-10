#!/bin/bash
# JCR 快速診斷腳本 — 不需要安裝 bookstore-scraper
# 在客戶主機上直接跑：bash test_jcr_simple.sh
#
# 檢查項目：
# 1. 出口 IP 穩定性
# 2. JCR CF bypass（curl 能不能直接連）
# 3. Clarivate IP 認證
# 4. login.incites 行為
# 5. Redis cookie 狀態

echo "============================================================"
echo "JCR 快速診斷"
echo "時間: $(date)"
echo "主機: $(hostname)"
echo "============================================================"

# 1. IP 穩定性
echo ""
echo "=== 1. 出口 IP 穩定性 ==="
IPS=""
for i in 1 2 3 4 5; do
    IP=$(curl -s --max-time 5 https://api.ipify.org 2>/dev/null)
    echo "  #$i: $IP"
    IPS="$IPS $IP"
done
UNIQUE=$(echo $IPS | tr ' ' '\n' | sort -u | wc -l)
if [ "$UNIQUE" -eq 1 ]; then
    echo "  結果: 穩定 ✅ ($UNIQUE 個 IP)"
else
    echo "  結果: 不穩定 ❌ ($UNIQUE 個不同 IP)"
    echo "  ⚠️ NAT pool 會導致 JCR auth 失敗"
fi

# 2. JCR 直連測試
echo ""
echo "=== 2. JCR 直連測試 ==="
STATUS=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 "https://jcr.clarivate.com/jcr/home")
echo "  jcr.clarivate.com/jcr/home → HTTP $STATUS"
if [ "$STATUS" = "200" ]; then
    echo "  結果: JCR 可連 ✅"
else
    echo "  結果: JCR 連線異常 ❌"
fi

# 3. Clarivate 看到的 IP
echo ""
echo "=== 3. Clarivate 偵測到的 IP ==="
BODY=$(curl -s --max-time 10 "https://access.clarivate.com/login?app=jcr&detectSession=true")
DETECTED_IP=$(echo "$BODY" | grep -oP 'globalIpAddress\s*=\s*"\K[^"]+')
echo "  Clarivate 看到: $DETECTED_IP"
echo "  ifconfig.me:    $(echo $IPS | awk '{print $1}')"
if [ "$DETECTED_IP" = "$(echo $IPS | awk '{print $1}')" ]; then
    echo "  結果: IP 一致 ✅"
else
    echo "  結果: IP 不一致 ⚠️ (可能有多出口)"
fi

# 4. login.incites 測試
echo ""
echo "=== 4. login.incites IP 認證 ==="
LOCATION=$(curl -s -o /dev/null -w "%{redirect_url}" --max-time 10 "https://login.incites.clarivate.com/?DestApp=IC2JCR")
echo "  redirect: $LOCATION"
if echo "$LOCATION" | grep -q "authCode" && ! echo "$LOCATION" | grep -q "authCode=null"; then
    echo "  結果: IP 認證通過 ✅ (有 authCode)"
else
    echo "  結果: IP 認證未通過 (server-side 不認，需要 client-side Angular 處理)"
fi

# 5. session-details 測試
echo ""
echo "=== 5. JCR session-details ==="
STATUS=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 "https://jcr.clarivate.com/api/jcr3/bwjournal/v1/session-details")
echo "  session-details → HTTP $STATUS"
echo "  (500/401 = 未認證，正常 — 認證由瀏覽器 JS 處理)"

# 6. HyProxy 設定檢查
echo ""
echo "=== 6. HyProxy 設定 ==="
if [ -f /hyproxy/conf/config.yml ]; then
    echo "  主設定: /hyproxy/conf/config.yml"
    PROXY_SETTING=$(grep -A 2 "antibot" /hyproxy/conf/config.yml 2>/dev/null)
    if [ -n "$PROXY_SETTING" ]; then
        echo "  antibot proxy: $(echo $PROXY_SETTING | grep address | awk '{print $2}')"
    else
        echo "  antibot proxy: 未設定 ❌"
    fi
    REDIS=$(grep -A 2 "redis-config" /hyproxy/conf/config.yml | grep tcp | awk '{print $2}')
    echo "  Redis: $REDIS"
else
    echo "  找不到 /hyproxy/conf/config.yml"
fi

# 7. Redis 檢查
echo ""
echo "=== 7. Redis cookie 狀態 ==="
if command -v redis-cli &>/dev/null; then
    REDIS_SELECT=$(grep -A 2 "redis-config" /hyproxy/conf/config.yml 2>/dev/null | grep select | awk '{print $2}')
    REDIS_SELECT=${REDIS_SELECT:-0}
    DB_SIZE=$(redis-cli -n $REDIS_SELECT DBSIZE 2>/dev/null | awk '{print $2}')
    echo "  Redis DB $REDIS_SELECT: $DB_SIZE keys"

    # 檢查有沒有 __cf_bm 在 session 裡
    CF_IN_SESSION=0
    for k in $(redis-cli -n $REDIS_SELECT keys "session_*" 2>/dev/null); do
        if redis-cli -n $REDIS_SELECT get "$k" 2>/dev/null | grep -q "__cf_bm"; then
            CF_IN_SESSION=$((CF_IN_SESSION+1))
        fi
    done
    echo "  Session 裡的 CF cookie: $CF_IN_SESSION"
    if [ "$CF_IN_SESSION" -gt 0 ]; then
        echo "  ⚠️ CF cookie 在 session 裡，可能導致 loop"
    fi

    # ShareCookie 檢查
    SHARE_KEYS=$(redis-cli -n $REDIS_SELECT keys "*clarivate*" 2>/dev/null | wc -l)
    echo "  Clarivate 相關 keys: $SHARE_KEYS"
else
    echo "  redis-cli 不存在"
fi

# 8. bookstore-scraper 狀態
echo ""
echo "=== 8. bookstore-scraper 狀態 ==="
if systemctl is-active bookstore-scraper &>/dev/null; then
    echo "  服務: 運行中 ✅"
    MEM=$(systemctl status bookstore-scraper 2>/dev/null | grep Memory | awk '{print $2}')
    echo "  記憶體: $MEM"
    PROXY_LISTEN=$(ss -tlnp | grep 8102)
    if [ -n "$PROXY_LISTEN" ]; then
        echo "  Proxy 8102: listening ✅"
    else
        echo "  Proxy 8102: 未啟動 ❌"
    fi
else
    echo "  服務: 未安裝或未啟動"
fi

echo ""
echo "============================================================"
echo "診斷完成"
echo "============================================================"
