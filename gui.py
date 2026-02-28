import re
import subprocess
import threading
import time
import tkinter as tk
import json
from tkinter import messagebox, ttk
from pathlib import Path

ADB_PATH = "adb"
DEBUG_LOG = Path(__file__).with_name("record_debug.log")
MACRO_DIR = Path(__file__).with_name("macros")
DEVICE_LIST_FILE = Path(__file__).with_name("devices.json")


class AdbMacroRecorder:
    def __init__(self, device: str, status_cb):
        self.device = device
        self.status_cb = status_cb
        self.recording = False
        self.playing = False
        self._record_thread = None
        self._play_thread = None
        self._record_proc = None
        self._stop_play_event = threading.Event()
        self.events = []
        self.min_tap_interval = 0.08
        self.min_play_delay = 0.05
        self.loop_cycle_delay = 2.5
        self.debug_enabled = True

        self.screen_w, self.screen_h = self._get_screen_size()
        self.max_x, self.max_y = self._get_touch_max()

    def _adb(self, *args):
        cmd = [ADB_PATH, "-s", self.device] + list(args)
        return subprocess.run(cmd, capture_output=True, text=True)

    def is_device_online(self):
        r = self._adb("get-state")
        return r.returncode == 0 and "device" in (r.stdout or "").strip()

    def set_events(self, events):
        sanitized = []
        for ev in events:
            try:
                x = int(ev["x"])
                y = int(ev["y"])
                delay = float(ev["delay"])
            except (KeyError, ValueError, TypeError):
                continue
            sanitized.append({"x": x, "y": y, "delay": max(0.0, delay)})
        self.events = sanitized

    def _get_screen_size(self):
        result = self._adb("shell", "wm", "size")
        m = re.search(r"Physical size:\s*(\d+)x(\d+)", result.stdout)
        if not m:
            return 1280, 720
        return int(m.group(1)), int(m.group(2))

    def _get_touch_max(self):
        result = self._adb("shell", "getevent", "-lp")
        x_max = None
        y_max = None
        for line in result.stdout.splitlines():
            line = line.strip()
            if "ABS_MT_POSITION_X" in line:
                m = re.search(r"max\s+(\d+)", line)
                if m:
                    x_max = int(m.group(1))
            if "ABS_MT_POSITION_Y" in line:
                m = re.search(r"max\s+(\d+)", line)
                if m:
                    y_max = int(m.group(1))
            if x_max is not None and y_max is not None:
                break

        if not x_max or not y_max:
            # fallback thường gặp
            return 32767, 32767
        return x_max, y_max

    def _raw_to_screen(self, raw_x, raw_y):
        x = int(raw_x * self.screen_w / max(self.max_x, 1))
        y = int(raw_y * self.screen_h / max(self.max_y, 1))
        x = max(0, min(self.screen_w - 1, x))
        y = max(0, min(self.screen_h - 1, y))
        return x, y

    def _sleep_interruptible(self, duration_s: float) -> bool:
        """Sleep theo nhịp nhỏ để stop play có hiệu lực ngay."""
        end_t = time.monotonic() + max(0.0, duration_s)
        while not self._stop_play_event.is_set():
            remain = end_t - time.monotonic()
            if remain <= 0:
                return True
            time.sleep(min(0.1, remain))
        return False

    def _build_cycle_events(self):
        """Reset/chuan hoa tap list cho moi cycle de tranh sai lech du lieu."""
        cycle_events = []
        for ev in self.events:
            try:
                x = int(ev["x"])
                y = int(ev["y"])
                delay = float(ev["delay"])
            except (KeyError, ValueError, TypeError):
                continue
            x = max(0, min(self.screen_w - 1, x))
            y = max(0, min(self.screen_h - 1, y))
            cycle_events.append({"x": x, "y": y, "delay": max(0.0, delay)})
        return cycle_events

    def start_recording(self):
        if self.recording:
            return
        if not self.is_device_online():
            self.status_cb("Device offline, hay Connect lai")
            return
        self.events = []
        if self.debug_enabled:
            DEBUG_LOG.write_text("", encoding="utf-8")
        self.recording = True
        self._record_thread = threading.Thread(target=self._record_loop, daemon=True)
        self._record_thread.start()
        self.status_cb("Dang recording...")

    def stop_recording(self):
        if not self.recording:
            return
        self.recording = False
        if self._record_proc and self._record_proc.poll() is None:
            self._record_proc.terminate()
        if len(self.events) == 0:
            self.status_cb("Da stop recording. 0 diem (xem record_debug.log)")
        else:
            self.status_cb(f"Da stop recording. So diem: {len(self.events)}")

    def _record_loop(self):
        cmd = [ADB_PATH, "-s", self.device, "shell", "getevent", "-lt"]
        self._record_proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

        # Ví dụ line:
        # [ 1718.123456] /dev/input/event5: EV_ABS ABS_MT_POSITION_X 0000023a
        # [ 1718.223456] /dev/input/event5: EV_KEY BTN_TOUCH UP
        raw_x = None
        raw_y = None
        touch_active = False
        saw_abs_update = False
        last_tap_t = None

        def parse_hex_tail(s: str):
            m = re.search(r"([0-9a-fA-F]{1,8})\s*$", s.strip())
            if not m:
                return None
            try:
                return int(m.group(1), 16)
            except ValueError:
                return None

        def append_tap(t: float):
            nonlocal last_tap_t
            if raw_x is None or raw_y is None:
                return
            if last_tap_t is not None and (t - last_tap_t) < self.min_tap_interval:
                return
            x, y = self._raw_to_screen(raw_x, raw_y)
            delay = 0.0 if last_tap_t is None else max(0.0, t - last_tap_t)
            self.events.append({"x": x, "y": y, "delay": delay})
            last_tap_t = t
            self.status_cb(f"Recording... {len(self.events)} diem")

        def parse_raw_triplet(s: str):
            # Dang tho: "...: 0003 0035 00001234"
            m = re.search(r":\s*([0-9a-fA-F]{4})\s+([0-9a-fA-F]{4})\s+([0-9a-fA-F]{8})\s*$", s.strip())
            if not m:
                return None
            etype = int(m.group(1), 16)
            ecode = int(m.group(2), 16)
            value = int(m.group(3), 16)
            return etype, ecode, value

        try:
            assert self._record_proc.stdout is not None
            for line in self._record_proc.stdout:
                if not self.recording:
                    break

                lower = line.lower()
                if self.debug_enabled:
                    with DEBUG_LOG.open("a", encoding="utf-8") as f:
                        f.write(line)
                if "permission denied" in lower or "not permitted" in lower:
                    self.status_cb("getevent bi tu choi quyen, khong the record")
                    break

                m_time = re.search(r"\[\s*([0-9]+\.[0-9]+)\]", line)
                if m_time:
                    t = float(m_time.group(1))
                else:
                    t = time.monotonic()

                if ("ABS_MT_POSITION_X" in line) or ("ABS_X" in line):
                    v = parse_hex_tail(line)
                    if v is not None:
                        raw_x = v
                        touch_active = True
                        saw_abs_update = True

                elif ("ABS_MT_POSITION_Y" in line) or ("ABS_Y" in line):
                    v = parse_hex_tail(line)
                    if v is not None:
                        raw_y = v
                        touch_active = True
                        saw_abs_update = True

                elif "BTN_TOUCH" in line:
                    if "DOWN" in line or "00000001" in line:
                        touch_active = True
                    elif "UP" in line or "00000000" in line:
                        append_tap(t)
                        touch_active = False

                elif "ABS_MT_TRACKING_ID" in line and "ffffffff" in line.lower():
                    # Nhiều máy Android dùng TRACKING_ID = ffffffff để báo nhấc tay.
                    append_tap(t)
                    touch_active = False

                elif "SYN_REPORT" in line and touch_active and saw_abs_update:
                    # BlueStacks co the chi phat ABS_MT_* + SYN_REPORT, khong co BTN_TOUCH.
                    append_tap(t)
                    saw_abs_update = False
                else:
                    raw_evt = parse_raw_triplet(line)
                    if not raw_evt:
                        continue
                    etype, ecode, value = raw_evt

                    # EV_ABS
                    if etype == 0x0003:
                        # ABS_MT_POSITION_X or ABS_X
                        if ecode in (0x0035, 0x0000):
                            raw_x = value
                            touch_active = True
                            saw_abs_update = True
                        # ABS_MT_POSITION_Y or ABS_Y
                        elif ecode in (0x0036, 0x0001):
                            raw_y = value
                            touch_active = True
                            saw_abs_update = True
                        # ABS_MT_TRACKING_ID = -1 => finger up
                        elif ecode == 0x0039 and value == 0xFFFFFFFF:
                            append_tap(t)
                            touch_active = False
                    # EV_KEY / BTN_TOUCH
                    elif etype == 0x0001 and ecode == 0x014A:
                        if value == 1:
                            touch_active = True
                        elif value == 0:
                            append_tap(t)
                            touch_active = False
                    # EV_SYN / SYN_REPORT
                    elif etype == 0x0000 and ecode == 0x0000 and touch_active and saw_abs_update:
                        append_tap(t)
                        saw_abs_update = False
        finally:
            if self._record_proc and self._record_proc.poll() is None:
                self._record_proc.terminate()

    def play(self, loop=False):
        if self.playing:
            return
        if not self.is_device_online():
            self.status_cb("Device offline, khong the play")
            return
        if not self.events:
            self.status_cb("Khong co du lieu de play")
            return

        self.playing = True
        self._stop_play_event.clear()
        self._play_thread = threading.Thread(target=self._play_loop, args=(loop,), daemon=True)
        self._play_thread.start()
        mode = "loop" if loop else "1 lan"
        self.status_cb(f"Dang play {len(self.events)} diem ({mode})...")

    def stop_play(self):
        if not self.playing:
            return
        self._stop_play_event.set()
        self.playing = False
        self.status_cb("Da stop play")

    def _play_loop(self, loop=False):
        try:
            cycle = 0
            while not self._stop_play_event.is_set():
                cycle += 1
                cycle_events = self._build_cycle_events()
                if not cycle_events:
                    self.status_cb("Khong co du lieu hop le de play")
                    return

                if loop and cycle > 1:
                    self.status_cb(f"Cho {self.loop_cycle_delay:.1f}s truoc vong {cycle}...")
                    if not self._sleep_interruptible(self.loop_cycle_delay):
                        self.status_cb("Play bi dung boi nguoi dung")
                        return

                for idx, event in enumerate(cycle_events, start=1):
                    if self._stop_play_event.is_set():
                        self.status_cb("Play bi dung boi nguoi dung")
                        return
                    sleep_s = max(event["delay"], self.min_play_delay)
                    if sleep_s > 0 and not self._sleep_interruptible(sleep_s):
                        self.status_cb("Play bi dung boi nguoi dung")
                        return
                    self._adb("shell", "input", "tap", str(event["x"]), str(event["y"]))
                    if loop:
                        self.status_cb(f"Loop {cycle} | {idx}/{len(cycle_events)}")
                    else:
                        self.status_cb(f"Play {idx}/{len(cycle_events)}")
                if not loop:
                    break
            self.status_cb("Play xong")
        finally:
            self.playing = False


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("CoC Macro Recorder (BlueStacks)")
        self.geometry("980x420")

        self.recorders = {}
        self.current_events = []
        self.macro_map = {}
        self.saved_devices = []
        self.connection_history = []
        self._build_ui()
        self._load_saved_devices()
        self.refresh_macro_list()

    def _build_ui(self):
        root = ttk.Frame(self, padding=12)
        root.pack(fill="both", expand=True)

        ttk.Label(root, text="Danh sach devices (phay):").grid(row=0, column=0, sticky="w")
        self.devices_var = tk.StringVar(value="127.0.0.1:5555,127.0.0.1:5556")
        self.devices_entry = ttk.Entry(root, textvariable=self.devices_var)
        self.devices_entry.grid(row=0, column=1, columnspan=3, sticky="ew", padx=(6, 6))

        ttk.Button(root, text="Connect Devices", command=self.connect_devices).grid(row=0, column=4, sticky="ew")
        ttk.Button(root, text="Test Tap All", command=self.test_tap_all).grid(row=0, column=5, sticky="ew", padx=(6, 0))

        ttk.Label(root, text="Lich su ket noi:").grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.history_var = tk.StringVar()
        self.history_combo = ttk.Combobox(root, textvariable=self.history_var, state="readonly")
        self.history_combo.grid(row=1, column=1, columnspan=3, sticky="ew", padx=(6, 6), pady=(8, 0))
        ttk.Button(root, text="Dung lai", command=self.use_selected_history).grid(row=1, column=4, sticky="ew", pady=(8, 0))
        ttk.Button(root, text="Xoa item connect", command=self.delete_selected_connection).grid(
            row=1, column=5, sticky="ew", padx=(6, 0), pady=(8, 0)
        )

        ttk.Label(root, text="Record device:").grid(row=2, column=0, sticky="w", pady=(10, 0))
        self.record_device_var = tk.StringVar()
        self.record_device_combo = ttk.Combobox(root, textvariable=self.record_device_var, state="readonly")
        self.record_device_combo.grid(row=2, column=1, columnspan=2, sticky="ew", padx=(6, 6), pady=(10, 0))

        ttk.Button(root, text="Recording", command=self.start_record).grid(row=2, column=3, sticky="ew", pady=(10, 0))
        ttk.Button(root, text="Stop Recording", command=self.stop_record).grid(row=2, column=4, sticky="ew", pady=(10, 0))

        ttk.Separator(root, orient="horizontal").grid(row=3, column=0, columnspan=7, sticky="ew", pady=10)

        ttk.Label(root, text="Macro list:").grid(row=4, column=0, sticky="w")
        self.macro_var = tk.StringVar()
        self.macro_combo = ttk.Combobox(root, textvariable=self.macro_var, state="readonly")
        self.macro_combo.grid(row=4, column=1, columnspan=3, sticky="ew", padx=(6, 6))

        ttk.Button(root, text="Load Macro", command=self.load_selected_macro).grid(row=4, column=4, sticky="ew")
        ttk.Button(root, text="Refresh", command=self.refresh_macro_list).grid(row=4, column=5, sticky="ew", padx=(6, 0))
        ttk.Button(root, text="Xoa macro", command=self.delete_selected_macro).grid(row=4, column=6, sticky="ew", padx=(6, 0))

        ttk.Label(root, text="Save as name:").grid(row=5, column=0, sticky="w", pady=(10, 0))
        self.save_name_var = tk.StringVar(value="macro_1")
        ttk.Entry(root, textvariable=self.save_name_var).grid(row=5, column=1, columnspan=2, sticky="ew", padx=(6, 6), pady=(10, 0))
        ttk.Button(root, text="Save Macro", command=self.save_macro).grid(row=5, column=3, sticky="ew", pady=(10, 0))

        ttk.Button(root, text="Play All Devices", command=self.play_all).grid(row=5, column=4, sticky="ew", pady=(10, 0))
        ttk.Button(root, text="Stop Play All", command=self.stop_play_all).grid(row=5, column=5, sticky="ew", padx=(6, 0), pady=(10, 0))

        self.loop_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(root, text="Loop macro", variable=self.loop_var).grid(
            row=6, column=4, columnspan=2, sticky="e"
        )

        self.lbl_count_var = tk.StringVar(value="So diem trong macro hien tai: 0")
        ttk.Label(root, textvariable=self.lbl_count_var).grid(row=6, column=0, columnspan=4, sticky="w", pady=(12, 4))

        self.status_var = tk.StringVar(value="San sang")
        ttk.Label(root, textvariable=self.status_var, foreground="#1f6aa5").grid(row=7, column=0, columnspan=7, sticky="w")

        for col in range(7):
            root.columnconfigure(col, weight=1)

    def set_status(self, text):
        self.after(0, self._set_status_ui, text)

    def _set_status_ui(self, text):
        self.status_var.set(text)

    def _update_record_device_combo(self):
        devices = sorted(self.recorders.keys())
        self.record_device_combo["values"] = devices
        if devices and self.record_device_var.get() not in devices:
            self.record_device_var.set(devices[0])
        if not devices:
            self.record_device_var.set("")

    def _load_saved_devices(self):
        if not DEVICE_LIST_FILE.exists():
            return
        try:
            payload = json.loads(DEVICE_LIST_FILE.read_text(encoding="utf-8"))
            devices = payload.get("devices", [])
            history = payload.get("history", [])
            if isinstance(devices, list):
                clean = [str(x).strip() for x in devices if str(x).strip()]
                self.saved_devices = clean
                if clean:
                    self.devices_var.set(",".join(clean))
            if isinstance(history, list):
                self.connection_history = [str(x).strip() for x in history if str(x).strip()]
                self._update_history_combo()
        except Exception:
            self.saved_devices = []
            self.connection_history = []

    def _save_devices(self):
        payload = {
            "devices": self.saved_devices,
            "history": self.connection_history,
            "updated_at": int(time.time()),
        }
        DEVICE_LIST_FILE.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")

    def _update_history_combo(self):
        values = list(self.connection_history)
        self.history_combo["values"] = values
        if values and self.history_var.get() not in values:
            self.history_var.set(values[0])
        if not values:
            self.history_var.set("")

    def _push_connection_history(self, devices):
        for dev in devices:
            item = str(dev).strip()
            if not item:
                continue
            if item in self.connection_history:
                self.connection_history.remove(item)
            self.connection_history.insert(0, item)
        self.connection_history = self.connection_history[:100]
        self._update_history_combo()

    def use_selected_history(self):
        selected = self.history_var.get().strip()
        if not selected:
            messagebox.showwarning("Canh bao", "Chon item trong lich su ket noi")
            return
        current = [x.strip() for x in self.devices_var.get().split(",") if x.strip()]
        if selected not in current:
            current.append(selected)
            self.devices_var.set(",".join(current))
        self.set_status(f"Da them vao danh sach connect: {selected}")

    def delete_selected_connection(self):
        selected = self.history_var.get().strip()
        if not selected:
            messagebox.showwarning("Canh bao", "Chon item connect de xoa")
            return
        if selected in self.connection_history:
            self.connection_history.remove(selected)
        self.saved_devices = [x for x in self.saved_devices if x != selected]
        if selected in self.recorders:
            self.recorders.pop(selected, None)
            self._update_record_device_combo()
        self._update_history_combo()
        self._save_devices()
        self.set_status(f"Da xoa item connect: {selected}")

    def connect_devices(self):
        raw = self.devices_var.get().strip()
        if not raw:
            messagebox.showerror("Loi", "Nhap it nhat 1 device")
            return

        devices = [x.strip() for x in raw.split(",") if x.strip()]
        if not devices:
            messagebox.showerror("Loi", "Danh sach device khong hop le")
            return

        connected = []
        failed = []
        for device in devices:
            proc = subprocess.run([ADB_PATH, "connect", device], capture_output=True, text=True)
            out = (proc.stdout + proc.stderr).strip()
            recorder = AdbMacroRecorder(device, lambda msg, d=device: self.set_status(f"[{d}] {msg}"))
            if recorder.is_device_online():
                self.recorders[device] = recorder
                connected.append(device)
            else:
                failed.append(f"{device} ({out})")

        # Luu lai danh sach device connect thanh cong de lan sau dung lai.
        self.saved_devices = sorted(set(connected)) if connected else devices
        self._push_connection_history(devices)
        self._save_devices()

        self._update_record_device_combo()
        msg = f"Connected: {len(connected)} | Failed: {len(failed)}"
        if failed:
            msg += " | " + "; ".join(failed[:2])
        self.set_status(msg)

    def test_tap_all(self):
        if not self.recorders:
            messagebox.showwarning("Canh bao", "Hay Connect device truoc")
            return
        ok = 0
        for device, recorder in self.recorders.items():
            if not recorder.is_device_online():
                continue
            cx = recorder.screen_w // 2
            cy = recorder.screen_h // 2
            r = recorder._adb("shell", "input", "tap", str(cx), str(cy))
            if r.returncode == 0:
                ok += 1
            else:
                self.set_status(f"[{device}] Test tap loi: {(r.stderr or r.stdout).strip()}")
        self.set_status(f"Da test tap tren {ok}/{len(self.recorders)} devices")

    def start_record(self):
        device = self.record_device_var.get().strip()
        if not device or device not in self.recorders:
            messagebox.showwarning("Canh bao", "Hay chon record device")
            return
        recorder = self.recorders[device]
        recorder.start_recording()

    def stop_record(self):
        device = self.record_device_var.get().strip()
        if not device or device not in self.recorders:
            return
        recorder = self.recorders[device]
        recorder.stop_recording()
        self.current_events = list(recorder.events)
        self.lbl_count_var.set(f"So diem trong macro hien tai: {len(self.current_events)}")

    def _safe_name(self, text):
        name = re.sub(r"[^a-zA-Z0-9_-]+", "_", text.strip())
        return name.strip("_") or "macro"

    def save_macro(self):
        if not self.current_events:
            messagebox.showwarning("Canh bao", "Chua co thao tac de luu")
            return
        MACRO_DIR.mkdir(parents=True, exist_ok=True)
        name = self._safe_name(self.save_name_var.get())
        ts = int(time.time())
        file_path = MACRO_DIR / f"{name}_{ts}.json"
        payload = {"name": name, "created_at": ts, "events": self.current_events}
        file_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
        self.refresh_macro_list()
        self.set_status(f"Da luu macro: {file_path.name}")

    def refresh_macro_list(self):
        MACRO_DIR.mkdir(parents=True, exist_ok=True)
        self.macro_map = {}
        values = []
        for fp in sorted(MACRO_DIR.glob("*.json"), reverse=True):
            try:
                data = json.loads(fp.read_text(encoding="utf-8"))
                name = data.get("name", fp.stem)
                events = data.get("events", [])
                label = f"{name} ({len(events)} diem) - {fp.name}"
                self.macro_map[label] = fp
                values.append(label)
            except Exception:
                continue
        self.macro_combo["values"] = values
        if values and self.macro_var.get() not in values:
            self.macro_var.set(values[0])

    def load_selected_macro(self):
        label = self.macro_var.get()
        fp = self.macro_map.get(label)
        if not fp:
            messagebox.showwarning("Canh bao", "Chon macro trong danh sach")
            return
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
            events = data.get("events", [])
        except Exception as ex:
            messagebox.showerror("Loi", f"Khong doc duoc macro: {ex}")
            return

        self.current_events = events
        self.lbl_count_var.set(f"So diem trong macro hien tai: {len(self.current_events)}")
        self.set_status(f"Da load macro: {fp.name}")

    def delete_selected_macro(self):
        label = self.macro_var.get()
        fp = self.macro_map.get(label)
        if not fp:
            messagebox.showwarning("Canh bao", "Chon macro trong danh sach")
            return
        if not messagebox.askyesno("Xac nhan", f"Xoa macro '{fp.name}'?"):
            return
        try:
            fp.unlink(missing_ok=True)
        except Exception as ex:
            messagebox.showerror("Loi", f"Khong xoa duoc macro: {ex}")
            return
        self.refresh_macro_list()
        self.set_status(f"Da xoa macro: {fp.name}")

    def play_all(self):
        if not self.recorders:
            messagebox.showwarning("Canh bao", "Hay Connect device truoc")
            return
        if not self.current_events:
            messagebox.showwarning("Canh bao", "Hay record hoac load macro truoc")
            return

        started = 0
        loop_mode = self.loop_var.get()
        for device, recorder in self.recorders.items():
            if not recorder.is_device_online():
                continue
            recorder.set_events(self.current_events)
            recorder.play(loop=loop_mode)
            started += 1
        mode = "loop" if loop_mode else "1 lan"
        self.set_status(f"Dang play tren {started} devices ({mode})")

    def stop_play_all(self):
        for recorder in self.recorders.values():
            recorder.stop_play()
        self.set_status("Da stop play tat ca devices")


if __name__ == "__main__":
    app = App()
    app.mainloop()
