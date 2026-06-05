#!/usr/bin/env python3
"""
Test nodriver against academic.oup.com CF Interactive Turnstile.
Run inside hypass pod (which has Chrome + Xvfb).

Usage (inside hypass pod):
    pip install nodriver
    xvfb-run -a python /tmp/test_nodriver_oup.py
"""
from __future__ import annotations

import asyncio
import sys
import nodriver as uc


URL = "https://academic.oup.com/bjaesthetics"
WAIT_SECONDS = 30
SCREENSHOT_PATH = "/tmp/nodriver_test.png"


async def main():
    print(f"=== Testing nodriver against {URL} ===", flush=True)

    browser = await uc.start(
        headless=False,
        browser_args=[
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
        ],
    )

    try:
        print("[*] Navigating...", flush=True)
        page = await browser.get(URL)

        print(f"[*] Waiting {WAIT_SECONDS}s for CF challenge resolution...", flush=True)
        for i in range(WAIT_SECONDS):
            await asyncio.sleep(1)
            if i % 5 == 4:
                print(f"    ... {i+1}s", flush=True)

        print(f"[*] Saving screenshot to {SCREENSHOT_PATH}", flush=True)
        await page.save_screenshot(SCREENSHOT_PATH)

        print("[*] Getting page content...", flush=True)
        content = await page.get_content()

        if any(s in content.lower() for s in ("just a moment", "verifying you are", "正在執行安全驗證", "performing security")):
            print("[!] Still on CF challenge page", flush=True)
            print(f"[!] Content length: {len(content)} chars", flush=True)
            print(f"[!] Content preview: {content[:800]}", flush=True)
        elif "British Journal of Aesthetics" in content or "BJAESTHETICS" in content.upper():
            print("[✓] SUCCESS! Got BJA page content", flush=True)
            print(f"[✓] Content length: {len(content)} chars", flush=True)
        else:
            print(f"[?] Unknown state — content length: {len(content)}", flush=True)
            print(f"[?] Preview: {content[:500]}", flush=True)

        cookies = await browser.cookies.get_all()
        cf_cookies = [c for c in cookies if c.name.lower().startswith("cf_") or c.name == "__cf_bm"]
        print(f"\n[*] CF cookies seen: {len(cf_cookies)}", flush=True)
        for c in cf_cookies:
            val = c.value[:50] + "..." if len(c.value) > 50 else c.value
            print(f"    {c.name} = {val}  (domain={c.domain})", flush=True)

        has_clearance = any(c.name == "cf_clearance" for c in cookies)
        if has_clearance:
            print("\n[✓] cf_clearance cookie present — CF challenge passed", flush=True)
            sys.exit(0)
        else:
            print("\n[!] No cf_clearance — CF challenge NOT passed", flush=True)
            sys.exit(1)

    finally:
        try:
            browser.stop()
        except Exception:
            pass


if __name__ == "__main__":
    asyncio.run(main())
