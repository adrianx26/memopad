# Check if uv is installed
if (-not (Get-Command "uv" -ErrorAction SilentlyContinue)) {
    Write-Host "Error: uv is not installed. Please install uv first (https://github.com/astral-sh/uv)." -ForegroundColor Red
    exit 1
}

# Install memopad globally using uv
Write-Host "Installing memopad globally..." -ForegroundColor Green
uv tool install . --force

if ($LASTEXITCODE -ne 0) {
    Write-Host "Error: Failed to install memopad." -ForegroundColor Red
    exit 1
}

# Find memopad executable
$memopadPath = (Get-Command "memopad" -ErrorAction SilentlyContinue).Source

if (-not $memopadPath) {
    # Fallback to check default uv tool location if not in PATH yet
    $uvBinPath = Join-Path $env:USERPROFILE ".local\bin\memopad.exe"
    if (Test-Path $uvBinPath) {
        $memopadPath = $uvBinPath
    } else {
        Write-Host "Error: Could not find memopad executable." -ForegroundColor Red
        exit 1
    }
}

Write-Host "Found memopad at: $memopadPath" -ForegroundColor Cyan

# Verify the install actually works. A "successful" `uv tool install` is not a
# working install: a broken package (e.g. an unimported name in a router, which
# only errors at runtime on the installed Python) would still report success.
# `memopad --version` exercises the CLI import chain — if it prints a version
# instead of a traceback, the package is usable.
Write-Host "Verifying install..." -ForegroundColor Green
$versionOutput = & $memopadPath --version 2>&1
if ($LASTEXITCODE -ne 0 -or ($versionOutput -join "`n") -match "Error|Traceback|Exception") {
    Write-Host "Error: memopad installed but failed to start:" -ForegroundColor Red
    Write-Host $versionOutput -ForegroundColor Red
    Write-Host "The package is broken (likely an import error). Reinstall or report the issue." -ForegroundColor Red
    exit 1
}
Write-Host "OK: $($versionOutput | Select-Object -First 1)" -ForegroundColor Green

# Configure Claude Desktop
$configPath = "$env:APPDATA\Claude\claude_desktop_config.json"
$configDir = Split-Path $configPath -Parent

if (-not (Test-Path $configDir)) {
    New-Item -ItemType Directory -Force -Path $configDir | Out-Null
}

$config = @{ mcpServers = @{} }
if (Test-Path $configPath) {
    try {
        $jsonContent = Get-Content $configPath -Raw
        if (-not [string]::IsNullOrWhiteSpace($jsonContent)) {
            $config = $jsonContent | ConvertFrom-Json -AsHashtable
        }
    } catch {
        Write-Host "Warning: Could not parse existing config file. Creating a new one." -ForegroundColor Yellow
    }
}

if (-not $config.ContainsKey("mcpServers")) {
    $config["mcpServers"] = @{}
}

# Update memopad configuration
$config.mcpServers["memopad"] = @{
    command = $memopadPath
    args    = @("mcp", "--transport", "stdio")
}

# Save configuration
$config | ConvertTo-Json -Depth 10 | Set-Content $configPath
Write-Host "Updated Claude Desktop configuration at: $configPath" -ForegroundColor Green
Write-Host "Please restart Claude Desktop to apply changes." -ForegroundColor Cyan
