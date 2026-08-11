$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

function Stop-ProcessTree {
    param([System.Diagnostics.Process]$Process)
    if ($null -ne $Process -and -not $Process.HasExited) {
        & taskkill.exe /PID $Process.Id /T /F 2>$null | Out-Null
    }
}

function Test-Python311 {
    param([string]$Executable, [string[]]$PrefixArguments = @())
    try {
        & $Executable @PrefixArguments -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) and sys.maxsize > 2**32 else 1)" 2>$null
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    }
}

Write-Host ""
Write-Host "PJM QR Operator" -ForegroundColor Cyan
Write-Host "================" -ForegroundColor DarkCyan

$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    Write-Host "First run: creating the local Python environment..." -ForegroundColor Yellow
    $PyLauncher = Get-Command py.exe -ErrorAction SilentlyContinue
    $Python = Get-Command python.exe -ErrorAction SilentlyContinue

    if ($null -ne $PyLauncher -and (Test-Python311 -Executable $PyLauncher.Source -PrefixArguments @("-3.11"))) {
        & $PyLauncher.Source -3.11 -m venv .venv
    } elseif ($null -ne $PyLauncher -and (Test-Python311 -Executable $PyLauncher.Source -PrefixArguments @("-3"))) {
        & $PyLauncher.Source -3 -m venv .venv
    } elseif ($null -ne $Python -and (Test-Python311 -Executable $Python.Source)) {
        & $Python.Source -m venv .venv
    } else {
        throw "Python 3.11 or newer was not found. Install 64-bit Python from https://www.python.org/downloads/windows/ and enable 'Add Python to PATH'."
    }
}
if (-not (Test-Python311 -Executable $VenvPython)) {
    throw "The existing .venv does not use 64-bit Python 3.11 or newer. Delete the .venv folder and run start_windows.bat again."
}

$NpmCommand = Get-Command npm.cmd -ErrorAction SilentlyContinue
if ($null -eq $NpmCommand) {
    throw "Node.js was not found. Install Node.js 22.13 or newer from https://nodejs.org/ and run this file again."
}
$NodeCommand = Get-Command node.exe -ErrorAction SilentlyContinue
$NodeVersion = if ($null -ne $NodeCommand) { [version](& $NodeCommand.Source -p "process.versions.node") } else { $null }
if ($null -eq $NodeVersion -or $NodeVersion -lt [version]"22.13.0") {
    throw "Node.js 22.13 or newer is required. Install the current LTS release from https://nodejs.org/ and run this file again."
}

$PreviousErrorPreference = $ErrorActionPreference
$ErrorActionPreference = "Continue"
& $VenvPython -c "import fastapi, uvicorn, websockets, numpy, cv2, zxingcpp" 2>$null
$DependenciesReady = $LASTEXITCODE -eq 0
$ErrorActionPreference = $PreviousErrorPreference
if (-not $DependenciesReady) {
    Write-Host "First run: installing Python dependencies..." -ForegroundColor Yellow
    & $VenvPython -m pip install --upgrade pip
    & $VenvPython -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw "Python dependency installation failed." }
}

if (-not (Test-Path (Join-Path $ProjectRoot "node_modules"))) {
    Write-Host "First run: installing dashboard dependencies..." -ForegroundColor Yellow
    & $NpmCommand.Source ci
    if ($LASTEXITCODE -ne 0) { throw "Dashboard dependency installation failed." }
}

$ApiProcess = $null
$UiProcess = $null
try {
    Write-Host "Starting the QR backend and dashboard..." -ForegroundColor Green
    $ApiProcess = Start-Process -FilePath $VenvPython -ArgumentList @(
        "-m", "uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", "8766"
    ) -WorkingDirectory $ProjectRoot -NoNewWindow -PassThru
    $UiProcess = Start-Process -FilePath $NpmCommand.Source -ArgumentList @(
        "run", "dev"
    ) -WorkingDirectory $ProjectRoot -NoNewWindow -PassThru

    $Ready = $false
    for ($Attempt = 0; $Attempt -lt 120; $Attempt++) {
        if ($ApiProcess.HasExited -or $UiProcess.HasExited) {
            throw "The app stopped before it became ready."
        }
        try {
            Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:8766/api/status" -TimeoutSec 1 | Out-Null
            Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:5174/" -TimeoutSec 1 | Out-Null
            $Ready = $true
            break
        } catch {
            Start-Sleep -Milliseconds 250
        }
    }
    if (-not $Ready) { throw "The app did not become ready in time." }

    Write-Host ""
    Write-Host "Dashboard: http://127.0.0.1:5174" -ForegroundColor Cyan
    Write-Host "Keep this window open. Press Ctrl+C to stop the app." -ForegroundColor DarkGray
    Start-Process "http://127.0.0.1:5174"

    while (-not $ApiProcess.HasExited -and -not $UiProcess.HasExited) {
        Start-Sleep -Seconds 1
    }
    throw "One of the app services stopped unexpectedly."
} finally {
    Stop-ProcessTree $UiProcess
    Stop-ProcessTree $ApiProcess
}
