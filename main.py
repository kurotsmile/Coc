# coc_bot_bs2.py
import subprocess
import time
import random
from concurrent.futures import ThreadPoolExecutor

ADB_PATH = "adb"  # hoặc đường dẫn đầy đủ tới adb.exe

# 2 thiết bị BlueStacks (ví dụ)
DEVICES = [
    "127.0.0.1:5555",
    "127.0.0.1:5556",
]

# Tọa độ ví dụ (bạn chỉnh lại theo màn hình giả lập của bạn)
# Mẹo lấy tọa độ: adb -s <serial> shell getevent -lt hoặc dùng app hiển thị touch pointer.
TROOP_SLOTS = {
    "barbarian": (120, 980),
    "archer":    (220, 980),
    "giant":     (320, 980),
}

DROP_POINTS = [
    (500, 300),
    (550, 350),
    (600, 400),
]

# Tọa độ UI mẫu (cần calibrate theo BlueStacks của bạn)
ATTACK_BUTTON = (1140, 650)
FIND_MATCH_BUTTON = (1030, 890)
END_BATTLE_BUTTON = (1130, 80)
CONFIRM_END_BUTTON = (760, 640)

def adb(device, *args):
    cmd = [ADB_PATH, "-s", device] + list(args)
    return subprocess.run(cmd, capture_output=True, text=True)

def tap(device, x, y):
    adb(device, "shell", "input", "tap", str(x), str(y))

def swipe(device, x1, y1, x2, y2, ms=200):
    adb(device, "shell", "input", "swipe", str(x1), str(y1), str(x2), str(y2), str(ms))

def deploy_troops(device, troop_name, count, points):
    if troop_name not in TROOP_SLOTS:
        raise ValueError(f"Không có troop: {troop_name}")
    sx, sy = TROOP_SLOTS[troop_name]

    # Chọn loại lính
    tap(device, sx, sy)
    time.sleep(0.2)

    # Thả lính
    for i in range(count):
        x, y = random.choice(points)
        tap(device, x, y)
        time.sleep(random.uniform(0.08, 0.18))

def start_battle(device):
    tap(device, *ATTACK_BUTTON)
    time.sleep(1.2)
    tap(device, *FIND_MATCH_BUTTON)
    # Chờ game vào màn hình trận
    time.sleep(8.0)

def end_battle(device):
    tap(device, *END_BATTLE_BUTTON)
    time.sleep(0.8)
    tap(device, *CONFIRM_END_BUTTON)
    time.sleep(4.0)

def auto_attack(device):
    # Ví dụ: zoom out trước khi thả quân (tùy game UI)
    # swipe(device, 300, 400, 250, 350, 120)
    start_battle(device)

    deploy_troops(device, "barbarian", 15, DROP_POINTS)
    time.sleep(0.5)
    deploy_troops(device, "archer", 10, DROP_POINTS)
    time.sleep(1.0)
    deploy_troops(device, "giant", 5, DROP_POINTS)
    # Chờ thêm để dọn công trình trước khi rời trận
    time.sleep(12.0)
    end_battle(device)

def run_on_device(device, rounds=1):
    print(f"[{device}] Bắt đầu {rounds} trận tự động...")
    for i in range(1, rounds + 1):
        print(f"[{device}] Trận {i}/{rounds}")
        auto_attack(device)
    print(f"[{device}] Hoàn tất.")

def main():
    # Kết nối ADB tới 2 instance BlueStacks
    for d in DEVICES:
        subprocess.run([ADB_PATH, "connect", d], capture_output=True, text=True)

    with ThreadPoolExecutor(max_workers=2) as ex:
        ex.map(run_on_device, DEVICES, [2] * len(DEVICES))

if __name__ == "__main__":
    main()
