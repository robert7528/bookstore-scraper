#!/bin/bash
# Redis Cookie 監控 — 檢查 CF cookie 是否混入 session
# crontab: 0 * * * * /opt/bookstore-scraper/tools/monitor_cookies.sh

DATE=$(date '+%Y-%m-%d %H:%M')
LOGDIR=/opt/bookstore-scraper/logs
LOGFILE=$LOGDIR/cookie_monitor.$(date '+%Y%m').log
mkdir -p $LOGDIR

# redis-cli DBSIZE 不同版本輸出「(integer) N」或單獨「N」，用 grep 抓純數字
DB_SIZE=$(redis-cli -n 9 DBSIZE 2>/dev/null | grep -oE '[0-9]+' | head -1)
JCR_KEYS=$(redis-cli -n 9 keys "*jcr*" 2>/dev/null | wc -l)

CF_COOKIE=0
PSSID_COOKIE=0
SESSIONS=$(redis-cli -n 9 keys "session_*" 2>/dev/null)
if [ -n "$SESSIONS" ]; then
    for k in $SESSIONS; do
        # tr -d '\0': HyProxy session 存 binary，bash 變數不能含 null byte，先濾掉避免 warning
        VAL=$(redis-cli -n 9 get "$k" 2>/dev/null | tr -d '\0')
        echo "$VAL" | grep -q "__cf_bm" && CF_COOKIE=$((CF_COOKIE+1))
        echo "$VAL" | grep -q "PSSID" && PSSID_COOKIE=$((PSSID_COOKIE+1))
    done
fi

echo "$DATE | Redis keys: $DB_SIZE | JCR cache: $JCR_KEYS | CF in session: $CF_COOKIE | PSSID in session: $PSSID_COOKIE" >> $LOGFILE
[ "$CF_COOKIE" -gt 0 ] && echo "$DATE | WARNING: CF cookie in session!" >> $LOGFILE
[ "$PSSID_COOKIE" -gt 0 ] && echo "$DATE | WARNING: PSSID in Redis session!" >> $LOGFILE
find $LOGDIR -name "cookie_monitor.*.log" -mtime +90 -delete 2>/dev/null
