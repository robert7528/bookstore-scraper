#!/usr/bin/env python3
"""Test JCR authentication flow via curl_cffi and browser fallback.

Run on jumper:
  cd /opt/bookstore-scraper
  .venv/bin/python3 tools/test_jcr_auth.py
"""
import asyncio
import json
import re
import sys
import time

# ---------------------------------------------------------------------------
# 1. curl_cffi: CF bypass + auth API testing
# ---------------------------------------------------------------------------
def test_curl_cffi():
    from curl_cffi.requests import Session

    print("=" * 60)
    print("Phase 1: curl_cffi — CF bypass + auth API tests")
    print("=" * 60)

    s = Session(impersonate="chrome136", verify=False)

    # 1a. JCR home (CF protected)
    print("\n--- 1a. JCR home page (CF bypass) ---")
    r = s.get("https://jcr.clarivate.com/jcr/home")
    print(f"Status: {r.status_code}, Size: {len(r.text)} bytes")
    is_cf = "<title>Just a moment" in r.text
    print(f"CF challenge: {is_cf}")
    if is_cf:
        print("ERROR: CF bypass failed!")
        return None

    # 1b. JCR session-details (needs auth)
    print("\n--- 1b. JCR session-details (needs auth) ---")
    r = s.get("https://jcr.clarivate.com/api/jcr3/bwjournal/v1/session-details")
    print(f"Status: {r.status_code}, Body: {r.text[:200]}")

    # 1c. access.clarivate.com login page (get cookies + IP)
    print("\n--- 1c. access.clarivate.com login page ---")
    r = s.get("https://access.clarivate.com/login?app=jcr&detectSession=true")
    print(f"Status: {r.status_code}, Size: {len(r.text)} bytes")
    ip_match = re.search(r'globalIpAddress\s*=\s*"([^"]+)"', r.text)
    if ip_match:
        print(f"Detected IP: {ip_match.group(1)}")
    print(f"Session cookies: {dict(s.cookies)}")

    # 1d. /api/session/access — the key call Angular app should make
    print("\n--- 1d. /api/session/access (detectSession check) ---")
    r = s.get("https://access.clarivate.com/api/session/access",
              params={"app": "jcr"})
    print(f"Status: {r.status_code}, Body: {r.text[:300]}")

    # 1e. /api/ip/authorize — IP auth (needs API key?)
    print("\n--- 1e. /api/ip/authorize ---")
    # Try with various headers that might provide the API key
    headers_to_try = [
        {},
        {"X-Api-Key": "jcr"},
        {"X-1P-WOS-SID": "test"},
        {"Origin": "https://access.clarivate.com",
         "Referer": "https://access.clarivate.com/login?app=jcr"},
    ]
    for h in headers_to_try:
        r = s.get("https://access.clarivate.com/api/ip/authorize",
                  params={"app": "jcr"}, headers=h)
        label = str(h) if h else "(no extra headers)"
        print(f"  {label[:60]}: {r.status_code} — {r.text[:100]}")

    # 1f. /app/api/user/validate/ip — IP validation endpoint
    print("\n--- 1f. /app/api/user/validate/ip ---")
    r = s.get("https://access.clarivate.com/app/api/user/validate/ip",
              params={"app": "jcr"})
    print(f"Status: {r.status_code}, Body: {r.text[:300]}")

    # 1g. /app/api/user/ip/auth — IP auth endpoint
    print("\n--- 1g. /app/api/user/ip/auth ---")
    for method in ["GET", "POST"]:
        r = s.request(method, "https://access.clarivate.com/app/api/user/ip/auth",
                      params={"app": "jcr"},
                      headers={"Content-Type": "application/json"},
                      data=json.dumps({"app": "jcr"}) if method == "POST" else None)
        print(f"  {method}: {r.status_code} — {r.text[:200]}")

    # 1h. /api/authorize/auto — auto authorize
    print("\n--- 1h. /api/authorize/auto ---")
    r = s.post("https://access.clarivate.com/api/authorize/auto",
               headers={"Content-Type": "application/json"},
               data=json.dumps({"app": "jcr"}))
    print(f"Status: {r.status_code}, Body: {r.text[:300]}")

    # 1i. login.incites flow with cookies
    print("\n--- 1i. login.incites → follow redirects ---")
    r = s.get("https://login.incites.clarivate.com/?DestApp=IC2JCR",
              allow_redirects=True)
    print(f"Final URL: {r.url}")
    print(f"Status: {r.status_code}, Size: {len(r.text)} bytes")
    print(f"All cookies: {dict(s.cookies)}")

    # Check for auth cookies
    auth_cookies = {k: v for k, v in s.cookies.items()
                    if k in ("IC2_SID", "PSSID", "ACCESS_METHOD", "userAuthType")}
    if auth_cookies:
        print(f"\n*** AUTH SUCCESS! Auth cookies: {auth_cookies}")
        return dict(s.cookies)
    else:
        print("\n*** No auth cookies obtained via curl_cffi")
        return None


# ---------------------------------------------------------------------------
# 2. Browser: full JS-based auth flow
# ---------------------------------------------------------------------------
def test_browser():
    print("\n" + "=" * 60)
    print("Phase 2: Browser — full JS auth flow (undetected-chromedriver)")
    print("=" * 60)

    try:
        import undetected_chromedriver as uc
    except ImportError:
        print("undetected-chromedriver not installed, skipping")
        return None

    options = uc.ChromeOptions()
    # Run with display (Xvfb on Linux)
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1280,720")

    print("\nStarting Chrome...")
    driver = uc.Chrome(options=options)

    try:
        # 2a. Visit JCR directly
        print("\n--- 2a. Visit jcr.clarivate.com ---")
        driver.get("https://jcr.clarivate.com/jcr/home")
        time.sleep(5)
        print(f"URL: {driver.current_url}")
        print(f"Title: {driver.title}")

        # 2b. Check if redirected to login
        if "access.clarivate.com" in driver.current_url:
            print("Redirected to access.clarivate.com login page")
            print("Waiting for IP-based auth to complete...")
            # Wait for potential IP auth (up to 30s)
            for i in range(30):
                time.sleep(2)
                url = driver.current_url
                print(f"  [{i*2}s] URL: {url[:80]}")
                if "jcr.clarivate.com" in url:
                    print("  Redirected back to JCR! Auth may have succeeded.")
                    break
                if "authCode=" in url and "authCode=null" not in url:
                    print(f"  Got authCode!")
                    break

        # 2c. Try access.clarivate.com login directly
        if "jcr.clarivate.com" not in driver.current_url:
            print("\n--- 2b. Try access.clarivate.com directly ---")
            driver.get("https://access.clarivate.com/login?app=jcr&detectSession=true")
            time.sleep(10)
            print(f"URL: {driver.current_url}")
            print(f"Title: {driver.title}")

        # 2d. Check cookies
        print("\n--- Cookies ---")
        cookies = driver.get_cookies()
        auth_cookies = {}
        for c in cookies:
            print(f"  {c['name']} = {c['value'][:50]}... (domain: {c['domain']})")
            if c['name'] in ("IC2_SID", "PSSID", "ACCESS_METHOD",
                             "userAuthType", "CUSTOMER_NAME", "IP_SET_ID_NAME"):
                auth_cookies[c['name']] = c['value']

        if auth_cookies:
            print(f"\n*** AUTH SUCCESS! Auth cookies: {json.dumps(auth_cookies, indent=2)}")

            # 2e. Test JCR API with auth cookies
            print("\n--- 2e. Visit JCR with auth cookies ---")
            driver.get("https://jcr.clarivate.com/jcr/home")
            time.sleep(5)
            print(f"URL: {driver.current_url}")
            print(f"Title: {driver.title}")

            # Try session-details
            driver.get("https://jcr.clarivate.com/api/jcr3/bwjournal/v1/session-details")
            time.sleep(2)
            body = driver.find_element("tag name", "body").text
            print(f"session-details: {body[:300]}")

            return auth_cookies
        else:
            print("\n*** No auth cookies from browser")

            # 2f. Try login.incites directly
            print("\n--- 2f. Try login.incites directly ---")
            driver.get("https://login.incites.clarivate.com/?DestApp=IC2JCR")
            time.sleep(10)
            print(f"URL: {driver.current_url}")

            cookies = driver.get_cookies()
            for c in cookies:
                if c['name'] in ("IC2_SID", "PSSID", "ACCESS_METHOD", "userAuthType"):
                    auth_cookies[c['name']] = c['value']

            if auth_cookies:
                print(f"*** AUTH SUCCESS via login.incites! {json.dumps(auth_cookies, indent=2)}")
                return auth_cookies
            else:
                print("*** Still no auth cookies")
                # Print page source for debugging
                print(f"Page title: {driver.title}")
                print(f"Current URL: {driver.current_url}")
                return None

    finally:
        driver.quit()
        print("\nBrowser closed.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    print("JCR Authentication Flow Test")
    print(f"Time: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print()

    # Phase 1: curl_cffi
    cookies = test_curl_cffi()

    if cookies:
        print("\n" + "=" * 60)
        print("curl_cffi auth SUCCEEDED — no browser needed!")
        print("=" * 60)
        return

    # Phase 2: browser fallback
    cookies = test_browser()

    if cookies:
        print("\n" + "=" * 60)
        print("Browser auth SUCCEEDED")
        print("=" * 60)
    else:
        print("\n" + "=" * 60)
        print("ALL AUTH METHODS FAILED")
        print("This IP may not be in Clarivate's TrustedIP whitelist.")
        print("=" * 60)


if __name__ == "__main__":
    main()
