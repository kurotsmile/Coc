@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

set "LOG_FILE=%SCRIPT_DIR%gui_launcher.log"

echo === Coc Auto Tool ===
echo Dang kiem tra moi truong...

set "PYTHON_CMD="
where py >nul 2>&1
if %ERRORLEVEL%==0 (
    set "PYTHON_CMD=py -3"
) else (
    where python >nul 2>&1
    if %ERRORLEVEL%==0 (
        set "PYTHON_CMD=python"
    )
)

if not defined PYTHON_CMD (
    echo Loi: khong tim thay Python.
    echo Hay cai Python 3 roi chay lai.
    pause
    exit /b 1
)

call %PYTHON_CMD% -c "import tkinter" >nul 2>&1
if not %ERRORLEVEL%==0 (
    echo Loi: Python hien tai khong co tkinter.
    echo Can cai ban Python co ho tro Tk de mo giao dien.
    pause
    exit /b 1
)

where adb >nul 2>&1
if %ERRORLEVEL%==0 (
    echo OK: tim thay adb.
) else (
    echo Canh bao: khong tim thay adb trong PATH.
    echo Tool van mo, nhung tinh nang dieu khien thiet bi se khong chay.
)

call %PYTHON_CMD% -c "import flask" >nul 2>&1
if %ERRORLEVEL%==0 (
    echo OK: da co flask.
) else (
    echo Canh bao: chua cai flask.
    echo Neu can chay server.py, dung lenh: python -m pip install flask
)

echo Dang mo giao dien...
where pythonw >nul 2>&1
if %ERRORLEVEL%==0 (
    start "" /B pythonw "%SCRIPT_DIR%gui.py"
) else (
    start "" /B %PYTHON_CMD% "%SCRIPT_DIR%gui.py"
)

echo Da mo giao dien trong tien trinh rieng.
echo Ban co the dong cua so CMD, tool van tiep tuc chay.
echo Neu can xem loi khoi dong, chay truc tiep: %PYTHON_CMD% "%SCRIPT_DIR%gui.py"
endlocal
exit /b 0
