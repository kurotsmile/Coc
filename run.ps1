$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptDir

Write-Host "=== Coc Auto Tool ==="
Write-Host "Dang kiem tra moi truong..."

$pythonLauncher = $null
if (Get-Command py -ErrorAction SilentlyContinue) {
    $pythonLauncher = @("py", "-3")
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $pythonLauncher = @("python")
}

if (-not $pythonLauncher) {
    Write-Host "Loi: khong tim thay Python."
    Write-Host "Hay cai Python 3 roi chay lai."
    Read-Host "Nhan Enter de dong cua so"
    exit 1
}

function Invoke-PythonCheck {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Code
    )

    $exe = $pythonLauncher[0]
    $args = @()
    if ($pythonLauncher.Length -gt 1) {
        $args += $pythonLauncher[1..($pythonLauncher.Length - 1)]
    }
    $args += @("-c", $Code)
    & $exe @args *> $null
}

Invoke-PythonCheck -Code "import tkinter"
if ($LASTEXITCODE -ne 0) {
    Write-Host "Loi: Python hien tai khong co tkinter."
    Write-Host "Can cai ban Python co ho tro Tk de mo giao dien."
    Read-Host "Nhan Enter de dong cua so"
    exit 1
}

if (Get-Command adb -ErrorAction SilentlyContinue) {
    Write-Host "OK: tim thay adb."
} else {
    Write-Host "Canh bao: khong tim thay adb trong PATH."
    Write-Host "Tool van mo, nhung tinh nang dieu khien thiet bi se khong chay."
}

Invoke-PythonCheck -Code "import flask"
if ($LASTEXITCODE -eq 0) {
    Write-Host "OK: da co flask."
} else {
    Write-Host "Canh bao: chua cai flask."
    Write-Host "Neu can chay server.py, dung lenh: python -m pip install flask"
}

Write-Host "Dang mo giao dien..."
$pythonw = Get-Command pythonw -ErrorAction SilentlyContinue
if ($pythonw) {
    Start-Process -FilePath $pythonw.Source -ArgumentList "`"$scriptDir\gui.py`"" -WorkingDirectory $scriptDir
} else {
    $exe = $pythonLauncher[0]
    $args = @()
    if ($pythonLauncher.Length -gt 1) {
        $args += $pythonLauncher[1..($pythonLauncher.Length - 1)]
    }
    $args += "`"$scriptDir\gui.py`""
    Start-Process -FilePath $exe -ArgumentList $args -WorkingDirectory $scriptDir
}

Write-Host "Da mo giao dien trong tien trinh rieng."
Write-Host "Ban co the dong cua so PowerShell, tool van tiep tuc chay."
