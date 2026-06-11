@echo off
:: Bookstore Scraper (HyPass) - OFFLINE Uninstall Launcher
:: Double-click as Administrator (auto-elevates). Removes service + .venv + logs.
:: For a full wipe incl. Python:  uninstall-offline.ps1 -RemovePython
powershell -Command "Start-Process powershell -ArgumentList '-ExecutionPolicy Bypass -File \"%~dp0uninstall-offline.ps1\"' -Verb RunAs"
