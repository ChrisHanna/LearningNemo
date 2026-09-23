[CmdletBinding()]
param(
    [string]$OutputPath = (Join-Path (Split-Path -Parent $PSScriptRoot) "LearningNeMo-review.zip")
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$output = [System.IO.Path]::GetFullPath($OutputPath)
if (Test-Path -LiteralPath $output) {
    throw "Output already exists: $output"
}

$allowedRoots = @("configs", "docs", "infra", "scripts", "src", "tests")
$allowedFiles = @(".gitignore", "README.md", "pyproject.toml", "uv.lock")
$excludedSegments = @("__pycache__", ".pytest_cache", ".ruff_cache", ".venv", "build", "dist", "node_modules")
$excludedSuffixes = @(".pyc", ".pyo")
$staging = Join-Path $env:TEMP ("learningnemo-review-" + [guid]::NewGuid().ToString("N"))
$archiveValidated = $false

function Get-RelativePath {
    param([string]$BasePath, [string]$TargetPath)

    $baseUri = New-Object System.Uri(($BasePath.TrimEnd("\") + "\"))
    $targetUri = New-Object System.Uri($TargetPath)
    return [System.Uri]::UnescapeDataString($baseUri.MakeRelativeUri($targetUri).ToString()).Replace("/", "\")
}

try {
    New-Item -ItemType Directory -Path $staging | Out-Null
    $files = @()
    foreach ($allowedFile in $allowedFiles) {
        $candidate = Join-Path $repoRoot $allowedFile
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            $files += Get-Item -LiteralPath $candidate
        }
    }
    foreach ($allowedRoot in $allowedRoots) {
        $rootPath = Join-Path $repoRoot $allowedRoot
        if (-not (Test-Path -LiteralPath $rootPath -PathType Container)) {
            continue
        }
        $files += Get-ChildItem -LiteralPath $rootPath -Recurse -File | Where-Object {
            $relative = Get-RelativePath $repoRoot $_.FullName
            $segments = $relative -split '[\/]'
            $excluded = @($segments | Where-Object { $excludedSegments -contains $_ }).Count -gt 0
            $badSuffix = $excludedSuffixes -contains $_.Extension
            -not $excluded -and -not $badSuffix
        }
    }
    $files = @($files | Sort-Object -Property FullName -Unique)
    $outsideAllowlist = @($files | Where-Object {
        $relative = Get-RelativePath $repoRoot $_.FullName
        $segments = $relative -split '[\\/]'
        $allowedFiles -notcontains $relative -and $allowedRoots -notcontains $segments[0]
    })
    if ($outsideAllowlist.Count -gt 0) {
        throw "Internal archive allowlist error."
    }

    foreach ($file in $files) {
        $relative = Get-RelativePath $repoRoot $file.FullName
        $destination = Join-Path $staging $relative
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $destination) | Out-Null
        Copy-Item -LiteralPath $file.FullName -Destination $destination
    }

    $secretPatterns = @(
        'sk-[A-Za-z0-9_-]{20,}',
        '-----BEGIN [A-Z ]*PRIVATE KEY-----',
        '(?i)(client_secret|api_key|password)\s*[:=]\s*["''][^"''$<{]{8,}'
    )
    $textExtensions = @(".css", ".html", ".js", ".json", ".md", ".ps1", ".py", ".sh", ".toml", ".xml", ".yaml", ".yml")
    $findings = @()
    Get-ChildItem -LiteralPath $staging -Recurse -File | Where-Object {
        $textExtensions -contains $_.Extension -and $_.FullName -notmatch '[\\/]vendor[\\/]'
    } | ForEach-Object {
        $content = Get-Content -LiteralPath $_.FullName -Raw
        foreach ($pattern in $secretPatterns) {
            if ($content -match $pattern) {
                $findings += Get-RelativePath $staging $_.FullName
                break
            }
        }
    }
    if ($findings.Count -gt 0) {
        throw "Potential secret material found in: $($findings -join ', ')"
    }

    Compress-Archive -Path (Join-Path $staging "*") -DestinationPath $output
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $archive = [System.IO.Compression.ZipFile]::OpenRead($output)
    try {
        $actualEntries = @(
            $archive.Entries |
                Where-Object { $_.Name -ne "" } |
                ForEach-Object { $_.FullName.Replace("\", "/") }
        )
        $expectedEntries = @(
            $files |
                ForEach-Object { (Get-RelativePath $repoRoot $_.FullName).Replace("\", "/") }
        )
        $missingEntries = @($expectedEntries | Where-Object { $actualEntries -notcontains $_ })
        $unexpectedEntries = @($actualEntries | Where-Object { $expectedEntries -notcontains $_ })
        $duplicateEntries = @($actualEntries | Group-Object | Where-Object { $_.Count -ne 1 })
        if ($missingEntries.Count -gt 0 -or $unexpectedEntries.Count -gt 0 -or $duplicateEntries.Count -gt 0) {
            throw (
                "Archive entry validation failed: missing=$($missingEntries.Count), " +
                "unexpected=$($unexpectedEntries.Count), duplicates=$($duplicateEntries.Count)"
            )
        }
    }
    finally {
        $archive.Dispose()
    }
    $archiveValidated = $true
    Write-Output "Created sanitized review archive: $output"
    Write-Output "Included files: $($files.Count)"
    Write-Output "Validated archive entries: $($expectedEntries.Count)"
}
finally {
    if (Test-Path -LiteralPath $staging) {
        Remove-Item -LiteralPath $staging -Recurse -Force
    }
    if (-not $archiveValidated -and (Test-Path -LiteralPath $output)) {
        Remove-Item -LiteralPath $output -Force
    }
}