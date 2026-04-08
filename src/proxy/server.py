"""HTTP forward proxy server — asyncio TCP server.

Supports:
- Plain HTTP proxy: GET http://example.com/path HTTP/1.1 → curl_cffi
- HTTPS via CONNECT:
  - CF-protected domains (mitm_domains) → MitM TLS + curl_cffi (bypass CF)
  - Other domains → transparent TCP tunnel (preserves HyProxy's TLS/cookies)
"""
from __future__ import annotations

import asyncio
import logging
import ssl

from ..config.settings import get as cfg
from ..scraper.session_manager import SessionManager
from .handler import handle_proxy_request, build_http_response
from .tls import get_ssl_context

logger = logging.getLogger(__name__)


class ProxyServer:
    """HTTP forward proxy with hybrid CONNECT handling."""

    def __init__(self, host: str, port: int, session_mgr: SessionManager, browser_pool=None):
        self._host = host
        self._port = port
        self._session_mgr = session_mgr
        self._browser_pool = browser_pool
        self._server = None
        self._ssl_context = None
        self._transparent_domains: set[str] = set()

    async def start(self):
        """Start the proxy TCP server."""
        # Load transparent (bypass MitM) domain list
        raw = cfg("proxy.transparent_domains", [])
        if isinstance(raw, list):
            self._transparent_domains = set(raw)
        logger.info("Transparent domains (bypass MitM): %s", self._transparent_domains or "(none — all MitM)")

        # Pre-load SSL context for MitM
        try:
            self._ssl_context = get_ssl_context()
            logger.info("SSL context loaded for CONNECT MitM")
        except Exception as e:
            logger.warning("SSL context failed: %s (all CONNECT will be transparent)", e)

        self._server = await asyncio.start_server(
            self._handle_client, self._host, self._port
        )
        logger.info("Forward proxy listening on %s:%d", self._host, self._port)

    async def stop(self):
        """Stop the proxy server."""
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            logger.info("Forward proxy stopped")

    def _needs_mitm(self, host: str) -> bool:
        """Check if a host needs MitM (curl_cffi) for CF bypass.

        Default is MitM (for CF bypass). Only transparent_domains bypass MitM.
        """
        if not self._ssl_context:
            return False
        return host not in self._transparent_domains

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        """Handle one proxy client connection."""
        peer = writer.get_extra_info("peername")
        try:
            # Read request line
            request_line = await asyncio.wait_for(reader.readline(), timeout=30)
            if not request_line:
                return

            request_line = request_line.decode("utf-8", errors="replace").strip()
            parts = request_line.split(" ", 2)
            if len(parts) < 3:
                writer.write(b"HTTP/1.1 400 Bad Request\r\n\r\n")
                await writer.drain()
                return

            method, target, version = parts

            # Read headers
            headers = await self._read_headers(reader)

            if method.upper() == "CONNECT":
                await self._handle_connect(target, headers, reader, writer, peer)
            else:
                await self._handle_plain(method, target, headers, reader, writer, peer)

        except asyncio.TimeoutError:
            logger.debug("Proxy client timeout: %s", peer)
        except ConnectionResetError:
            logger.debug("Proxy client disconnected: %s", peer)
        except Exception as e:
            logger.error("Proxy error from %s: %s", peer, e)
            try:
                writer.write(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
                await writer.drain()
            except Exception:
                pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def _handle_plain(self, method: str, url: str, headers: dict, reader, writer, peer):
        """Handle plain HTTP proxy request (GET http://host/path HTTP/1.1)."""
        body = await self._read_body(headers, reader)

        status, resp_headers, resp_body = await handle_proxy_request(
            method, url, headers, body, self._session_mgr, self._browser_pool
        )

        response = build_http_response(status, resp_headers, resp_body)
        writer.write(response)
        await writer.drain()

    async def _handle_connect(self, target: str, headers: dict, reader, writer, peer):
        """Route CONNECT to MitM or transparent tunnel based on domain."""
        # Parse target host:port
        if ":" in target:
            host, port_str = target.rsplit(":", 1)
            port = int(port_str)
        else:
            host, port = target, 443

        if self._needs_mitm(host):
            await self._handle_connect_mitm(host, port, target, headers, reader, writer, peer)
        else:
            await self._handle_connect_transparent(host, port, target, reader, writer, peer)

    # ── Transparent tunnel (default) ──────────────────────────────────

    async def _handle_connect_transparent(self, host, port, target, reader, writer, peer):
        """Transparent TCP tunnel — relay bytes like Squid."""
        logger.info("CONNECT %s (transparent) from %s", target, peer)

        try:
            remote_reader, remote_writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=30
            )
        except Exception as e:
            logger.error("CONNECT failed to %s: %s", target, e)
            writer.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
            await writer.drain()
            return

        writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
        await writer.drain()

        await self._relay(reader, writer, remote_reader, remote_writer, target)

    async def _relay(self, client_reader, client_writer, remote_reader, remote_writer, target):
        """Relay bytes between client and remote until either side closes."""

        async def _forward(src, dst, label):
            try:
                while True:
                    data = await src.read(65536)
                    if not data:
                        break
                    dst.write(data)
                    await dst.drain()
            except (ConnectionResetError, BrokenPipeError, asyncio.CancelledError):
                pass
            except Exception as e:
                logger.debug("Relay %s error for %s: %s", label, target, e)
            finally:
                try:
                    dst.close()
                except Exception:
                    pass

        task_c2r = asyncio.create_task(_forward(client_reader, remote_writer, "client→remote"))
        task_r2c = asyncio.create_task(_forward(remote_reader, client_writer, "remote→client"))

        done, pending = await asyncio.wait(
            [task_c2r, task_r2c], return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)

        try:
            remote_writer.close()
            await remote_writer.wait_closed()
        except Exception:
            pass

        logger.debug("CONNECT tunnel closed: %s", target)

    # ── MitM tunnel (CF-protected domains) ────────────────────────────

    async def _handle_connect_mitm(self, host, port, target, headers, reader, writer, peer):
        """MitM TLS — decrypt, re-request via curl_cffi, return response.

        Used for CF-protected domains where TLS fingerprint matters.
        """
        if not self._ssl_context:
            logger.warning("MitM requested for %s but no SSL context, falling back to transparent", target)
            await self._handle_connect_transparent(host, port, target, reader, writer, peer)
            return

        logger.info("CONNECT %s (MitM curl_cffi) from %s", target, peer)

        # Reply 200 to establish tunnel
        writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
        await writer.drain()

        # Upgrade to TLS (MitM)
        try:
            transport = writer.transport
            protocol = transport.get_protocol()

            ssl_transport = await asyncio.get_event_loop().start_tls(
                transport, protocol, self._ssl_context, server_side=True
            )

            tls_reader = asyncio.StreamReader()
            tls_protocol = asyncio.StreamReaderProtocol(tls_reader)
            ssl_transport.set_protocol(tls_protocol)
            tls_writer = asyncio.StreamWriter(ssl_transport, tls_protocol, tls_reader, asyncio.get_event_loop())

            await self._handle_mitm_tunnel(host, port, tls_reader, tls_writer, peer)

        except ssl.SSLError as e:
            logger.warning("TLS handshake failed for %s: %s", target, e)
        except Exception as e:
            logger.error("CONNECT MitM error for %s: %s", target, e)

    async def _handle_mitm_tunnel(self, host: str, port: str, reader, writer, peer):
        """Handle HTTP requests inside a MitM TLS tunnel."""
        try:
            while True:
                request_line = await asyncio.wait_for(reader.readline(), timeout=60)
                if not request_line:
                    break

                request_line = request_line.decode("utf-8", errors="replace").strip()
                if not request_line:
                    break

                parts = request_line.split(" ", 2)
                if len(parts) < 3:
                    break

                method, path, version = parts
                headers = await self._read_headers(reader)

                # Build full URL
                url = f"https://{host}{path}"

                body = await self._read_body(headers, reader)

                status, resp_headers, resp_body = await handle_proxy_request(
                    method, url, headers, body, self._session_mgr, self._browser_pool
                )

                response = build_http_response(status, resp_headers, resp_body)
                writer.write(response)
                await writer.drain()

                conn = headers.get("connection", "").lower()
                if conn == "close":
                    break

        except asyncio.TimeoutError:
            pass
        except Exception as e:
            logger.debug("MitM tunnel closed for %s: %s", host, e)

    # ── Shared helpers ────────────────────────────────────────────────

    async def _read_headers(self, reader) -> dict[str, str]:
        """Read HTTP headers until blank line."""
        headers = {}
        while True:
            line = await asyncio.wait_for(reader.readline(), timeout=30)
            line = line.decode("utf-8", errors="replace").strip()
            if not line:
                break
            if ":" in line:
                key, value = line.split(":", 1)
                headers[key.strip().lower()] = value.strip()
        return headers

    async def _read_body(self, headers: dict, reader) -> bytes:
        """Read request body based on Content-Length."""
        content_length = int(headers.get("content-length", "0"))
        if content_length > 0:
            max_size = cfg("proxy.max_body_size", 10 * 1024 * 1024)
            if content_length > max_size:
                raise ValueError(f"Request body too large: {content_length}")
            return await asyncio.wait_for(reader.readexactly(content_length), timeout=60)
        return b""
