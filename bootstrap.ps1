$ErrorActionPreference = "Stop"

$repositoryDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $repositoryDir

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker is required. Install Docker Desktop, then run this again."
}

docker compose version *> $null
if ($LASTEXITCODE -ne 0) {
    throw "Docker Compose v2 is required (the 'docker compose' command)."
}

$cohereKey = $env:COHERE_API_KEY
if ([string]::IsNullOrWhiteSpace($cohereKey)) {
    $secureKey = Read-Host "Cohere API key" -AsSecureString
    $cohereKey = [System.Net.NetworkCredential]::new("", $secureKey).Password
}

if ([string]::IsNullOrWhiteSpace($cohereKey)) {
    throw "A Cohere API key is required for the live learning stack."
}
if ($cohereKey -notmatch '^[A-Za-z0-9._-]+$') {
    throw "The API key contains unsupported characters."
}

$environmentFile = Join-Path $repositoryDir ".env"
$preserved = @()
if (Test-Path $environmentFile) {
    $preserved = Get-Content $environmentFile | Where-Object {
        $_ -notmatch '^COHERE_API_KEY=' -and $_ -notmatch '^HIGHLAND_MODEL_BACKEND='
    }
}
$content = @($preserved) + @(
    "HIGHLAND_MODEL_BACKEND=cohere",
    "COHERE_API_KEY=$cohereKey"
)
[System.IO.File]::WriteAllLines(
    $environmentFile,
    $content,
    [System.Text.UTF8Encoding]::new($false)
)

Write-Host "Starting Highland and synchronizing the fictional enterprise data..."
docker compose up --build --detach --wait
if ($LASTEXITCODE -ne 0) {
    throw "Docker Compose could not start the Highland stack."
}

Write-Host ""
Write-Host "Highland is ready:"
Write-Host "  Workspace:  http://localhost:3000"
Write-Host "  API docs:   http://localhost:8080/docs"
Write-Host "  Mock APIs:  http://localhost:8099"
Write-Host ""
Write-Host "Follow the code: docs/LEARNING_PATH.md"
Write-Host "Stop (keep data): docker compose down"
Write-Host "Reset demo data: docker compose run --rm highland-api highland reset --yes"
