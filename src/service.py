"""Cross-platform service management for bookstore-scraper.

Provides install/uninstall/start/stop/status commands that work on both
Linux (systemd) and Windows (Windows Service via pywin32).

Usage:
    python -m src.cli service install
    python -m src.cli service start
    python -m src.cli service stop
    python -m src.cli service status
    python -m src.cli service uninstall
"""
from __future__ import annotations

import os
import platform
import subprocess
import sys
from pathlib import Path

SERVICE_NAME = "bookstore-scraper"
DISPLAY_NAME = "Bookstore Scraper API"
DESCRIPTION = "HyFSE Python Driver - TLS fingerprint proxy service"


def _get_python() -> str:
    """Get the Python executable path."""
    return sys.executable


def _get_app_dir() -> Path:
    """Get the project root directory."""
    return Path(__file__).resolve().parent.parent


def _is_admin() -> bool:
    """Check if running with admin/root privileges."""
    if platform.system() == "Windows":
        import ctypes
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    return os.geteuid() == 0


# ─── Linux (systemd) ────────────────────────────────────────────────────────

SYSTEMD_UNIT_TEMPLATE = """\
[Unit]
Description={display_name}
After=network.target

[Service]
Type=simple
WorkingDirectory={app_dir}
ExecStart={exec_start}
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
"""

SYSTEMD_PATH = f"/etc/systemd/system/{SERVICE_NAME}.service"


def _build_exec_start(python: str) -> str:
    """Build ExecStart command based on browser.headless setting in YAML."""
    import shutil
    try:
        from .config.settings import get as cfg
        headless = cfg("browser.headless", True)
    except Exception:
        headless = True

    base_cmd = f"{python} -m src.cli serve"
    if not headless and shutil.which("xvfb-run"):
        return f'/usr/bin/xvfb-run --auto-servernum --server-args="-screen 0 1920x1080x24" {base_cmd}'
    return base_cmd


def _linux_install():
    app_dir = _get_app_dir()
    python = _get_python()
    exec_start = _build_exec_start(python)
    unit = SYSTEMD_UNIT_TEMPLATE.format(
        display_name=DISPLAY_NAME, app_dir=app_dir, exec_start=exec_start,
    )
    with open(SYSTEMD_PATH, "w") as f:
        f.write(unit)
    subprocess.run(["systemctl", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "enable", SERVICE_NAME], check=True)
    print(f"Service installed: {SYSTEMD_PATH}")
    if "xvfb-run" in exec_start:
        print("  Xvfb enabled (browser.headless=false)")
    else:
        print("  Xvfb disabled (browser.headless=true)")


def _linux_uninstall():
    subprocess.run(["systemctl", "stop", SERVICE_NAME], check=False)
    subprocess.run(["systemctl", "disable", SERVICE_NAME], check=False)
    if os.path.exists(SYSTEMD_PATH):
        os.remove(SYSTEMD_PATH)
    subprocess.run(["systemctl", "daemon-reload"], check=True)
    print("Service uninstalled.")


def _linux_start():
    subprocess.run(["systemctl", "start", SERVICE_NAME], check=True)
    print("Service started.")


def _linux_stop():
    subprocess.run(["systemctl", "stop", SERVICE_NAME], check=True)
    print("Service stopped.")


def _linux_status():
    subprocess.run(["systemctl", "status", SERVICE_NAME, "--no-pager"], check=False)


# ─── Windows (WinSW) ────────────────────────────────────────────────────────

WINSW_URL = "https://github.com/winsw/winsw/releases/download/v3.0.0-alpha.11/WinSW-net461.exe"


def _win_get_winsw() -> Path:
    """Get or download WinSW executable."""
    app_dir = _get_app_dir()
    winsw = app_dir / "deploy" / f"{SERVICE_NAME}.exe"
    if winsw.exists():
        return winsw

    # Try to download WinSW
    src = app_dir / "deploy" / "WinSW.exe"
    if src.exists():
        import shutil
        shutil.copy2(src, winsw)
        return winsw

    print(f"Downloading WinSW...")
    try:
        import urllib.request
        urllib.request.urlretrieve(WINSW_URL, str(winsw))
        print(f"  Downloaded: {winsw}")
    except Exception as e:
        raise FileNotFoundError(
            f"WinSW not found. Please download from {WINSW_URL} "
            f"and save as {winsw}"
        ) from e
    return winsw


def _win_ensure_xml():
    """Ensure WinSW XML config exists next to the exe."""
    app_dir = _get_app_dir()
    xml_src = app_dir / "deploy" / "bookstore-scraper.xml"
    xml_dst = app_dir / "deploy" / f"{SERVICE_NAME}.xml"
    if xml_src.exists() and not xml_dst.exists():
        import shutil
        shutil.copy2(xml_src, xml_dst)

    # Create logs directory
    (app_dir / "logs").mkdir(exist_ok=True)


def _win_install():
    winsw = _win_get_winsw()
    _win_ensure_xml()
    subprocess.run([str(winsw), "install"], check=True, cwd=str(winsw.parent))
    print(f"Service installed via WinSW: {SERVICE_NAME}")


def _win_uninstall():
    winsw = _win_get_winsw()
    subprocess.run([str(winsw), "stop"], check=False, cwd=str(winsw.parent))
    subprocess.run([str(winsw), "uninstall"], check=True, cwd=str(winsw.parent))
    print("Service uninstalled.")


def _win_start():
    winsw = _win_get_winsw()
    subprocess.run([str(winsw), "start"], check=True, cwd=str(winsw.parent))
    print("Service started.")


def _win_stop():
    winsw = _win_get_winsw()
    subprocess.run([str(winsw), "stop"], check=True, cwd=str(winsw.parent))
    print("Service stopped.")


def _win_status():
    winsw = _win_get_winsw()
    subprocess.run([str(winsw), "status"], check=False, cwd=str(winsw.parent))


# ─── Dispatcher ──────────────────────────────────────────────────────────────

COMMANDS = {
    "Linux": {
        "install": _linux_install,
        "uninstall": _linux_uninstall,
        "start": _linux_start,
        "stop": _linux_stop,
        "status": _linux_status,
    },
    "Windows": {
        "install": _win_install,
        "uninstall": _win_uninstall,
        "start": _win_start,
        "stop": _win_stop,
        "status": _win_status,
    },
}


def run_service_command(action: str):
    system = platform.system()
    commands = COMMANDS.get(system)
    if not commands:
        print(f"Unsupported platform: {system}")
        sys.exit(1)

    if action not in commands:
        print(f"Unknown action: {action}")
        print(f"Available: {', '.join(commands.keys())}")
        sys.exit(1)

    if action in ("install", "uninstall", "start", "stop") and not _is_admin():
        if system == "Windows":
            print("ERROR: Please run as Administrator.")
        else:
            print("ERROR: Please run with sudo.")
        sys.exit(1)

    commands[action]()
