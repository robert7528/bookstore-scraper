#!/usr/bin/env python3
"""
模擬瀏覽器 cookie jar，走 JCR auth redirect chain，
追蹤 __cf_bm cookie 在 HyProxy cookie-domain rewrite 後的覆蓋過程。

用法：python3 tools/test_cookie_flow.py
"""

import subprocess
import re
import sys

CONFIG_NAME = "libdb.yuntech.edu.tw"


def curl_one(url, timeout=15):
    """用 curl 發一個請求，回傳 (status, headers_list, set_cookies_raw)"""
    try:
        result = subprocess.run(
            ["curl", "-k", "-s", "-D", "-", "-o", "/dev/null",
             "--max-time", str(timeout), url],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout + 5
        )
        stdout = result.stdout.decode("utf-8", errors="replace")
    except Exception as e:
        return 0, [], []

    status = 0
    headers = []
    cookies_raw = []
    for line in stdout.split("\n"):
        line = line.strip()
        m = re.match(r"HTTP/[\d.]+ (\d+)", line)
        if m:
            status = int(m.group(1))
        if ":" in line:
            k, v = line.split(":", 1)
            headers.append((k.strip(), v.strip()))
            if k.strip().lower() == "set-cookie":
                cookies_raw.append(v.strip())
    return status, headers, cookies_raw


def parse_cookie_attrs(raw):
    """解析 Set-Cookie 的 name, value, domain, path"""
    parts = raw.split(";")
    nv = parts[0].strip()
    eq = nv.index("=") if "=" in nv else len(nv)
    name = nv[:eq].strip()
    value = nv[eq+1:].strip() if eq < len(nv) else ""
    domain = ""
    path = "/"
    for p in parts[1:]:
        p = p.strip()
        if p.lower().startswith("domain="):
            domain = p.split("=", 1)[1].strip()
        elif p.lower().startswith("path="):
            path = p.split("=", 1)[1].strip()
    return name, value[:20] + "...", domain, path


def hyproxy_rewrite_domain(original_domain, profile_host, cookie_domain_setting):
    """模擬 HyProxy profile.go:636-674 的 cookie domain rewrite"""
    domain0 = profile_host
    if ":" in domain0:
        domain0 = domain0[:domain0.index(":")]
    dot1 = domain0.find(".")
    domain1 = domain0[dot1 + 1:] if dot1 >= 0 else ""
    dot2 = domain1.find(".")
    domain2 = domain1[dot2 + 1:] if dot2 >= 0 else ""

    od = original_domain.lower()
    domain_level = -1
    if domain0 and domain0 in od:
        domain_level = 0
    elif "." in domain1 and ("." + domain1) in od:
        domain_level = 1
    elif "." in domain2 and ("." + domain2) in od:
        domain_level = 2

    if cookie_domain_setting == "1":
        dot = CONFIG_NAME.index(".")
        return CONFIG_NAME[dot:]
    else:
        t0 = profile_host.replace(".", "-").replace(":", "-") + "." + CONFIG_NAME
        dot1 = t0.index(".")
        t1 = t0[dot1 + 1:]
        dot2 = t1.find(".")
        t2 = t1[dot2 + 1:] if dot2 >= 0 else ""
        if domain_level == 1:
            return "." + t1
        elif domain_level == 2:
            return "." + t2
        else:
            return t0


def get_location(headers):
    for k, v in headers:
        if k.lower() == "location":
            return v
    return None


def main():
    print("=" * 70)
    print("JCR Auth Flow Cookie Jar Simulation")
    print("=" * 70)

    # 模擬 auth flow 的請求順序
    steps = [
        ("1. JCR home", "https://jcr.clarivate.com/jcr/home", "jcr.clarivate.com"),
        ("2. login.incites", "https://login.incites.clarivate.com/?DestApp=IC2JCR", "login.incites.clarivate.com"),
        ("3. access.clarivate", "https://access.clarivate.com/login?app=jcr&detectSession=true", "access.clarivate.com"),
    ]

    # 兩種模式的 cookie jar
    jar_with = {}     # cookie-domain="1"
    jar_without = {}  # 不設 cookie-domain

    events_with = []
    events_without = []

    for step_name, url, profile_host in steps:
        print(f"\n--- {step_name}: {url} ---")
        status, headers, cookies_raw = curl_one(url)
        print(f"  Status: {status}")

        # 如果有 redirect，顯示 Location
        loc = get_location(headers)
        if loc:
            print(f"  Location: {loc[:100]}")

        if not cookies_raw:
            print("  (no Set-Cookie)")
            continue

        for raw in cookies_raw:
            name, val_short, orig_domain, path = parse_cookie_attrs(raw)
            print(f"  Set-Cookie: {name}={val_short} | domain={orig_domain} | path={path}")

            # 模擬 cookie-domain="1" 的 rewrite
            new_domain_1 = hyproxy_rewrite_domain(orig_domain, profile_host, "1")
            key_1 = (name, new_domain_1, path)
            old_1 = jar_with.get(key_1)
            jar_with[key_1] = {"from": profile_host, "value": val_short}
            if old_1 and old_1["from"] != profile_host:
                events_with.append({
                    "step": step_name,
                    "cookie": name,
                    "domain": new_domain_1,
                    "old_from": old_1["from"],
                    "new_from": profile_host,
                })

            # 模擬不設 cookie-domain 的 rewrite
            new_domain_no = hyproxy_rewrite_domain(orig_domain, profile_host, "")
            key_no = (name, new_domain_no, path)
            old_no = jar_without.get(key_no)
            jar_without[key_no] = {"from": profile_host, "value": val_short}
            if old_no and old_no["from"] != profile_host:
                events_without.append({
                    "step": step_name,
                    "cookie": name,
                    "domain": new_domain_no,
                    "old_from": old_no["from"],
                    "new_from": profile_host,
                })

    # --- 結果 ---
    print("\n" + "=" * 70)
    print("Cookie Jar - cookie-domain=\"1\"")
    print("=" * 70)
    for (name, domain, path), info in sorted(jar_with.items()):
        print(f"  {name:<15} domain={domain:<30} from={info['from']}")

    print("\n" + "=" * 70)
    print("Cookie Jar - no cookie-domain")
    print("=" * 70)
    for (name, domain, path), info in sorted(jar_without.items()):
        print(f"  {name:<15} domain={domain:<50} from={info['from']}")

    # --- 覆蓋事件 ---
    print("\n" + "=" * 70)
    print("OVERWRITE EVENTS")
    print("=" * 70)

    if events_with:
        print('\n  cookie-domain="1":')
        for e in events_with:
            print(f"    [{e['step']}] {e['cookie']} @ {e['domain']}")
            print(f"      was from: {e['old_from']}")
            print(f"      now from: {e['new_from']}  <-- OVERWRITTEN!")
    else:
        print('\n  cookie-domain="1": no overwrites')

    if events_without:
        print("\n  no cookie-domain:")
        for e in events_without:
            print(f"    [{e['step']}] {e['cookie']} @ {e['domain']}")
            print(f"      was from: {e['old_from']}")
            print(f"      now from: {e['new_from']}  <-- OVERWRITTEN!")
    else:
        print("\n  no cookie-domain: no overwrites")

    # --- 最終結論 ---
    print("\n" + "=" * 70)
    if events_with and not events_without:
        print("CONCLUSION: cookie-domain=\"1\" causes __cf_bm overwrite!")
        print("            Removing it prevents the conflict.")
    elif events_with and events_without:
        print("CONCLUSION: both modes have overwrites, but cookie-domain=\"1\" is worse.")
    elif not events_with and not events_without:
        print("CONCLUSION: no overwrites detected in either mode.")
    print("=" * 70)


if __name__ == "__main__":
    main()
