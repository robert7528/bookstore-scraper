#!/usr/bin/env python3
"""
測試 HyProxy cookie-domain rewrite 是否造成 __cf_bm 互相覆蓋。

模擬使用者透過 HyProxy 訪問 JCR auth flow，觀察 __cf_bm cookie 的變化。
在 jumper 主機上直接執行，不經過 HyProxy，只是用 curl 抓 Set-Cookie 分析。

用法：python3 test_cookie_rewrite.py
"""

import subprocess
import re
import json
import sys

DOMAINS = [
    ("jcr.clarivate.com", "/jcr/home"),
    ("access.clarivate.com", "/login?app=jcr&detectSession=true"),
    ("login.incites.clarivate.com", "/"),
]

CONFIG_NAME = "libdb.yuntech.edu.tw"
PROXY_BY = 1  # by hostname
COOKIE_DOMAIN_SETTING = "1"  # 目前 JCR 的設定


def get_cookies(host, path):
    """用 curl 抓 Set-Cookie headers"""
    url = f"https://{host}{path}"
    try:
        result = subprocess.run(
            ["curl", "-k", "-sD", "-", "-o", "/dev/null", url],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30
        )
        result.stdout = result.stdout.decode("utf-8", errors="replace")
        cookies = []
        for line in result.stdout.split("\n"):
            if line.lower().startswith("set-cookie:"):
                cookies.append(line.split(":", 1)[1].strip())
        return cookies
    except Exception as e:
        return [f"ERROR: {e}"]


def parse_cookie(raw):
    """解析 Set-Cookie header"""
    parts = raw.split(";")
    name_value = parts[0].strip()
    name = name_value.split("=")[0].strip()
    attrs = {}
    for part in parts[1:]:
        part = part.strip()
        if "=" in part:
            k, v = part.split("=", 1)
            attrs[k.strip().lower()] = v.strip()
        elif part:
            attrs[part.strip().lower()] = True
    return name, attrs


def simulate_rewrite(cookie_name, attrs, profile_host, cookie_domain_setting):
    """模擬 HyProxy profile.go:636-698 的 cookie domain rewrite"""
    original_domain = attrs.get("domain", "")

    # domain 解析 (profile.go:638-643)
    domain0 = profile_host
    if ":" in domain0:
        domain0 = domain0[:domain0.index(":")]
    domain1 = domain0[domain0.index(".") + 1:] if "." in domain0 else ""
    domain2 = domain1[domain1.index(".") + 1:] if "." in domain1 else ""

    # domainLevel 判斷 (profile.go:644-652)
    domain_level = -1
    od = original_domain.lower()
    if domain0 and domain0 in od:
        domain_level = 0
    elif "." in domain1 and f".{domain1}" in od:
        domain_level = 1
    elif "." in domain2 and f".{domain2}" in od:
        domain_level = 2

    # 新 domain (profile.go:658-674)
    if cookie_domain_setting == "1":
        # cookie-domain: "1"
        new_domain = CONFIG_NAME[CONFIG_NAME.index("."):]
    elif PROXY_BY in (1, 2):
        t0 = profile_host.replace(".", "-").replace(":", "-") + "." + CONFIG_NAME
        t1 = t0[t0.index(".") + 1:]
        t2 = t1[t1.index(".") + 1:] if "." in t1 else ""
        if domain_level == 1:
            new_domain = "." + t1
        elif domain_level == 2:
            new_domain = "." + t2
        else:
            new_domain = t0
    else:
        new_domain = "(unchanged)"

    # SameSite/Secure strip (profile.go:691-698)
    original_samesite = attrs.get("samesite", "(not set)")
    original_secure = "secure" in attrs

    return {
        "cookie_name": cookie_name,
        "profile_host": profile_host,
        "original_domain": original_domain,
        "domain_level": domain_level,
        "new_domain": new_domain,
        "samesite_stripped": original_samesite if original_samesite != "(not set)" else False,
        "secure_stripped": original_secure,
    }


def main():
    print("=" * 70)
    print("HyProxy Cookie Rewrite 測試")
    print(f"config.Name = {CONFIG_NAME}")
    print(f"ProxyBy = {PROXY_BY}")
    print(f"cookie-domain = \"{COOKIE_DOMAIN_SETTING}\"")
    print("=" * 70)

    all_results = []

    for host, path in DOMAINS:
        print(f"\n--- 抓取 {host}{path} ---")
        cookies = get_cookies(host, path)
        if not cookies:
            print("  (沒有 Set-Cookie)")
            continue

        for raw in cookies:
            name, attrs = parse_cookie(raw)
            print(f"  原始: {raw[:120]}...")

            # 模擬 cookie-domain: "1"
            result1 = simulate_rewrite(name, attrs, host, "1")
            # 模擬不設 cookie-domain
            result_no = simulate_rewrite(name, attrs, host, "")

            all_results.append({
                "host": host,
                "cookie": name,
                "original_domain": attrs.get("domain", "(none)"),
                "with_cookie_domain_1": result1["new_domain"],
                "without_cookie_domain": result_no["new_domain"],
                "samesite": attrs.get("samesite", "(not set)"),
                "secure": "secure" in attrs,
            })

    # 結果彙總
    print("\n" + "=" * 70)
    print("結果比較")
    print("=" * 70)
    print(f"{'來源':<40} {'cookie':<12} {'原始domain':<28} {'cookie-domain=1':<25} {'不設cookie-domain'}")
    print("-" * 140)
    for r in all_results:
        print(f"{r['host']:<40} {r['cookie']:<12} {r['original_domain']:<28} {r['with_cookie_domain_1']:<25} {r['without_cookie_domain']}")

    # 衝突檢查
    print("\n" + "=" * 70)
    print("衝突檢查（同名 + 同 domain = 互相覆蓋）")
    print("=" * 70)

    for label, key in [("cookie-domain=\"1\"", "with_cookie_domain_1"), ("不設cookie-domain", "without_cookie_domain")]:
        seen = {}
        conflicts = []
        for r in all_results:
            cookie_key = (r["cookie"], r[key])
            if cookie_key in seen:
                conflicts.append((r["cookie"], r[key], seen[cookie_key], r["host"]))
            else:
                seen[cookie_key] = r["host"]

        if conflicts:
            print(f"\n  [CONFLICT] {label}:")
            for name, domain, host1, host2 in conflicts:
                print(f"    {name} @ {domain} -- {host1} vs {host2} overwrite!")
        else:
            print(f"\n  [OK] {label} no conflict")


if __name__ == "__main__":
    main()
