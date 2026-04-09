#!/usr/bin/env python3
"""
Mini proxy 模擬 HyProxy cookie-domain rewrite，供瀏覽器實測。

用法:
  # 模式1: 模擬 cookie-domain="1" (會衝突)
  python3 tools/test_proxy_cookie.py --cookie-domain=1 --port=8103

  # 模式2: 不做 cookie-domain rewrite (正常)
  python3 tools/test_proxy_cookie.py --port=8103

瀏覽器設定 HTTP Proxy: jumper_ip:8103，然後開 https://jcr.clarivate.com/jcr/home
"""

import asyncio
import logging
import re
import ssl
import sys
import os

# 加入 src 路徑
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from curl_cffi.requests import AsyncSession


def ensure_cert(cert_file, key_file):
    """自動產生自簽憑證"""
    if os.path.exists(cert_file) and os.path.exists(key_file):
        return
    import subprocess
    subprocess.run([
        "openssl", "req", "-x509", "-newkey", "rsa:2048",
        "-keyout", key_file, "-out", cert_file,
        "-days", "365", "-nodes",
        "-subj", "/CN=test-proxy"
    ], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("test-proxy")

# HyProxy 設定模擬
CONFIG_NAME = "libdb.yuntech.edu.tw"
COOKIE_DOMAIN_MODE = ""  # "" or "1"


def rewrite_set_cookie(cookie_str, profile_host):
    """模擬 HyProxy profile.go:636-698 的 Set-Cookie rewrite"""

    # 解析原始 domain
    domain_match = re.search(r"(?i)\bdomain=\.?([^;\s]+)", cookie_str)
    original_domain = domain_match.group(1) if domain_match else ""

    if not original_domain:
        return cookie_str  # 沒有 domain 屬性，不改

    # 計算 domainLevel (profile.go:638-652)
    domain0 = profile_host
    dot1 = domain0.find(".")
    domain1 = domain0[dot1 + 1:] if dot1 >= 0 else ""
    dot2 = domain1.find(".")
    domain2 = domain1[dot2 + 1:] if dot2 >= 0 else ""

    od = original_domain.lower()
    domain_level = -1
    if domain0 and domain0 in od:
        domain_level = 0
    elif "." in domain1 and domain1 in od:
        domain_level = 1
    elif "." in domain2 and domain2 in od:
        domain_level = 2

    # 移除原始 domain
    cookie_str = re.sub(r"(?i)\s?;?\s?domain=[^;\s]+;?\s?", "; ", cookie_str)

    # 加新 domain
    if COOKIE_DOMAIN_MODE == "1":
        new_domain = CONFIG_NAME[CONFIG_NAME.index("."):]
    else:
        t0 = profile_host.replace(".", "-").replace(":", "-") + "." + CONFIG_NAME
        dot1 = t0.index(".")
        t1 = t0[dot1 + 1:]
        dot2 = t1.find(".")
        t2 = t1[dot2 + 1:] if dot2 >= 0 else ""
        if domain_level == 1:
            new_domain = "." + t1
        elif domain_level == 2:
            new_domain = "." + t2
        else:
            new_domain = t0

    cookie_str += "; domain=" + new_domain

    # 模擬 HyProxy strip SameSite + Secure (profile.go:691-698)
    cookie_str = re.sub(r"(?i);\s?samesite\s?=\s?(none|strict|lax)\s?;?", ";", cookie_str)
    if not cookie_str.startswith("__Host-"):
        cookie_str = re.sub(r"(?i);\s?secure\s?;?", ";", cookie_str)

    return cookie_str


async def handle_connect(reader, writer, ssl_ctx):
    """處理 CONNECT 請求 (MitM)"""
    # 讀取 CONNECT 請求
    request_line = await reader.readline()
    request_str = request_line.decode("utf-8", errors="replace").strip()

    if not request_str.startswith("CONNECT"):
        writer.close()
        return

    target = request_str.split()[1]
    host = target.split(":")[0]
    port = int(target.split(":")[1]) if ":" in target else 443

    # 讀取剩餘 headers
    while True:
        line = await reader.readline()
        if line == b"\r\n" or line == b"\n" or not line:
            break

    # 回 200 Connection established
    writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
    await writer.drain()

    # TLS handshake (MitM)
    try:
        tls_reader, tls_writer = await asyncio.open_connection(
            ssl=ssl_ctx, sock=writer.transport.get_extra_info("socket"),
            server_side=True, server_hostname=None
        )
    except Exception:
        # asyncio SSL workaround: wrap existing transport
        loop = asyncio.get_event_loop()
        transport = writer.transport
        protocol = asyncio.StreamReaderProtocol(asyncio.StreamReader())
        new_transport = await loop.start_tls(
            transport, protocol, ssl_ctx, server_side=True
        )
        tls_reader = protocol._stream_reader
        tls_writer = asyncio.StreamWriter(new_transport, protocol, tls_reader, loop)

    try:
        await handle_https_request(tls_reader, tls_writer, host)
    except Exception as e:
        logger.debug("Connection error: %s", e)
    finally:
        try:
            tls_writer.close()
        except Exception:
            pass


async def handle_https_request(reader, writer, default_host):
    """處理 MitM 後的 HTTP 請求"""
    while True:
        # 讀取 request
        request_line = await reader.readline()
        if not request_line:
            break
        request_str = request_line.decode("utf-8", errors="replace").strip()
        if not request_str:
            break

        parts = request_str.split()
        if len(parts) < 3:
            break
        method = parts[0]
        path = parts[1]

        # 讀取 headers
        headers = {}
        while True:
            line = await reader.readline()
            if line == b"\r\n" or line == b"\n" or not line:
                break
            decoded = line.decode("utf-8", errors="replace").strip()
            if ":" in decoded:
                k, v = decoded.split(":", 1)
                headers[k.strip()] = v.strip()

        host = headers.get("Host", headers.get("host", default_host))
        url = "https://" + host + path

        # 讀取 body
        content_length = int(headers.get("Content-Length", headers.get("content-length", "0")))
        body = b""
        if content_length > 0:
            body = await reader.readexactly(content_length)

        # 用 curl_cffi 發請求
        try:
            async with AsyncSession(impersonate="chrome") as session:
                clean = {k: v for k, v in headers.items()
                         if k.lower() not in ("host", "connection", "accept-encoding",
                                               "proxy-connection", "proxy-authorization")}
                kwargs = {"headers": clean, "allow_redirects": False}
                if method == "GET":
                    r = await session.get(url, **kwargs)
                elif method == "POST":
                    r = await session.post(url, data=body or None, **kwargs)
                elif method == "HEAD":
                    r = await session.head(url, **kwargs)
                elif method == "PUT":
                    r = await session.put(url, data=body or None, **kwargs)
                elif method == "DELETE":
                    r = await session.delete(url, **kwargs)
                elif method == "OPTIONS":
                    r = await session.options(url, **kwargs)
                else:
                    r = await session.get(url, **kwargs)
        except Exception as e:
            logger.error("Request failed: %s %s -> %s", method, url[:80], e)
            error_resp = "HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n"
            writer.write(error_resp.encode())
            await writer.drain()
            continue

        # 構建 response
        status = r.status_code
        resp_body = r.content

        # 處理 headers（保留多值 Set-Cookie）
        resp_lines = ["HTTP/1.1 {} OK".format(status)]
        for k, v in r.headers.multi_items():
            kl = k.lower()
            # 跳過 hop-by-hop 和會壞的 headers
            if kl in ("connection", "transfer-encoding", "content-encoding",
                       "content-length", "keep-alive"):
                continue
            if kl == "set-cookie":
                original = v
                rewritten = rewrite_set_cookie(v, host)
                if original != rewritten:
                    logger.info("COOKIE REWRITE [%s]: %s", host, v[:60])
                    logger.info("  -> %s", rewritten[:80])
                v = rewritten
            resp_lines.append("{}: {}".format(k, v))

        resp_lines.append("Content-Length: {}".format(len(resp_body)))
        resp_header = "\r\n".join(resp_lines) + "\r\n\r\n"

        writer.write(resp_header.encode("utf-8") + resp_body)
        await writer.drain()

        log_msg = "PROXY %s %s -> %d (%d bytes)" % (method, url[:80], status, len(resp_body))
        if status >= 400:
            logger.warning(log_msg)
        else:
            logger.info(log_msg)

        # HTTP/1.0 不支援 keep-alive
        if headers.get("Connection", "").lower() == "close":
            break


async def handle_client(reader, writer, ssl_ctx):
    try:
        # 偷看第一個 byte 判斷是 CONNECT 還是普通 HTTP
        first_line = await reader.readline()
        if not first_line:
            writer.close()
            return

        request_str = first_line.decode("utf-8", errors="replace").strip()

        if request_str.startswith("CONNECT"):
            # 把已讀的放回去... 其實 CONNECT 的 request line 已經讀了
            target = request_str.split()[1]
            host = target.split(":")[0]

            # 讀取剩餘 headers
            while True:
                line = await reader.readline()
                if line == b"\r\n" or line == b"\n" or not line:
                    break

            logger.info("CONNECT %s (MitM)", target)

            # 回 200
            writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
            await writer.drain()

            # TLS upgrade
            loop = asyncio.get_event_loop()
            transport = writer.transport
            protocol = asyncio.StreamReaderProtocol(asyncio.StreamReader())
            try:
                new_transport = await loop.start_tls(
                    transport, protocol, ssl_ctx, server_side=True
                )
            except Exception as e:
                logger.error("TLS handshake failed for %s: %s", host, e)
                return

            tls_reader = protocol._stream_reader
            tls_writer = asyncio.StreamWriter(new_transport, protocol, tls_reader, loop)

            await handle_https_request(tls_reader, tls_writer, host)
        else:
            # 普通 HTTP proxy 請求
            logger.info("HTTP %s (not supported, use CONNECT)", request_str[:60])
            writer.write(b"HTTP/1.1 405 Method Not Allowed\r\nContent-Length: 0\r\n\r\n")
            await writer.drain()
    except Exception as e:
        logger.debug("Client error: %s", e)
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def main():
    global COOKIE_DOMAIN_MODE

    import argparse
    parser = argparse.ArgumentParser(description="Test proxy for cookie-domain rewrite")
    parser.add_argument("--port", type=int, default=8103)
    parser.add_argument("--cookie-domain", default="", help='"1" to simulate HyProxy cookie-domain=1')
    args = parser.parse_args()

    COOKIE_DOMAIN_MODE = args.cookie_domain

    # 確保自簽憑證存在
    cert_file = os.path.join(os.path.dirname(__file__), "..", "configs", "proxy-cert.pem")
    key_file = os.path.join(os.path.dirname(__file__), "..", "configs", "proxy-key.pem")
    cert_file = os.path.abspath(cert_file)
    key_file = os.path.abspath(key_file)

    if not os.path.exists(cert_file):
        ensure_cert(cert_file, key_file)

    ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ssl_ctx.load_cert_chain(cert_file, key_file)
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE

    server = await asyncio.start_server(
        lambda r, w: handle_client(r, w, ssl_ctx),
        "0.0.0.0", args.port
    )

    mode_desc = 'cookie-domain="1" (CONFLICT mode)' if COOKIE_DOMAIN_MODE == "1" else "no cookie-domain (NORMAL mode)"
    logger.info("=" * 60)
    logger.info("Test proxy started on port %d", args.port)
    logger.info("Mode: %s", mode_desc)
    logger.info("Browser proxy: http://<host>:%d", args.port)
    logger.info("Then open: https://jcr.clarivate.com/jcr/home")
    logger.info("=" * 60)

    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
