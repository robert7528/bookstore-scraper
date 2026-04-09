#!/usr/bin/env python3
"""
分析 JCR 所有 profile 的 cookie 累積情況。
模擬使用者瀏覽 JCR 相關網站時，cookie-domain="1" 會累積多少 cookie 到 .yuntech.edu.tw。

用法：.venv/bin/python3 tools/test_cookie_accumulation.py
"""

import subprocess
import re
import sys
import time

CONFIG_NAME = "libdb.yuntech.edu.tw"

# JCR 設定檔裡的所有 https profile（實際上瀏覽 JCR 會碰到的域名）
JCR_PROFILES = [
    # 核心
    ("jcr.clarivate.com", "/jcr/home"),
    ("access.clarivate.com", "/login?app=jcr&detectSession=true"),
    ("login.incites.clarivate.com", "/"),
    ("error.incites.clarivate.com", "/"),
    ("clarivate.com", "/"),
    # WOS 相關
    ("www.webofscience.com", "/"),
    ("webofscience.com", "/"),
    ("www.webofknowledge.com", "/"),
    ("webofknowledge.com", "/"),
    # 其他 Clarivate
    ("apps.clarivate.com", "/"),
    ("incites.clarivate.com", "/"),
    ("incites.thomsonreuters.com", "/"),
    ("jcr.incites.thomsonreuters.com", "/"),
    # Analytics/tracking
    ("snowplow.apps.clarivate.com", "/"),
    ("snowplow-collector.userintel.prod.sp.aws.clarivate.net", "/"),
    ("snowplow-collector.staging.userintel.dev.sp.aws.clarivate.net", "/"),
    # Third party
    ("cdn.cookielaw.org", "/"),
    ("privacyportal.onetrust.com", "/"),
    ("publons.com", "/"),
    ("kopernio.com", "/"),
]


def curl_cookies(host, path, timeout=10):
    """用 curl 抓 Set-Cookie headers"""
    url = "https://{}{}".format(host, path)
    try:
        result = subprocess.run(
            ["curl", "-k", "-s", "-D", "-", "-o", "/dev/null",
             "--max-time", str(timeout), url],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout + 5
        )
        stdout = result.stdout.decode("utf-8", errors="replace")
        cookies = []
        for line in stdout.split("\n"):
            if line.lower().strip().startswith("set-cookie:"):
                cookies.append(line.split(":", 1)[1].strip())
        return cookies
    except Exception as e:
        return []


def parse_cookie(raw):
    """解析 Set-Cookie"""
    parts = raw.split(";")
    nv = parts[0].strip()
    eq = nv.find("=")
    name = nv[:eq].strip() if eq >= 0 else nv.strip()
    value = nv[eq+1:].strip() if eq >= 0 else ""
    domain = ""
    path = "/"
    for p in parts[1:]:
        p = p.strip()
        pl = p.lower()
        if pl.startswith("domain="):
            domain = p.split("=", 1)[1].strip()
        elif pl.startswith("path="):
            path = p.split("=", 1)[1].strip()
    return name, value, domain, path


def cookie_domain_rewrite(original_domain, profile_host):
    """模擬 cookie-domain="1" 的 rewrite"""
    dot = CONFIG_NAME.index(".")
    return CONFIG_NAME[dot:]  # .yuntech.edu.tw


def estimate_cookie_header_size(cookies):
    """估算 Cookie request header 大小"""
    # Cookie header 格式: Cookie: name1=value1; name2=value2; ...
    parts = []
    for c in cookies:
        parts.append("{}={}".format(c["name"], c["value"]))
    header = "Cookie: " + "; ".join(parts)
    return len(header)


def main():
    print("=" * 70)
    print("JCR Cookie Accumulation Analysis")
    print("cookie-domain=\"1\" -> all cookies go to {}".format(
        CONFIG_NAME[CONFIG_NAME.index("."):]))
    print("=" * 70)

    all_cookies = {}  # (name, domain, path) -> {info}
    all_cookies_list = []
    cf_cookies = []
    non_cf_cookies = []

    tested = 0
    for host, path in JCR_PROFILES:
        sys.stdout.write("\r  Testing {}/{}...  {}".format(
            tested + 1, len(JCR_PROFILES), host[:50]))
        sys.stdout.flush()
        raw_cookies = curl_cookies(host, path)
        tested += 1

        for raw in raw_cookies:
            name, value, orig_domain, path = parse_cookie(raw)
            new_domain = cookie_domain_rewrite(orig_domain, host)
            is_cf = name.lower().startswith("__cf") or name.lower() == "cf_clearance"

            key = (name, new_domain, path)
            info = {
                "name": name,
                "value": value,
                "orig_domain": orig_domain,
                "new_domain": new_domain,
                "from_host": host,
                "is_cf": is_cf,
                "value_len": len(value),
                "raw_len": len(raw),
            }

            if key in all_cookies:
                # 同名同 domain → 覆蓋
                old = all_cookies[key]
                if old["from_host"] != host:
                    info["overwrites"] = old["from_host"]
            all_cookies[key] = info
            all_cookies_list.append(info)

            if is_cf:
                cf_cookies.append(info)
            else:
                non_cf_cookies.append(info)

    print("\r" + " " * 70)

    # --- 結果 ---
    print("\n" + "=" * 70)
    print("All cookies found (before dedup)")
    print("=" * 70)
    print("  Total: {} cookies from {} domains".format(
        len(all_cookies_list), tested))
    print("  CF cookies (__cf_bm etc): {}".format(len(cf_cookies)))
    print("  Non-CF cookies: {}".format(len(non_cf_cookies)))

    print("\n" + "=" * 70)
    print("Unique cookies on {} (after overwrite)".format(
        CONFIG_NAME[CONFIG_NAME.index("."):]))
    print("=" * 70)

    final_cookies = sorted(all_cookies.values(), key=lambda x: x["name"])
    for c in final_cookies:
        overwrite_info = ""
        if "overwrites" in c:
            overwrite_info = "  [OVERWROTE from {}]".format(c["overwrites"])
        print("  {:<30} {:>6} bytes  from: {:<40}{}".format(
            c["name"], c["value_len"], c["from_host"], overwrite_info))

    # --- Cookie header 大小估算 ---
    header_size = estimate_cookie_header_size(final_cookies)

    print("\n" + "=" * 70)
    print("Cookie Header Size Estimation")
    print("=" * 70)
    print("  Unique cookies: {}".format(len(final_cookies)))
    print("  Estimated Cookie header size: {} bytes ({:.1f} KB)".format(
        header_size, header_size / 1024))
    print("")
    if header_size > 8192:
        print("  [DANGER] Exceeds 8KB! Many servers reject headers > 8KB")
    elif header_size > 4096:
        print("  [WARNING] Exceeds 4KB, may cause issues with some servers")
    else:
        print("  [OK] Under 4KB")

    # --- CF cookie 過濾後 ---
    non_cf_final = [c for c in final_cookies if not c["is_cf"]]
    header_size_no_cf = estimate_cookie_header_size(non_cf_final)
    print("\n  After CF cookie filtering (our fix):")
    print("  Cookies: {} -> {}".format(len(final_cookies), len(non_cf_final)))
    print("  Header size: {} bytes ({:.1f} KB) -> {} bytes ({:.1f} KB)".format(
        header_size, header_size / 1024,
        header_size_no_cf, header_size_no_cf / 1024))
    print("  Saved: {} bytes ({:.1f} KB)".format(
        header_size - header_size_no_cf,
        (header_size - header_size_no_cf) / 1024))

    # --- 不用 cookie-domain="1" 的情況 ---
    print("\n" + "=" * 70)
    print("Without cookie-domain=\"1\" (per-subdomain)")
    print("=" * 70)
    print("  Each site's cookies stay on its own subdomain")
    print("  Browser only sends cookies matching the current subdomain")
    print("  -> No cross-site accumulation")
    print("  -> Cookie header stays small")

    # --- 覆蓋列表 ---
    overwrites = [c for c in final_cookies if "overwrites" in c]
    if overwrites:
        print("\n" + "=" * 70)
        print("Cookie Overwrites (same name+domain, different source)")
        print("=" * 70)
        for c in overwrites:
            print("  {} : {} overwrote by {}".format(
                c["name"], c["overwrites"], c["from_host"]))


if __name__ == "__main__":
    main()
