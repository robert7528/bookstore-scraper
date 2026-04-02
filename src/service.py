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


# ─── Windows (sc.exe + pythonw) ──────────────────────────────────────────────

def _win_get_pythonw() -> str:
    """Get pythonw.exe path (no console window)."""
    p = Path(_get_python())
    pythonw = p.parent / "pythonw.exe"
    if pythonw.exists():
        return str(pythonw)
    return str(p)


def _win_install():
    app_dir = _get_app_dir()
    python = _get_python()
    # Use NSSM if available, otherwise sc.exe
    nssm = _win_find_nssm()
    if nssm:
        subprocess.run([nssm, "install", SERVICE_NAME, python, "-m", "src.cli", "serve"], check=True)
        subprocess.run([nssm, "set", SERVICE_NAME, "AppDirectory", str(app_dir)], check=True)
        subprocess.run([nssm, "set", SERVICE_NAME, "DisplayName", DISPLAY_NAME], check=True)
        subprocess.run([nssm, "set", SERVICE_NAME, "Description", DESCRIPTION], check=True)

        log_dir = app_dir / "logs"
        log_dir.mkdir(exist_ok=True)
        subprocess.run([nssm, "set", SERVICE_NAME, "AppStdout", str(log_dir / "service.log")], check=True)
        subprocess.run([nssm, "set", SERVICE_NAME, "AppStderr", str(log_dir / "error.log")], check=True)
        subprocess.run([nssm, "set", SERVICE_NAME, "AppRotateFiles", "1"], check=True)
        subprocess.run([nssm, "set", SERVICE_NAME, "AppRotateBytes", "10485760"], check=True)
        print(f"Service installed via NSSM: {SERVICE_NAME}")
    else:
        bin_path = f'"{python}" -m src.cli serve'
        subprocess.run([
            "sc.exe", "create", SERVICE_NAME,
            f"binPath={bin_path}",
            f"DisplayName={DISPLAY_NAME}",
            "start=auto",
        ], check=True)
        print(f"Service installed via sc.exe: {SERVICE_NAME}")
        print("  Note: Install NSSM for better service management (log rotation, restart).")


def _win_uninstall():
    nssm = _win_find_nssm()
    if nssm:
        subprocess.run([nssm, "stop", SERVICE_NAME], check=False)
        subprocess.run([nssm, "remove", SERVICE_NAME, "confirm"], check=True)
    else:
        subprocess.run(["sc.exe", "stop", SERVICE_NAME], check=False)
        subprocess.run(["sc.exe", "delete", SERVICE_NAME], check=True)
    print("Service uninstalled.")


def _win_start():
    nssm = _win_find_nssm()
    if nssm:
        subprocess.run([nssm, "start", SERVICE_NAME], check=True)
    else:
        subprocess.run(["sc.exe", "start", SERVICE_NAME], check=True)
    print("Service started.")


def _win_stop():
    nssm = _win_find_nssm()
    if nssm:
        subprocess.run([nssm, "stop", SERVICE_NAME], check=True)
    else:
        subprocess.run(["sc.exe", "stop", SERVICE_NAME], check=True)
    print("Service stopped.")


def _win_status():
    nssm = _win_find_nssm()
    if nssm:
        subprocess.run([nssm, "status", SERVICE_NAME], check=False)
    else:
        subprocess.run(["sc.exe", "query", SERVICE_NAME], check=False)


def _win_find_nssm() -> str | None:
    """Find NSSM executable."""
    import shutil
    return shutil.which("nssm")


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
