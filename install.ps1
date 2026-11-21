param(
    [string]$AppDirectory = (Join-Path $env:LOCALAPPDATA 'hamr-app'),
    [switch]$PersistPath
)
$ErrorActionPreference = 'Stop'

function Invoke-Uv {
    & uv @args
    if ($LASTEXITCODE -ne 0) { throw "uv exited with status $LASTEXITCODE" }
}

$AppDirectory = [IO.Path]::GetFullPath($AppDirectory)
$metadata = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'pyproject.toml') -Raw
if ($metadata -notmatch '(?m)^version\s*=\s*"([^"]+)"') { throw 'Could not find the hamr version.' }
$version = $Matches[1]
$wheel = Join-Path $PSScriptRoot "dist\hamr-$version-py3-none-any.whl"
if (-not (Test-Path -LiteralPath $wheel -PathType Leaf)) {
    throw "Missing $wheel. Run uv build --wheel from the checkout first."
}
$environment = Join-Path $AppDirectory 'venv'
$scripts = Join-Path $environment 'Scripts'
$requirements = [IO.Path]::GetTempFileName()
try {
    $locked = @(Invoke-Uv export --project $PSScriptRoot --locked --no-dev --no-emit-project --format requirements-txt)
    $hash = (Get-FileHash -LiteralPath $wheel -Algorithm SHA256).Hash.ToLowerInvariant()
    $uri = ([uri]$wheel).AbsoluteUri
    [IO.File]::WriteAllLines($requirements, [string[]]($locked + "hamr @ $uri --hash=sha256:$hash"), [Text.UTF8Encoding]::new($false))
    Invoke-Uv venv --allow-existing --python 3.12 $environment
    Invoke-Uv pip sync --python (Join-Path $scripts 'python.exe') --require-hashes $requirements
    $exe = Join-Path $scripts 'hamr.exe'
    if ((& $exe --version) -ne $version -or $LASTEXITCODE -ne 0) { throw 'Could not verify the installed hamr version.' }
    $env:Path = "$scripts;$env:Path"
    if ($PersistPath) {
        $old = [Environment]::GetEnvironmentVariable('Path', 'User')
        $entries = @($old -split ';' | Where-Object { $_ -and $_ -ne $scripts })
        [Environment]::SetEnvironmentVariable('Path', ((@($scripts) + $entries) -join ';'), 'User')
    }
    Write-Host "hamr $version"
    Write-Host "Installed command: $exe"
    if ((Get-Command hamr).Source -ne $exe) {
        Write-Warning "Another command named hamr takes priority. Use $exe directly."
    }
}
finally {
    Remove-Item -LiteralPath $requirements -ErrorAction SilentlyContinue
}
