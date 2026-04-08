"""HTTP forward proxy server — asyncio TCP server.

Supports:
- Plain HTTP proxy: GET http://example.com/path HTTP/1.1 → curl_cffi
- HTTPS via CONNECT: transparent TCP tunnel (like Squid)

CONNECT uses transparent tunneling by default so that HyProxy's own
TLS/cookies/headers reach the origin untouched.  Only plain HTTP requests
go through curl_cffi (for TLS fingerprint impersonation).
"""
from __future__ import annotations

import asyncio
import logging

from ..config.settings import get as cfg
from ..scraper.session_manager import SessionManager
from .handler import handle_proxy_request, build_http_response

logger = logging.getLogger(__name__)


class ProxyServer:
    """HTTP forward proxy with transparent CONNECT tunneling."""

    def __init__(self, host: str, port: int, session_mgr: SessionManager, browser_pool=None):
        self._host = host
        self._port = port
        self._session_mgr = session_mgr
        self._browser_pool = browser_pool
        self._server = None

    async def start(self):
        """Start the proxy TCP server."""
        self._server = await asyncio.start_server(
            self._handle_client, self._host, self._port
        )
        logger.info("Forward proxy listening on %s:%d (CONNECT=transparent tunnel)", self._host, self._port)

    async def stop(self):
        """Stop the proxy server."""
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            logger.info("Forward proxy stopped")

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
        # Read body if present
        body = await self._read_body(headers, reader)

        # Execute request via curl_cffi
        status, resp_headers, resp_body = await handle_proxy_request(
            method, url, headers, body, self._session_mgr, self._browser_pool
        )

        # Build and send response
        response = build_http_response(status, resp_headers, resp_body)
        writer.write(response)
        await writer.drain()

    async def _handle_connect(self, target: str, headers: dict, reader, writer, peer):
        """Handle CONNECT with transparent TCP tunneling.

        Just relay bytes between client and origin — no TLS interception.
        This preserves HyProxy's own TLS handshake, cookies, and headers.
        """
        # Parse target host:port
        if ":" in target:
            host, port_str = target.rsplit(":", 1)
            port = int(port_str)
        else:
            host, port = target, 443

        logger.info("CONNECT %s (transparent tunnel) from %s", target, peer)

        # Connect to origin
        try:
            remote_reader, remote_writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=30
            )
        except Exception as e:
            logger.error("CONNECT failed to %s: %s", target, e)
            writer.write(f"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n".encode())
            await writer.drain()
            return

        # Reply 200 to establish tunnel
        writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
        await writer.drain()

        # Relay bytes bidirectionally
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

        # Wait for either direction to finish, then cancel the other
        done, pending = await asyncio.wait(
            [task_c2r, task_r2c], return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
        # Wait for cancelled tasks to finish
        await asyncio.gather(*pending, return_exceptions=True)

        # Clean up remote connection
        try:
            remote_writer.close()
            await remote_writer.wait_closed()
        except Exception:
            pass

        logger.debug("CONNECT tunnel closed: %s", target)

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
