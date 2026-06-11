@echo off
:: Bookstore Scraper (HyPass) - OFFLINE Installer Launcher
:: 雙擊本檔以「系統管理員」權限開始離線安裝(會自動提權)。
powershell -Command "Start-Process powershell -ArgumentList '-ExecutionPolicy Bypass -File \"%~dp0install-offline.ps1\"' -Verb RunAs"
