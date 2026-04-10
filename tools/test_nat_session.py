#!/usr/bin/env python3
"""Test if NAT pool causes JCR session invalidation.

Verifies whether browser auth session survives by checking:
1. Auth via browser → get IC2_SID
2. Immediately fetch session-details from SAME browser → 200 or 401?
3. Check IP consistency during the session

Run on jumper:
  cd /opt/bookstore-scraper
  .venv/bin/python3 tools/test_nat_session.py
"""
import json
import time


def main():
    import undetected_chromedriver as uc
    import subprocess

    print("=" * 60)
    print("NAT Pool + JCR Session Test")
    print("=" * 60)

    # 1. Check IP stability first
    print("\n--- 1. IP stability check (system curl) ---")
    ips = set()
    for i in range(5):
        r = subprocess.run(["curl", "-s", "https://api.ipify.org"], capture_output=True, text=True, timeout=10)
        ip = r.stdout.strip()
        ips.add(ip)
        print(f"  curl #{i+1}: {ip}")
    print(f"  Unique IPs: {len(ips)} → {'UNSTABLE' if len(ips) > 1 else 'STABLE'}")

    # 2. Start browser
    print("\n--- 2. Starting Chrome ---")
    options = uc.ChromeOptions()
    options.add_argument("--no-first-run")
    options.add_argument("--no-default-browser-check")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--window-size=1280,720")

    headless = True
    try:
        # Check if Xvfb is available (non-headless on Linux)
        subprocess.run(["pgrep", "-f", "Xvfb"], capture_output=True, timeout=3)
        headless = False
    except Exception:
        pass

    if headless:
        options.add_argument("--headless=new")

    version_main = None
    try:
        result = subprocess.run(["google-chrome", "--version"], capture_output=True, text=True, timeout=5)
        version_main = int(result.stdout.strip().split()[-1].split(".")[0])
    except Exception:
        pass

    driver = uc.Chrome(options=options, version_main=version_main)
    driver.set_page_load_timeout(60)
    driver.set_script_timeout(30)
    print(f"  Chrome started (headless={headless})")

    try:
        # 3. Check browser IP
        print("\n--- 3. Browser IP check ---")
        driver.get("https://api.ipify.org")
        time.sleep(2)
        browser_ip = driver.find_element("tag name", "body").text.strip()
        print(f"  Browser IP: {browser_ip}")

        # Check IP multiple times from browser
        browser_ips = set()
        for i in range(3):
            result = driver.execute_async_script("""
                var cb = arguments[arguments.length - 1];
                fetch('https://api.ipify.org').then(r => r.text()).then(t => cb(t.trim())).catch(e => cb('error:'+e));
            """)
            browser_ips.add(result)
            print(f"  Browser fetch #{i+1}: {result}")
        print(f"  Browser IPs: {len(browser_ips)} unique → {'UNSTABLE' if len(browser_ips) > 1 else 'STABLE'}")

        # 4. Navigate to JCR and authenticate
        print("\n--- 4. JCR authentication ---")
        driver.get("https://jcr.clarivate.com/jcr/home")

        deadline = time.time() + 60
        authenticated = False
        while time.time() < deadline:
            time.sleep(2)
            cookies = {c["name"]: c["value"] for c in driver.get_cookies()}
            if "IC2_SID" in cookies:
                print(f"  IC2_SID obtained!")
                auth_ip = cookies.get("userAuthIDType", "unknown").strip('"')
                print(f"  Auth IP (userAuthIDType): {auth_ip}")
                authenticated = True
                break
            print(f"  Waiting... URL: {driver.current_url[:60]}")

        if not authenticated:
            print("  AUTH FAILED - cannot continue test")
            driver.save_screenshot("/tmp/nat_test_auth_fail.png")
            return

        # 5. Check browser IP after auth
        print("\n--- 5. IP after auth ---")
        post_auth_ip = driver.execute_async_script("""
            var cb = arguments[arguments.length - 1];
            fetch('https://api.ipify.org').then(r => r.text()).then(t => cb(t.trim())).catch(e => cb('error:'+e));
        """)
        print(f"  Current browser IP: {post_auth_ip}")
        print(f"  Auth was done with IP: {auth_ip}")
        print(f"  IP match: {post_auth_ip == auth_ip}")

        # 6. Test session-details from browser (same process)
        print("\n--- 6. session-details from browser fetch ---")
        for i in range(5):
            result = driver.execute_async_script("""
                var cb = arguments[arguments.length - 1];
                fetch('https://jcr.clarivate.com/api/jcr3/bwjournal/v1/session-details', {
                    credentials: 'include',
                    headers: {'Accept': 'application/json'}
                })
                .then(function(r) {
                    return r.text().then(function(t) {
                        cb({status: r.status, body: t.substring(0, 300), len: t.length});
                    });
                })
                .catch(function(e) { cb({status: 0, error: e.toString()}); });
            """)
            status = result.get("status", 0)
            body_len = result.get("len", 0)
            error = result.get("error", "")
            body_preview = result.get("body", "")[:100]
            print(f"  #{i+1}: status={status}, size={body_len}, body={body_preview}")
            if error:
                print(f"       error: {error}")
            time.sleep(1)

        # 7. Check if IP changed during fetch tests
        print("\n--- 7. IP after fetch tests ---")
        final_ip = driver.execute_async_script("""
            var cb = arguments[arguments.length - 1];
            fetch('https://api.ipify.org').then(r => r.text()).then(t => cb(t.trim())).catch(e => cb('error:'+e));
        """)
        print(f"  Browser IP now: {final_ip}")
        print(f"  Auth IP was:    {auth_ip}")
        print(f"  Still match:    {final_ip == auth_ip}")

        # 8. Check all cookies
        print("\n--- 8. Current cookies ---")
        for c in driver.get_cookies():
            if c["name"] in ("IC2_SID", "PSSID", "ACCESS_METHOD", "userAuthType",
                             "userAuthIDType", "CUSTOMER_NAME", "clearStatus"):
                print(f"  {c['name']} = {c['value'][:50]}... (domain: {c['domain']})")

        # 9. Summary
        print("\n" + "=" * 60)
        print("SUMMARY")
        print("=" * 60)
        print(f"  System curl IPs:   {ips}")
        print(f"  Browser IPs:       {browser_ips}")
        print(f"  Auth IP:           {auth_ip}")
        print(f"  Post-auth IP:      {post_auth_ip}")
        print(f"  Final IP:          {final_ip}")

        all_browser_same = len(browser_ips) == 1
        auth_match = post_auth_ip == auth_ip
        final_match = final_ip == auth_ip

        if all_browser_same and auth_match and final_match:
            print("  → Browser IP is STABLE within session")
            print("  → NAT pool should NOT affect browser-based auth")
        else:
            print("  → Browser IP CHANGED during session!")
            print("  → NAT pool IS causing session issues")

    finally:
        try:
            driver.quit()
        except Exception:
            pass
        # Cleanup
        import os, signal
        try:
            subprocess.run(["pkill", "-9", "-f", "undetected_chromedriver"], capture_output=True, timeout=5)
        except Exception:
            pass
        print("\nBrowser closed.")


if __name__ == "__main__":
    main()
