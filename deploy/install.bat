@echo off
:: Bookstore Scraper - Windows Installer Launcher
:: Double-click this file to start installation with Administrator privileges.
powershell -Command "Start-Process powershell -ArgumentList '-ExecutionPolicy Bypass -File \"%~dp0deploy-windows.ps1\"' -Verb RunAs"
