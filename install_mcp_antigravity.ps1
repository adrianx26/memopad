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
    }
    else {
        Write-Host "Error: Could not find memopad executable." -ForegroundColor Red
        exit 1
    }
}

Write-Host "Found memopad at: $memopadPath" -ForegroundColor Cyan

# Configure Antigravity
$configPath = Join-Path $env:USERPROFILE ".gemini\antigravity\mcp_config.json"
Write-Host "Checking Antigravity configuration at: $configPath"

if (-not (Test-Path $configPath)) {
    Write-Host "Error: Antigravity config file not found at $configPath." -ForegroundColor Red
    # Optionally create it if missing?
    # New-Item -ItemType File -Path $configPath -Value "{}" -Force
    exit 1
}

$config = @{ mcpServers = @{} }
try {
    $jsonContent = Get-Content $configPath -Raw
    if (-not [string]::IsNullOrWhiteSpace($jsonContent)) {
        # Assuming PowerShell Core or appropriately updated PS 5.1
        # If -AsHashtable is not available, this script might fail on older systems.
        # Mirroring install_mcp.ps1 pattern.
        $config = $jsonContent | ConvertFrom-Json -AsHashtable
    }
}
catch {
    Write-Host "Warning: Could not parse existing config file. Starting with empty config." -ForegroundColor Yellow
}

if (-not $config.ContainsKey("mcpServers")) {
    $config["mcpServers"] = @{}
}

# Update memopad configuration
$config.mcpServers["memopad"] = @{
    command = $memopadPath
    args    = @("mcp")
}

# Save configuration
$config | ConvertTo-Json -Depth 10 | Set-Content $configPath
Write-Host "Updated Antigravity configuration at: $configPath" -ForegroundColor Green
Write-Host "Please restart Antigravity to apply changes." -ForegroundColor Cyan
