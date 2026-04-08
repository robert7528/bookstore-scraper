"""HTTP forward proxy server — asyncio TCP server with CONNECT MitM support.

Supports:
- Plain HTTP proxy: GET http://example.com/path HTTP/1.1
- HTTPS via CONNECT + MitM TLS: CONNECT host:443 HTTP/1.1

HyProxy has InsecureSkipVerify=true, so self-signed cert works for MitM.
"""
from __future__ import annotations

import asyncio
import logging
import ssl
from urllib.parse import urlparse

from ..config.settings import get as cfg
from ..scraper.session_manager import SessionManager
from .handler import handle_proxy_request, build_http_response
from .tls import get_ssl_context

logger = logging.getLogger(__name__)


class ProxyServer:
    """HTTP forward proxy with CONNECT MitM TLS support."""

    def __init__(self, host: str, port: int, session_mgr: SessionManager, browser_pool=None):
        self._host = host
        self._port = port
        self._session_mgr = session_mgr
        self._browser_pool = browser_pool
        self._server = None
        self._ssl_context = None

    async def start(self):
        """Start the proxy TCP server."""
        # Pre-load SSL context for CONNECT MitM
        try:
            self._ssl_context = get_ssl_context()
            logger.info("SSL context loaded for CONNECT MitM")
        except Exception as e:
            logger.warning("SSL context failed: %s (CONNECT will be unavailable)", e)

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

        # Execute request
        status, resp_headers, resp_body = await handle_proxy_request(
            method, url, headers, body, self._session_mgr, self._browser_pool
        )

        # Build and send response
        response = build_http_response(status, resp_headers, resp_body)
        writer.write(response)
        await writer.drain()

    async def _handle_connect(self, target: str, headers: dict, reader, writer, peer):
        """Handle CONNECT request with MitM TLS.

        Flow:
        1. Reply 200 Connection established
        2. TLS handshake with client (self-signed cert)
        3. Read decrypted HTTP request from client
        4. Forward via curl_cffi to origin
        5. Return response through TLS tunnel
        """
        if not self._ssl_context:
            writer.write(b"HTTP/1.1 501 CONNECT Not Supported (no SSL cert)\r\n\r\n")
            await writer.drain()
            return

        # Parse target host:port
        if ":" in target:
            host, port = target.rsplit(":", 1)
        else:
            host, port = target, "443"

        logger.debug("CONNECT %s from %s", target, peer)

        # Reply 200 to establish tunnel
        writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
        await writer.drain()

        # Upgrade connection to TLS (MitM)
        try:
            transport = writer.transport
            protocol = transport.get_protocol()

            # Perform TLS handshake as server
            ssl_transport = await asyncio.get_event_loop().start_tls(
                transport, protocol, self._ssl_context, server_side=True
            )

            # Create new reader/writer for the TLS connection
            tls_reader = asyncio.StreamReader()
            tls_protocol = asyncio.StreamReaderProtocol(tls_reader)
            ssl_transport.set_protocol(tls_protocol)
            tls_writer = asyncio.StreamWriter(ssl_transport, tls_protocol, tls_reader, asyncio.get_event_loop())

            # Handle requests inside the TLS tunnel
            await self._handle_tunnel(host, port, tls_reader, tls_writer, peer)

        except ssl.SSLError as e:
            logger.warning("TLS handshake failed for %s: %s", target, e)
        except Exception as e:
            logger.error("CONNECT tunnel error for %s: %s", target, e)

    async def _handle_tunnel(self, host: str, port: str, reader, writer, peer):
        """Handle HTTP requests inside a TLS tunnel (after CONNECT MitM)."""
        try:
            while True:
                # Read request line from decrypted stream
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

                # Read headers
                headers = await self._read_headers(reader)

                # Build full URL (path is relative inside tunnel)
                scheme = "https"
                url = f"{scheme}://{host}{path}"

                # Read body
                body = await self._read_body(headers, reader)

                # Execute request
                status, resp_headers, resp_body = await handle_proxy_request(
                    method, url, headers, body, self._session_mgr, self._browser_pool
                )

                # Send response through tunnel
                response = build_http_response(status, resp_headers, resp_body)
                writer.write(response)
                await writer.drain()

                # Check if connection should close
                conn = headers.get("connection", "").lower()
                if conn == "close":
                    break

        except asyncio.TimeoutError:
            pass
        except Exception as e:
            logger.debug("Tunnel closed for %s: %s", host, e)

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
