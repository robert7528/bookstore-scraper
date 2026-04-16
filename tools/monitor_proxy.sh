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
CF_FILTERED=$(echo "$LOGS" | grep -c "Managed cookie filtered: __cf_bm\|Managed cookie filtered: cf_clearance\|CF cookie filtered")
PSSID_FILTERED=$(echo "$LOGS" | grep -c "Managed cookie filtered: PSSID\|Managed cookie filtered: IC2_SID")
JS_PATCHED=$(echo "$LOGS" | grep -c "Patched")
JCR_200=$(echo "$LOGS" | grep "session-details" | grep -c "200")
JCR_500=$(echo "$LOGS" | grep "session-details" | grep -c "500")
ERRORS=$(echo "$LOGS" | grep "ERROR" | grep -v "Task was destroyed but it is pending" | grep -c "ERROR")

# Check HyProxy log for sessionExpired (today's log)
HYPROXY_LOG="/hyproxy/logs/hyproxy.log.$(date '+%Y%m%d')"
if [ -f "$HYPROXY_LOG" ]; then
    SESSION_EXPIRED=$(grep -c "sessionExpired" "$HYPROXY_LOG" 2>/dev/null)
else
    SESSION_EXPIRED="-"
fi

echo "$DATE | status=$STATUS mem=$MEM | CF_filtered=$CF_FILTERED PSSID_filtered=$PSSID_FILTERED JS_patched=$JS_PATCHED JCR_200=$JCR_200 JCR_500=$JCR_500 errors=$ERRORS sessionExpired=$SESSION_EXPIRED" >> $LOGFILE
[ "$STATUS" != "running" ] && echo "$DATE | ALERT: service not running!" >> $LOGFILE
find $LOGDIR -name "proxy_monitor.*.log" -mtime +90 -delete 2>/dev/null
