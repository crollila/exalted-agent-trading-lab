param(
    [switch]$Clean
)

$ErrorActionPreference = "Stop"

if (-not (Get-Command pyinstaller -ErrorAction SilentlyContinue)) {
    Write-Host "PyInstaller is not installed. Optional install:"
    Write-Host "  pip install pyinstaller pywebview"
    exit 1
}

if ($Clean) {
    Remove-Item -LiteralPath "build" -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath "dist" -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath "launch_desktop_app.spec" -Force -ErrorAction SilentlyContinue
}

pyinstaller `
    --onefile `
    --name launch_desktop_app `
    scripts/launch_desktop_app.py

Write-Host ""
Write-Host "Built launcher under dist/. This is only a dashboard launcher."
Write-Host "If pywebview is installed it opens a desktop window; otherwise it falls back to a browser."
Write-Host "It does not package secrets and it is not the trading engine."
