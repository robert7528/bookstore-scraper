#!/bin/bash
# Proxy 運作狀態監控 — 服務狀態 + CF 過濾 + JCR auth 統計
# crontab: 0 * * * * /opt/bookstore-scraper/tools/monitor_proxy.sh

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
