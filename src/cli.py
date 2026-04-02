from __future__ import annotations

import asyncio
import json
import sys

import click

from .config.loader import list_sites
from .sites.runner import run_search


@click.group()
def main():
    """Bookstore Scraper CLI"""
    pass


@main.command()
def sites():
    """List available site configs."""
    for s in list_sites():
        click.echo(s)


@main.command()
@click.argument("site")
@click.argument("keyword")
@click.option("--page", "-p", default=1, help="Page number")
@click.option("--pretty", is_flag=True, help="Pretty print JSON output")
def search(site: str, keyword: str, page: int, pretty: bool):
    """Search a site by keyword."""
    result = asyncio.run(run_search(site=site, keyword=keyword, page=page))
    indent = 2 if pretty else None
    output = json.dumps(result.model_dump(), ensure_ascii=False, indent=indent)
    sys.stdout.buffer.write(output.encode("utf-8"))
    sys.stdout.buffer.write(b"\n")


@main.command()
@click.argument("url")
@click.option("--method", "-m", default="GET", help="HTTP method (GET/POST)")
@click.option("--header", "-H", multiple=True, help="Header in 'Key: Value' format")
@click.option("--body", "-b", default="", help="Request body (POST)")
@click.option("--timeout", "-t", default=30, help="Timeout in seconds")
@click.option("--impersonate", default="chrome136", help="Browser TLS fingerprint")
@click.option("--output", "-o", default="", help="Output file path (default: stdout)")
def request(url: str, method: str, header: tuple, body: str, timeout: int, impersonate: str, output: str):
    """Send HTTP request via curl_cffi (HyFSE python driver).

    \b
    HyFSE calls this command, gets back JSON with status_code/headers/body.
    Body is written to a separate file to avoid pipe/stdout size issues.

    \b
    Examples:
        python -m src.cli request "https://example.com"
        python -m src.cli request "https://example.com" -m POST -b "key=value"
        python -m src.cli request "https://example.com" -o response.json
    """
    from .scraper.curl_scraper import CurlScraper
    import tempfile
    import time

    headers = {}
    for h in header:
        if ": " in h:
            k, v = h.split(": ", 1)
            headers[k] = v

    async def do_request():
        t0 = time.perf_counter()
        scraper = CurlScraper(impersonate=impersonate, timeout=timeout)
        try:
            if method.upper() == "GET":
                resp = await scraper.get(url, headers=headers or None)
            elif method.upper() == "POST":
                resp = await scraper.post(url, headers=headers or None, data=body or None)
            else:
                return {"status_code": 0, "headers": {}, "body_file": "", "url": "", "elapsed": 0, "exception": f"Unsupported method: {method}"}
            elapsed = time.perf_counter() - t0

            # Write body to temp file to avoid stdout size issues
            body_file = tempfile.mktemp(suffix=".html", prefix="hyfse_")
            with open(body_file, "w", encoding="utf-8") as f:
                f.write(resp.text)

            return {
                "status_code": resp.status_code,
                "headers": resp.headers,
                "body_file": body_file,
                "body_size": len(resp.text),
                "url": resp.url,
                "elapsed": round(elapsed, 3),
                "exception": "",
            }
        except Exception as e:
            elapsed = time.perf_counter() - t0
            return {"status_code": 0, "headers": {}, "body_file": "", "body_size": 0, "url": "", "elapsed": round(elapsed, 3), "exception": str(e)}
        finally:
            await scraper.close()

    result = asyncio.run(do_request())
    json_output = json.dumps(result, ensure_ascii=False)

    if output:
        with open(output, "w", encoding="utf-8") as f:
            f.write(json_output)
    else:
        sys.stdout.buffer.write(json_output.encode("utf-8"))
        sys.stdout.buffer.write(b"\n")


@main.command()
@click.option("--host", default="0.0.0.0", help="Bind host")
@click.option("--port", default=8000, help="Bind port")
def serve(host: str, port: int):
    """Start the API server."""
    import uvicorn
    from .config.settings import get as cfg
    from .main import app
    host = host or cfg("server.host", "0.0.0.0")
    port = port or cfg("server.port", 8000)
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
