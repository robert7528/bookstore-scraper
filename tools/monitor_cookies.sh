#!/bin/bash
# Redis Cookie 監控 — 檢查 CF cookie 是否混入 session
# crontab: 0 * * * * /opt/bookstore-scraper/tools/monitor_cookies.sh

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
