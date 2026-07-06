$ErrorActionPreference = "Stop"

Write-Host "Installing Python dependencies..." -ForegroundColor Cyan
& .\.venv\Scripts\pip.exe install -r requirements.txt

Write-Host "Installing Playwright Chromium browser..." -ForegroundColor Cyan
& .\.venv\Scripts\python.exe -m playwright install chromium

Write-Host "Setup complete." -ForegroundColor Green
