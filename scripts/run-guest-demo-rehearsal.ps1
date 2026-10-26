param(
    [Parameter(Mandatory = $true)]
    [string]$EvidencePath,
    [switch]$PreflightOnly
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$testScript = Join-Path $root 'tests\guest-invoice-cycle.browser.cjs'
$node = (Get-Command node -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
$edge = 'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'
if (-not (Test-Path $edge)) { throw 'The installed Edge browser is required.' }
$package = Get-ChildItem (Join-Path $env:LOCALAPPDATA 'npm-cache\_npx') -Directory |
    Where-Object { Test-Path (Join-Path $_.FullName 'node_modules\playwright\package.json') } |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1
if ($null -eq $package) { throw 'The existing Playwright package is required; no unattended package install is attempted.' }
if (Test-Path $EvidencePath) { throw 'A prior rehearsal attempt is retained. Do not replay it.' }
$env:NODE_PATH = Join-Path $package.FullName 'node_modules'
$env:GUEST_CYCLE_EVIDENCE = $EvidencePath
if ($PreflightOnly) {
    [pscustomobject]@{ Ready = $true; Node = $node; Browser = $edge; Script = $testScript; Evidence = $EvidencePath; MutationsStarted = $false } | ConvertTo-Json
    return
}
New-Item -ItemType Directory -Path (Split-Path -Parent $EvidencePath) -Force | Out-Null
Start-Transcript -Path ($EvidencePath + '.log') -NoClobber | Out-Null
try {
    Set-Location $root
    & $node $testScript
    if ($LASTEXITCODE -ne 0) { throw 'The live guest rehearsal did not confirm completion. Inspect the retained evidence; no action is retried.' }
} finally {
    Stop-Transcript | Out-Null
}