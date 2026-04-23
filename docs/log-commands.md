# HyPass Log 常用指令參考

Service name: `bookstore-scraper`
Log 主要來源：systemd journald
額外 log 檔：`/opt/bookstore-scraper/logs/*.log`（monitor 腳本產生）

## 基本

```bash
# 即時 tail（Ctrl+C 結束）
journalctl -u bookstore-scraper -f

# 最近 100 行
journalctl -u bookstore-scraper -n 100 --no-pager

# 今天整天
journalctl -u bookstore-scraper --since today --no-pager

# 最近 1 小時 / 10 分鐘
journalctl -u bookstore-scraper --since "1 hour ago" --no-pager
journalctl -u bookstore-scraper --since "10 minutes ago" --no-pager

# 指定時間區間
journalctl -u bookstore-scraper --since "14:30:00" --until "14:40:00" --no-pager
```

## 搜尋

```bash
# 找錯誤
journalctl -u bookstore-scraper --since today --no-pager | grep -i error

# 看 cache 命中率
journalctl -u bookstore-scraper --since "1 hour ago" --no-pager | \
  grep -cE 'cache hit|Asset cached'

# 看 blocked domain 狀況
journalctl -u bookstore-scraper --since today --no-pager | grep 'blocked domain'

# 看 curl vs browser fallback 分布
journalctl -u bookstore-scraper --since today --no-pager | \
  grep -oE 'via (curl|BrowserPool|browser)' | sort | uniq -c

# 特定 URL / domain
journalctl -u bookstore-scraper --since "30 minutes ago" --no-pager | grep jcr.clarivate.com

# 看 session / cookie / PSSID 相關
journalctl -u bookstore-scraper --since today --no-pager | grep -iE 'session|pssid|cookie'

# 看 Angular domain patch 有沒有觸發
journalctl -u bookstore-scraper --since today --no-pager | grep 'Patched'
```

## 效能分析

```bash
# 每筆請求耗時 top 20 慢
journalctl -u bookstore-scraper --since today --no-pager | \
  grep -oE '\([0-9]+\.[0-9]+s\)' | sort -n | tail -20

# 平均 / 最慢 / 筆數
journalctl -u bookstore-scraper --since today --no-pager | \
  grep -oE '\(([0-9.]+)s\)' | sed 's/[^0-9.]//g' | \
  awk '{sum+=$1; n++; if($1>max)max=$1} END {printf "count=%d max=%.2fs avg=%.2fs\n", n, max, sum/n}'

# 今天各小時請求量
journalctl -u bookstore-scraper --since today --no-pager | \
  grep 'PROXY\|FETCH' | awk '{print $3}' | cut -d: -f1-2 | sort | uniq -c
```

## 匯出到檔案慢慢看

```bash
journalctl -u bookstore-scraper --since today --no-pager > /tmp/bs-today.log
wc -l /tmp/bs-today.log
less /tmp/bs-today.log
```

## API endpoints（彙總統計）

```bash
# 每筆 fetch 耗時 / driver / 記憶體
curl -s http://127.0.0.1:8101/monitor/history | python3 -m json.tool | less

# 當前 curl session 列表
curl -s http://127.0.0.1:8101/sessions | python3 -m json.tool

# 各 session 裡的 cookie（debug JCR 認證用）
curl -s http://127.0.0.1:8101/sessions/cookies | python3 -m json.tool

# 系統狀態（CPU / Memory / FD / threads）
curl -s http://127.0.0.1:8101/monitor | python3 -m json.tool
```

## Monitor 腳本產生的 log

```bash
# 當月 cookie monitor（Redis 狀態）
tail -20 /opt/bookstore-scraper/logs/cookie_monitor.$(date +%Y%m).log

# 當月 proxy monitor（CF/PSSID 過濾、JCR status 統計）
tail -20 /opt/bookstore-scraper/logs/proxy_monitor.$(date +%Y%m).log

# 手動觸發一次監控
/opt/bookstore-scraper/tools/monitor_cookies.sh
/opt/bookstore-scraper/tools/monitor_proxy.sh
```

## Service 狀態

```bash
# 簡短狀態
systemctl status bookstore-scraper --no-pager | grep -E 'Active|Memory|Main PID'

# 完整狀態
systemctl status bookstore-scraper

# 確認啟動時間（監測 in-memory cache 是否剛被清）
systemctl status bookstore-scraper | grep 'Active:'

# 重啟
systemctl restart bookstore-scraper
```

## HyProxy 側 log（不是 HyPass，但常一起查）

```bash
# HyProxy 當日 log
tail -50 /hyproxy/logs/hyproxy.log.$(date +%Y%m%d)
tail -50 /hyproxy/logs/event.log.$(date +%Y%m%d)

# 搜尋特定事件
grep -iE 'error|warn|expire' /hyproxy/logs/event.log.$(date +%Y%m%d)
grep -iE 'pendo|pssid|content-pendo' /hyproxy/logs/hyproxy.log.$(date +%Y%m%d)
```

## Redis 相關

```bash
# DB 大小（HyProxy session store）
redis-cli -n 9 DBSIZE

# 列出 session keys
redis-cli -n 9 keys "session_*" | head -10

# 看 session 內容（binary，過濾 null byte）
redis-cli -n 9 get "session_XXXX" | tr -d '\0' | head -c 500

# 清空（會踢掉所有使用者 session，慎用）
redis-cli -n 9 FLUSHDB
```
