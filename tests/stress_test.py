"""
Stress test — 測試博客來在不同頻率下的封鎖行為。

Usage:
    python tests/stress_test.py --total 20 --delay 0.5
    python tests/stress_test.py --total 50 --delay 0
    python tests/stress_test.py --total 100 --concurrency 5 --delay 0
"""
from __future__ import annotations

import argparse
import asyncio
import time

import re

from src.scraper.engine import ScraperEngine

URL_TEMPLATE = "https://search.books.com.tw/search/query/key/{keyword}/cat/all/v/0/page/{page}"

KEYWORDS = ["python", "java", "AI", "機器學習", "小說", "歷史", "經濟", "設計"]

# 內容驗證：確認回傳的是真正的搜尋結果頁面
CHALLENGE_SIGNS = ["challenge-platform", "cf-browser-verification", "turnstile", "just a moment"]
VALID_SIGNS = ["table-searchlist", "itemlist_"]


def validate_content(html: str) -> tuple[str, int]:
    """回傳 (狀態, 解析到的筆數)。狀態: ok / challenge / empty / unknown"""
    html_lower = html[:5000].lower()

    for sign in CHALLENGE_SIGNS:
        if sign in html_lower:
            return "challenge", 0

    item_count = len(re.findall(r'id="itemlist_', html))

    if item_count > 0:
        return "ok", item_count

    if "search_results" in html_lower and item_count == 0:
        return "empty", 0

    return "unknown", 0


async def single_request(engine: ScraperEngine, idx: int, keyword: str, page: int) -> dict:
    url = URL_TEMPLATE.format(keyword=keyword, page=page)
    t0 = time.perf_counter()
    try:
        resp = await engine.get(url)
        elapsed = time.perf_counter() - t0
        content_status, item_count = validate_content(resp.text)
        is_blocked = resp.status_code == 403 or content_status == "challenge"
        return {
            "idx": idx,
            "keyword": keyword,
            "status": resp.status_code,
            "content": content_status,
            "items": item_count,
            "length": len(resp.text),
            "elapsed": round(elapsed, 2),
            "blocked": is_blocked,
        }
    except Exception as e:
        elapsed = time.perf_counter() - t0
        return {
            "idx": idx,
            "keyword": keyword,
            "status": -1,
            "content": "error",
            "items": 0,
            "length": 0,
            "elapsed": round(elapsed, 2),
            "blocked": True,
            "error": str(e),
        }


async def run_sequential(total: int, delay: float):
    """逐筆發送，觀察何時開始被擋。"""
    print(f"=== Sequential: {total} requests, delay={delay}s ===\n")
    results = []
    async with ScraperEngine() as engine:
        for i in range(total):
            kw = KEYWORDS[i % len(KEYWORDS)]
            page = (i // len(KEYWORDS)) + 1
            r = single_request(engine, i + 1, kw, page)
            result = await r
            status_icon = "X" if result["blocked"] else "O"
            print(f"  [{result['idx']:3d}] {status_icon}  HTTP {result['status']}  {result['content']:9s}  items={result['items']:2d}  {result['elapsed']}s  {result['keyword']}")
            results.append(result)
            if delay > 0:
                await asyncio.sleep(delay)

    return results


async def run_concurrent(total: int, concurrency: int, delay: float):
    """並發發送，測試高併發是否觸發封鎖。"""
    print(f"=== Concurrent: {total} requests, concurrency={concurrency}, delay={delay}s ===\n")
    sem = asyncio.Semaphore(concurrency)
    results = []

    async def bounded(engine, idx, kw, page):
        async with sem:
            result = await single_request(engine, idx, kw, page)
            status_icon = "X" if result["blocked"] else "O"
            print(f"  [{result['idx']:3d}] {status_icon}  HTTP {result['status']}  {result['content']:9s}  items={result['items']:2d}  {result['elapsed']}s  {result['keyword']}")
            if delay > 0:
                await asyncio.sleep(delay)
            return result

    async with ScraperEngine() as engine:
        tasks = []
        for i in range(total):
            kw = KEYWORDS[i % len(KEYWORDS)]
            page = (i // len(KEYWORDS)) + 1
            tasks.append(bounded(engine, i + 1, kw, page))
        results = await asyncio.gather(*tasks)

    return list(results)


def print_summary(results: list[dict]):
    total = len(results)
    blocked = sum(1 for r in results if r["blocked"])
    content_ok = sum(1 for r in results if r["content"] == "ok")
    content_empty = sum(1 for r in results if r["content"] == "empty")
    content_challenge = sum(1 for r in results if r["content"] == "challenge")
    content_unknown = sum(1 for r in results if r["content"] == "unknown")
    total_items = sum(r["items"] for r in results)
    avg_time = sum(r["elapsed"] for r in results) / total if total else 0

    print(f"\n{'='*60}")
    print(f"  Total requests:  {total}")
    print(f"  HTTP blocked:    {blocked}  ({blocked/total*100:.1f}%)")
    print(f"  Content OK:      {content_ok}  ({content_ok/total*100:.1f}%)  total items={total_items}")
    print(f"  Content empty:   {content_empty}")
    print(f"  Content challenge: {content_challenge}")
    print(f"  Content unknown: {content_unknown}")
    print(f"  Avg time:        {avg_time:.2f}s")

    for r in results:
        if r["blocked"] or r["content"] != "ok":
            print(f"  !! First problem at request #{r['idx']}: {r['content']}")
            break
    else:
        print(f"  All requests returned valid search results")
    print(f"{'='*60}")


async def main():
    parser = argparse.ArgumentParser(description="Stress test for bookstore scraper")
    parser.add_argument("--total", "-n", type=int, default=20, help="Total requests")
    parser.add_argument("--delay", "-d", type=float, default=0.5, help="Delay between requests (seconds)")
    parser.add_argument("--concurrency", "-c", type=int, default=0, help="Concurrent requests (0=sequential)")
    args = parser.parse_args()

    t0 = time.perf_counter()

    if args.concurrency > 0:
        results = await run_concurrent(args.total, args.concurrency, args.delay)
    else:
        results = await run_sequential(args.total, args.delay)

    elapsed = time.perf_counter() - t0
    print_summary(results)
    print(f"  Wall time: {elapsed:.1f}s")


if __name__ == "__main__":
    asyncio.run(main())
