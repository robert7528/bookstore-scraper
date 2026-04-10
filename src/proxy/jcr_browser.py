"""JCR browser session — persistent Chrome for auth + API requests.

Instead of extracting cookies from the browser and injecting into curl_cffi
(which fails due to NAT pool IP mismatch), this module keeps a persistent
Chrome instance alive and routes JCR API requests through it via fetch().

All requests use the same browser process → same outgoing IP → auth works.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from functools import partial

from ..config.settings import get as cfg

logger = logging.getLogger(__name__)

_executor = ThreadPoolExecutor(max_workers=1)

# Browser idle timeout — close Chrome if no requests for this long
_IDLE_TIMEOUT = cfg("browser.idle_timeout", 300)
_MAX_LIFETIME = cfg("browser.max_lifetime", 7200)


class JCRBrowserSession:
    """Persistent browser session for JCR API requests."""

    def __init__(self):
        self._driver = None
        self._lock = asyncio.Lock()
        self._last_used: float = 0
        self._created_at: float = 0
        self._authenticated = False
        self._request_count = 0
        self._idle_task: asyncio.Task | None = None

    @property
    def is_alive(self) -> bool:
        if not self._driver:
            return False
        try:
            _ = self._driver.title
            return True
        except Exception:
            return False

    async def fetch(self, method: str, url: str, headers: dict, body: bytes
                    ) -> tuple[int, dict, bytes]:
        """Execute HTTP request via browser fetch(). Returns (status, headers, body)."""
        async with self._lock:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                _executor,
                partial(self._fetch_sync, method, url, headers, body)
            )
            self._last_used = time.time()
            self._start_idle_watcher()
            return result

    def _fetch_sync(self, method: str, url: str, headers: dict, body: bytes
                    ) -> tuple[int, dict, bytes]:
        """Sync fetch via browser (runs in thread pool)."""
        self._ensure_browser_sync()

        if not self._authenticated:
            self._authenticate_sync()

        self._request_count += 1

        # Build fetch options
        fetch_headers = {}
        for k, v in headers.items():
            kl = k.lower()
            # Skip hop-by-hop and browser-managed headers
            if kl in ("host", "connection", "accept-encoding", "proxy-connection",
                       "proxy-authorization", "te", "trailer", "transfer-encoding",
                       "upgrade", "keep-alive"):
                continue
            fetch_headers[k] = v

        fetch_opts = {
            "method": method.upper(),
            "headers": fetch_headers,
            "credentials": "include",  # Send cookies
        }
        if body and method.upper() in ("POST", "PUT", "PATCH"):
            # Encode body as base64 for safe JS transfer
            import base64
            b64 = base64.b64encode(body).decode("ascii")
            fetch_opts["body"] = f"__BASE64__{b64}"

        opts_json = json.dumps(fetch_opts)

        # Use JavaScript fetch() API in the browser
        # This runs in the browser's context with its cookies and IP
        js_code = """
        var callback = arguments[arguments.length - 1];
        var opts = JSON.parse(arguments[0]);

        // Handle base64 body
        if (opts.body && opts.body.startsWith('__BASE64__')) {
            var b64 = opts.body.substring(10);
            var binary = atob(b64);
            var bytes = new Uint8Array(binary.length);
            for (var i = 0; i < binary.length; i++) {
                bytes[i] = binary.charCodeAt(i);
            }
            opts.body = bytes.buffer;
        }

        fetch(arguments[1], opts)
            .then(function(response) {
                var respHeaders = {};
                response.headers.forEach(function(value, key) {
                    respHeaders[key] = value;
                });
                return response.arrayBuffer().then(function(buf) {
                    var arr = new Uint8Array(buf);
                    var str = '';
                    // Convert to latin1 string for safe transfer
                    for (var i = 0; i < arr.length; i++) {
                        str += String.fromCharCode(arr[i]);
                    }
                    callback({
                        status: response.status,
                        headers: respHeaders,
                        body: btoa(str),
                        error: null
                    });
                });
            })
            .catch(function(err) {
                callback({
                    status: 0,
                    headers: {},
                    body: '',
                    error: err.toString()
                });
            });
        """

        try:
            # execute_async_script waits for the callback
            self._driver.set_script_timeout(30)
            result = self._driver.execute_async_script(js_code, opts_json, url)

            if not result or result.get("error"):
                err = result.get("error", "unknown") if result else "no result"
                logger.error("Browser fetch error for %s: %s", url[:80], err)
                return 502, {}, f"Browser fetch error: {err}".encode()

            status = result["status"]
            resp_headers = result.get("headers", {})
            import base64
            resp_body = base64.b64decode(result.get("body", ""))

            logger.info("Browser fetch %s %s → %d (%d bytes)",
                        method, url[:80], status, len(resp_body))
            return status, resp_headers, resp_body

        except Exception as e:
            logger.error("Browser fetch exception for %s: %s", url[:80], e)
            # If browser is dead, mark for restart
            if "session" in str(e).lower() or "disconnected" in str(e).lower():
                self._driver = None
                self._authenticated = False
            return 502, {}, f"Browser fetch error: {e}".encode()

    def _ensure_browser_sync(self):
        """Start Chrome if not running."""
        if self._driver is not None:
            # Check lifetime
            if _MAX_LIFETIME > 0 and time.time() - self._created_at > _MAX_LIFETIME:
                logger.info("JCR browser max lifetime exceeded, restarting")
                self._close_sync()
            else:
                try:
                    _ = self._driver.title
                    return
                except Exception:
                    logger.warning("JCR browser lost, restarting")
                    self._driver = None
                    self._authenticated = False

        import undetected_chromedriver as uc

        options = uc.ChromeOptions()
        options.add_argument("--no-first-run")
        options.add_argument("--no-default-browser-check")
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
        options.add_argument("--window-size=1280,720")
        options.add_argument("--lang=zh-TW")

        headless = cfg("browser.headless", True)
        if headless:
            options.add_argument("--headless=new")

        import subprocess
        version_main = None
        try:
            result = subprocess.run(
                ["google-chrome", "--version"], capture_output=True, text=True, timeout=5
            )
            version_main = int(result.stdout.strip().split()[-1].split(".")[0])
        except Exception:
            pass

        self._driver = uc.Chrome(options=options, version_main=version_main)
        self._driver.set_page_load_timeout(60)
        self._created_at = time.time()
        self._request_count = 0
        self._authenticated = False
        logger.info("JCR browser started (headless=%s)", headless)

    def _authenticate_sync(self):
        """Navigate to JCR and complete IP-based auth."""
        logger.info("JCR browser: authenticating via jcr.clarivate.com...")
        self._driver.get("https://jcr.clarivate.com/jcr/home")

        # Wait for IC2_SID cookie (IP auth completed)
        deadline = time.time() + 60
        while time.time() < deadline:
            time.sleep(2)
            cookies = {c["name"]: c["value"] for c in self._driver.get_cookies()}
            if "IC2_SID" in cookies:
                logger.info("JCR browser: IP auth completed (IC2_SID obtained)")
                self._authenticated = True
                self._last_used = time.time()
                return

            url = self._driver.current_url
            logger.debug("JCR browser: waiting for auth... %s", url[:60])

        logger.warning("JCR browser: auth timed out after 60s")
        try:
            self._driver.save_screenshot("/tmp/jcr_auth_debug.png")
        except Exception:
            pass

    def _close_sync(self):
        """Close Chrome."""
        if not self._driver:
            return
        pid = None
        try:
            pid = self._driver.browser_pid
        except Exception:
            pass
        try:
            self._driver.quit()
        except Exception:
            pass
        self._driver = None
        self._authenticated = False
        # Cleanup
        import os, signal, subprocess
        if pid:
            try:
                subprocess.run(["pkill", "-9", "-P", str(pid)], capture_output=True, timeout=5)
                os.kill(pid, signal.SIGKILL)
            except (ProcessLookupError, OSError):
                pass
            try:
                os.waitpid(pid, os.WNOHANG)
            except ChildProcessError:
                pass
        try:
            subprocess.run(["pkill", "-9", "-f", "undetected_chromedriver"], capture_output=True, timeout=5)
        except Exception:
            pass
        logger.info("JCR browser closed (requests: %d)", self._request_count)

    async def close(self):
        if self._idle_task and not self._idle_task.done():
            self._idle_task.cancel()
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(_executor, self._close_sync)

    def _start_idle_watcher(self):
        if self._idle_task and not self._idle_task.done():
            return

        async def _watch():
            while True:
                await asyncio.sleep(60)
                if not self._driver:
                    break
                now = time.time()
                if now - self._last_used > _IDLE_TIMEOUT:
                    logger.info("JCR browser idle for %ds, closing", _IDLE_TIMEOUT)
                    await self.close()
                    break
                if _MAX_LIFETIME > 0 and now - self._created_at > _MAX_LIFETIME:
                    logger.info("JCR browser max lifetime reached, closing")
                    await self.close()
                    break

        self._idle_task = asyncio.create_task(_watch())


# Global instance
jcr_browser = JCRBrowserSession()
