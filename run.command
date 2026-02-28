#!/bin/zsh

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_FILE="$SCRIPT_DIR/gui_launcher.log"
cd "$SCRIPT_DIR" || exit 1

echo "=== Coc Auto Tool ==="
echo "Dang kiem tra moi truong..."

if ! command -v python3 >/dev/null 2>&1; then
  echo "Loi: khong tim thay python3."
  echo "Hay cai Python 3 roi chay lai."
  read -r "?Nhan Enter de dong cua so..."
  exit 1
fi

if ! python3 -c "import tkinter" >/dev/null 2>&1; then
  echo "Loi: Python hien tai khong co tkinter."
  echo "Can cai ban Python co ho tro Tk de mo giao dien."
  read -r "?Nhan Enter de dong cua so..."
  exit 1
fi

if command -v adb >/dev/null 2>&1; then
  echo "OK: tim thay adb."
else
  echo "Canh bao: khong tim thay adb trong PATH."
  echo "Tool van mo, nhung tinh nang dieu khien thiet bi se khong chay."
fi

if python3 -c "import flask" >/dev/null 2>&1; then
  echo "OK: da co flask."
else
  echo "Canh bao: chua cai flask."
  echo "Neu can chay server.py, dung lenh: python3 -m pip install flask"
fi

echo "Dang mo giao dien..."
nohup python3 "$SCRIPT_DIR/gui.py" >"$LOG_FILE" 2>&1 < /dev/null &
APP_PID=$!
disown "$APP_PID" 2>/dev/null

sleep 1
if kill -0 "$APP_PID" >/dev/null 2>&1; then
  echo "Da mo giao dien trong nen (PID: $APP_PID)."
  echo "Ban co the dong cua so Terminal, tool van tiep tuc chay."
  echo "Log: $LOG_FILE"
  exit 0
fi

echo "Khong the mo giao dien. Kiem tra log: $LOG_FILE"
read -r "?Nhan Enter de dong cua so..."
exit 1
