import re
import subprocess
import threading
import time
import tkinter as tk
import json
from tkinter import font as tkfont, messagebox, ttk
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
            self.status_cb("Device offline, please reconnect")
            return
        self.events = []
        if self.debug_enabled:
            DEBUG_LOG.write_text("", encoding="utf-8")
        self.recording = True
        self._record_thread = threading.Thread(target=self._record_loop, daemon=True)
        self._record_thread.start()
        self.status_cb("Recording...")

    def stop_recording(self):
        if not self.recording:
            return
        self.recording = False
        if self._record_proc and self._record_proc.poll() is None:
            self._record_proc.terminate()
        if len(self.events) == 0:
            self.status_cb("Recording stopped. 0 points captured (see record_debug.log)")
        else:
            self.status_cb(f"Recording stopped. Points captured: {len(self.events)}")

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
            self.status_cb(f"Recording... {len(self.events)} points")

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
                    self.status_cb("getevent permission denied, unable to record")
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
            self.status_cb("Device offline, unable to play")
            return
        if not self.events:
            self.status_cb("No recorded data to play")
            return

        self.playing = True
        self._stop_play_event.clear()
        self._play_thread = threading.Thread(target=self._play_loop, args=(loop,), daemon=True)
        self._play_thread.start()
        mode = "loop" if loop else "one time"
        self.status_cb(f"Playing {len(self.events)} points ({mode})...")

    def stop_play(self):
        if not self.playing:
            return
        self._stop_play_event.set()
        self.playing = False
        self.status_cb("Playback stopped")

    def _play_loop(self, loop=False):
        try:
            cycle = 0
            while not self._stop_play_event.is_set():
                cycle += 1
                cycle_events = self._build_cycle_events()
                if not cycle_events:
                    self.status_cb("No valid data available for playback")
                    return

                if loop and cycle > 1:
                    self.status_cb(f"Waiting {self.loop_cycle_delay:.1f}s before cycle {cycle}...")
                    if not self._sleep_interruptible(self.loop_cycle_delay):
                        self.status_cb("Playback stopped by user")
                        return

                for idx, event in enumerate(cycle_events, start=1):
                    if self._stop_play_event.is_set():
                        self.status_cb("Playback stopped by user")
                        return
                    sleep_s = max(event["delay"], self.min_play_delay)
                    if sleep_s > 0 and not self._sleep_interruptible(sleep_s):
                        self.status_cb("Playback stopped by user")
                        return
                    self._adb("shell", "input", "tap", str(event["x"]), str(event["y"]))
                    if loop:
                        self.status_cb(f"Loop {cycle} | {idx}/{len(cycle_events)}")
                    else:
                        self.status_cb(f"Play {idx}/{len(cycle_events)}")
                if not loop:
                    break
            self.status_cb("Playback complete")
        finally:
            self.playing = False


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("CoC Macro Recorder (BlueStacks)")
        self.geometry("1220x680")
        self.minsize(1120, 620)
        self.configure(bg="#f4f7fb")

        self.recorders = {}
        self.current_events = []
        self.macro_map = {}
        self.saved_devices = []
        self.connection_history = []
        self._configure_styles()
        self._build_ui()
        self._load_saved_devices()
        self.refresh_macro_list()

    def _configure_styles(self):
        style = ttk.Style(self)
        if "clam" in style.theme_names():
            style.theme_use("clam")

        title_font = tkfont.Font(family="Helvetica", size=18, weight="bold")
        subtitle_font = tkfont.Font(family="Helvetica", size=10)
        section_font = tkfont.Font(family="Helvetica", size=10, weight="bold")
        button_font = tkfont.Font(family="Helvetica", size=10, weight="bold")

        style.configure("App.TFrame", background="#f4f7fb")
        style.configure("Hero.TFrame", background="#f4f7fb")
        style.configure("Panel.TFrame", background="#ffffff")
        style.configure("PanelBar.TFrame", background="#ffffff")
        style.configure(
            "Panel.TLabelframe",
            background="#ffffff",
            borderwidth=1,
            relief="solid",
        )
        style.configure(
            "Panel.TLabelframe.Label",
            background="#ffffff",
            foreground="#18324a",
            font=section_font,
        )
        style.configure("HeaderTitle.TLabel", background="#f4f7fb", foreground="#102a43", font=title_font)
        style.configure("HeaderSub.TLabel", background="#f4f7fb", foreground="#627d98", font=subtitle_font)
        style.configure("Section.TLabel", background="#ffffff", foreground="#243b53", font=section_font)
        style.configure("TLabel", background="#ffffff", foreground="#243b53")
        style.configure("TCheckbutton", background="#ffffff", foreground="#243b53")
        style.configure("Status.TLabel", background="#ffffff", foreground="#1f6aa5", font=subtitle_font)
        style.configure("TEntry", padding=(8, 6), fieldbackground="#ffffff")
        style.configure("TCombobox", padding=(6, 4), fieldbackground="#ffffff", background="#ffffff")
        style.configure(
            "TButton",
            padding=(12, 8),
            relief="flat",
            borderwidth=0,
            font=button_font,
            foreground="#243b53",
            background="#eef4fa",
        )
        style.map(
            "TButton",
            background=[("active", "#e3edf7"), ("pressed", "#d7e5f2"), ("disabled", "#f4f7fb")],
            foreground=[("disabled", "#9fb3c8")],
        )
        style.configure(
            "Action.TButton",
            padding=(12, 8),
            relief="flat",
            borderwidth=0,
            foreground="#243b53",
            background="#eef4fa",
        )
        style.map(
            "Action.TButton",
            background=[("active", "#e3edf7"), ("pressed", "#d7e5f2"), ("disabled", "#f4f7fb")],
            foreground=[("disabled", "#9fb3c8")],
        )
        style.configure(
            "Primary.TButton",
            padding=(12, 8),
            relief="flat",
            borderwidth=0,
            foreground="#ffffff",
            background="#1f6aa5",
        )
        style.map(
            "Primary.TButton",
            background=[("active", "#185a8c"), ("pressed", "#144b72"), ("disabled", "#9fc2db")],
            foreground=[("disabled", "#d9e2ec")],
        )
        style.configure(
            "Danger.TButton",
            padding=(12, 8),
            relief="flat",
            borderwidth=0,
            foreground="#ffffff",
            background="#d64545",
        )
        style.map(
            "Danger.TButton",
            background=[("active", "#bd3636"), ("pressed", "#a82c2c"), ("disabled", "#edb3b3")],
            foreground=[("disabled", "#f7e8e8")],
        )
        style.configure(
            "Accent.TButton",
            padding=(12, 8),
            relief="flat",
            borderwidth=0,
            foreground="#ffffff",
            background="#2f855a",
        )
        style.map(
            "Accent.TButton",
            background=[("active", "#276749"), ("pressed", "#22543d"), ("disabled", "#9fceb6")],
            foreground=[("disabled", "#e5f3eb")],
        )
        style.configure("Treeview", background="#ffffff", fieldbackground="#ffffff", foreground="#243b53", rowheight=30)
        style.map("Treeview", background=[("selected", "#d9eaf7")], foreground=[("selected", "#102a43")])
        style.configure(
            "Treeview.Heading",
            background="#e9f1f8",
            foreground="#102a43",
            font=section_font,
            relief="flat",
            padding=(8, 8),
        )
        style.configure("TSeparator", background="#d9e2ec")
        style.configure(
            "Vertical.TScrollbar",
            background="#d9e6f2",
            troughcolor="#f5f8fb",
            borderwidth=0,
            arrowcolor="#486581",
            relief="flat",
        )
        style.map(
            "Vertical.TScrollbar",
            background=[("active", "#c7d9ea"), ("pressed", "#b6cde3")],
            arrowcolor=[("active", "#334e68")],
        )

    def _build_ui(self):
        root = ttk.Frame(self, padding=16, style="App.TFrame")
        root.pack(fill="both", expand=True)

        root.columnconfigure(0, weight=3)
        root.columnconfigure(1, weight=2)
        root.rowconfigure(1, weight=1)

        hero = ttk.Frame(root, style="Hero.TFrame")
        hero.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 14))
        hero.columnconfigure(0, weight=1)

        ttk.Label(hero, text="CoC Macro Control Center", style="HeaderTitle.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(
            hero,
            text="Quan ly thiet bi, ghi thao tac va phat macro tren mot giao dien gon, ro va de thao tac hon.",
            style="HeaderSub.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))

        left_panel = ttk.LabelFrame(root, text="Device Control", padding=14, style="Panel.TLabelframe")
        left_panel.grid(row=1, column=0, sticky="nsew")

        right_panel = ttk.LabelFrame(root, text="Macro Library", padding=14, style="Panel.TLabelframe")
        right_panel.grid(row=1, column=1, sticky="nsew", padx=(16, 0))
        right_panel.rowconfigure(1, weight=1)
        right_panel.columnconfigure(0, weight=1)

        ttk.Label(left_panel, text="Device list (comma-separated):", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        self.devices_var = tk.StringVar(value="127.0.0.1:5555,127.0.0.1:5556")
        self.devices_entry = ttk.Entry(left_panel, textvariable=self.devices_var)
        self.devices_entry.grid(row=0, column=1, columnspan=3, sticky="ew", padx=(6, 6))

        ttk.Button(left_panel, text="Connect Devices", command=self.connect_devices, style="Primary.TButton").grid(
            row=0, column=4, sticky="ew"
        )
        ttk.Button(left_panel, text="Test Tap All", command=self.test_tap_all, style="Action.TButton").grid(
            row=0, column=5, sticky="ew", padx=(6, 0)
        )

        ttk.Label(left_panel, text="Connection history:", style="Section.TLabel").grid(row=1, column=0, sticky="w", pady=(10, 0))
        self.history_var = tk.StringVar()
        self.history_combo = ttk.Combobox(left_panel, textvariable=self.history_var, state="readonly")
        self.history_combo.grid(row=1, column=1, columnspan=3, sticky="ew", padx=(6, 6), pady=(8, 0))
        ttk.Button(left_panel, text="Reuse", command=self.use_selected_history, style="Action.TButton").grid(
            row=1, column=4, sticky="ew", pady=(8, 0)
        )
        ttk.Button(left_panel, text="Delete History Item", command=self.delete_selected_connection, style="Danger.TButton").grid(
            row=1, column=5, sticky="ew", padx=(6, 0), pady=(8, 0)
        )

        self.record_device_var = tk.StringVar()
        ttk.Label(left_panel, text="Connected devices", style="Section.TLabel").grid(row=2, column=0, sticky="w", pady=(14, 0))
        ttk.Button(left_panel, text="▶ Start Recording", command=self.start_record, style="Accent.TButton").grid(
            row=2, column=3, sticky="ew", pady=(10, 0)
        )
        ttk.Button(left_panel, text="■ Stop Recording", command=self.stop_record, style="Danger.TButton").grid(
            row=2, column=4, sticky="ew", pady=(10, 0)
        )

        device_wrap = ttk.Frame(left_panel, style="Panel.TFrame")
        device_wrap.grid(row=3, column=0, columnspan=6, sticky="nsew", pady=(6, 0))
        device_wrap.columnconfigure(0, weight=1)
        device_wrap.rowconfigure(0, weight=1)

        self.device_table = ttk.Treeview(
            device_wrap,
            columns=("device", "status", "screen"),
            show="headings",
            height=5,
        )
        self.device_table.heading("device", text="Device")
        self.device_table.heading("status", text="Status")
        self.device_table.heading("screen", text="Screen")
        self.device_table.column("device", width=180, minwidth=140, anchor="w")
        self.device_table.column("status", width=90, minwidth=80, anchor="center", stretch=False)
        self.device_table.column("screen", width=110, minwidth=90, anchor="center", stretch=False)
        self.device_table.grid(row=0, column=0, sticky="nsew")
        self.device_table.bind("<<TreeviewSelect>>", self._on_device_select)

        device_scroll = ttk.Scrollbar(device_wrap, orient="vertical", command=self.device_table.yview, style="Vertical.TScrollbar")
        device_scroll.grid(row=0, column=1, sticky="ns")
        self.device_table.configure(yscrollcommand=device_scroll.set)

        ttk.Separator(left_panel, orient="horizontal").grid(row=4, column=0, columnspan=6, sticky="ew", pady=10)

        ttk.Label(left_panel, text="Save as name:", style="Section.TLabel").grid(row=5, column=0, sticky="w", pady=(6, 0))
        self.save_name_var = tk.StringVar(value="macro_1")
        ttk.Entry(left_panel, textvariable=self.save_name_var).grid(
            row=5, column=1, columnspan=2, sticky="ew", padx=(6, 6), pady=(4, 0)
        )
        ttk.Button(left_panel, text="Save Macro", command=self.save_macro, style="Primary.TButton").grid(
            row=5, column=3, sticky="ew", pady=(4, 0)
        )

        ttk.Button(left_panel, text="Play All Devices", command=self.play_all, style="Primary.TButton").grid(
            row=6, column=3, sticky="ew", pady=(12, 0)
        )
        ttk.Button(left_panel, text="Stop Play All", command=self.stop_play_all, style="Danger.TButton").grid(
            row=6, column=4, sticky="ew", padx=(6, 0), pady=(10, 0)
        )

        self.loop_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(left_panel, text="Loop macro", variable=self.loop_var).grid(
            row=6, column=5, sticky="e", pady=(10, 0)
        )

        self.lbl_count_var = tk.StringVar(value="Points in current macro: 0")
        ttk.Label(left_panel, textvariable=self.lbl_count_var, style="Section.TLabel").grid(
            row=7, column=0, columnspan=4, sticky="w", pady=(12, 4)
        )

        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(left_panel, textvariable=self.status_var, style="Status.TLabel").grid(
            row=8, column=0, columnspan=6, sticky="w"
        )

        ttk.Label(right_panel, text="Macro list", style="Section.TLabel").grid(row=0, column=0, sticky="w")

        table_wrap = ttk.Frame(right_panel, style="Panel.TFrame")
        table_wrap.grid(row=1, column=0, sticky="nsew", pady=(6, 0))
        table_wrap.columnconfigure(0, weight=1)
        table_wrap.rowconfigure(0, weight=1)

        self.macro_table = ttk.Treeview(
            table_wrap,
            columns=("name", "points", "file", "updated"),
            show="headings",
            height=12,
        )
        self.macro_table.heading("name", text="Name")
        self.macro_table.heading("points", text="Points")
        self.macro_table.heading("file", text="File")
        self.macro_table.heading("updated", text="Updated")
        self.macro_table.column("name", width=120, minwidth=100, anchor="w")
        self.macro_table.column("points", width=70, minwidth=60, anchor="center", stretch=False)
        self.macro_table.column("file", width=170, minwidth=140, anchor="w")
        self.macro_table.column("updated", width=120, minwidth=110, anchor="center", stretch=False)
        self.macro_table.grid(row=0, column=0, sticky="nsew")
        self.macro_table.bind("<<TreeviewSelect>>", self._on_macro_select)
        self.macro_table.bind("<Double-1>", lambda _event: self.load_selected_macro())

        macro_scroll = ttk.Scrollbar(table_wrap, orient="vertical", command=self.macro_table.yview, style="Vertical.TScrollbar")
        macro_scroll.grid(row=0, column=1, sticky="ns")
        self.macro_table.configure(yscrollcommand=macro_scroll.set)

        macro_actions = ttk.Frame(right_panel, style="PanelBar.TFrame")
        macro_actions.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        for col in range(3):
            macro_actions.columnconfigure(col, weight=1)
        ttk.Button(macro_actions, text="Load Macro", command=self.load_selected_macro, style="Action.TButton").grid(
            row=0, column=0, sticky="ew"
        )
        ttk.Button(macro_actions, text="Refresh", command=self.refresh_macro_list, style="Primary.TButton").grid(
            row=0, column=1, sticky="ew", padx=6
        )
        ttk.Button(macro_actions, text="Delete Macro", command=self.delete_selected_macro, style="Danger.TButton").grid(
            row=0, column=2, sticky="ew"
        )

        for col in range(6):
            left_panel.columnconfigure(col, weight=1)
        left_panel.rowconfigure(3, weight=1)

    def set_status(self, text):
        self.after(0, self._set_status_ui, text)

    def _set_status_ui(self, text):
        self.status_var.set(text)

    def _update_record_device_combo(self):
        current_selection = self.device_table.selection()
        selected_id = current_selection[0] if current_selection else None
        self.device_table.delete(*self.device_table.get_children())
        devices = sorted(self.recorders.keys())
        for device in devices:
            recorder = self.recorders[device]
            is_online = recorder.is_device_online()
            status = "Online" if is_online else "Offline"
            screen = f"{recorder.screen_w}x{recorder.screen_h}"
            self.device_table.insert("", "end", iid=device, values=(device, status, screen))

        if devices and self.record_device_var.get() not in devices:
            self.record_device_var.set(devices[0])
        if not devices:
            self.record_device_var.set("")
            return

        target = selected_id if selected_id in self.recorders else self.record_device_var.get()
        if target in self.recorders:
            self.device_table.selection_set(target)
            self.device_table.focus(target)

    def _on_device_select(self, _event=None):
        device = self._get_selected_device()
        if device:
            self.record_device_var.set(device)
            self.set_status(f"Selected device: {device}")

    def _get_selected_device(self):
        selection = self.device_table.selection()
        if selection:
            return selection[0]
        device = self.record_device_var.get().strip()
        if device in self.recorders:
            return device
        return ""

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
            messagebox.showwarning("Warning", "Select an item from connection history")
            return
        current = [x.strip() for x in self.devices_var.get().split(",") if x.strip()]
        if selected not in current:
            current.append(selected)
            self.devices_var.set(",".join(current))
        self.set_status(f"Added to connection list: {selected}")

    def delete_selected_connection(self):
        selected = self.history_var.get().strip()
        if not selected:
            messagebox.showwarning("Warning", "Select a connection history item to delete")
            return
        if selected in self.connection_history:
            self.connection_history.remove(selected)
        self.saved_devices = [x for x in self.saved_devices if x != selected]
        if selected in self.recorders:
            self.recorders.pop(selected, None)
            self._update_record_device_combo()
        self._update_history_combo()
        self._save_devices()
        self.set_status(f"Deleted connection history item: {selected}")

    def connect_devices(self):
        raw = self.devices_var.get().strip()
        if not raw:
            messagebox.showerror("Error", "Enter at least one device")
            return

        devices = [x.strip() for x in raw.split(",") if x.strip()]
        if not devices:
            messagebox.showerror("Error", "The device list is invalid")
            return

        connected = []
        failed = []
        next_recorders = {}
        for device in devices:
            proc = subprocess.run([ADB_PATH, "connect", device], capture_output=True, text=True)
            out = (proc.stdout + proc.stderr).strip()
            recorder = AdbMacroRecorder(device, lambda msg, d=device: self.set_status(f"[{d}] {msg}"))
            if recorder.is_device_online():
                next_recorders[device] = recorder
                connected.append(device)
            else:
                failed.append(f"{device} ({out})")

        self.recorders = next_recorders

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
            messagebox.showwarning("Warning", "Connect devices first")
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
                self.set_status(f"[{device}] Test tap failed: {(r.stderr or r.stdout).strip()}")
        self.set_status(f"Test tap completed on {ok}/{len(self.recorders)} devices")

    def start_record(self):
        device = self._get_selected_device()
        if not device or device not in self.recorders:
            messagebox.showwarning("Warning", "Select a recording device")
            return
        recorder = self.recorders[device]
        recorder.start_recording()

    def stop_record(self):
        device = self._get_selected_device()
        if not device or device not in self.recorders:
            return
        recorder = self.recorders[device]
        recorder.stop_recording()
        self.current_events = list(recorder.events)
        self.lbl_count_var.set(f"Points in current macro: {len(self.current_events)}")

    def _safe_name(self, text):
        name = re.sub(r"[^a-zA-Z0-9_-]+", "_", text.strip())
        return name.strip("_") or "macro"

    def save_macro(self):
        if not self.current_events:
            messagebox.showwarning("Warning", "No recorded actions to save")
            return
        MACRO_DIR.mkdir(parents=True, exist_ok=True)
        name = self._safe_name(self.save_name_var.get())
        ts = int(time.time())
        file_path = MACRO_DIR / f"{name}_{ts}.json"
        payload = {"name": name, "created_at": ts, "events": self.current_events}
        file_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
        self.refresh_macro_list()
        self.set_status(f"Macro saved: {file_path.name}")

    def refresh_macro_list(self):
        MACRO_DIR.mkdir(parents=True, exist_ok=True)
        current_selection = self.macro_table.selection()
        selected_id = current_selection[0] if current_selection else None
        self.macro_map = {}
        self.macro_table.delete(*self.macro_table.get_children())
        for fp in sorted(MACRO_DIR.glob("*.json"), reverse=True):
            try:
                data = json.loads(fp.read_text(encoding="utf-8"))
                name = data.get("name", fp.stem)
                events = data.get("events", [])
                created_at = data.get("created_at")
                if isinstance(created_at, (int, float)):
                    updated = time.strftime("%Y-%m-%d %H:%M", time.localtime(created_at))
                else:
                    updated = time.strftime("%Y-%m-%d %H:%M", time.localtime(fp.stat().st_mtime))
                item_id = str(fp)
                self.macro_map[item_id] = fp
                self.macro_table.insert("", "end", iid=item_id, values=(name, len(events), fp.name, updated))
            except Exception:
                continue

        item_ids = self.macro_table.get_children()
        if not item_ids:
            return
        if selected_id in self.macro_map:
            self.macro_table.selection_set(selected_id)
            self.macro_table.focus(selected_id)
        else:
            self.macro_table.selection_set(item_ids[0])
            self.macro_table.focus(item_ids[0])

    def _on_macro_select(self, _event=None):
        selected = self._get_selected_macro_path()
        if selected:
            self.set_status(f"Selected macro: {selected.name}")

    def _get_selected_macro_path(self):
        selection = self.macro_table.selection()
        if not selection:
            return None
        return self.macro_map.get(selection[0])

    def load_selected_macro(self):
        fp = self._get_selected_macro_path()
        if not fp:
            messagebox.showwarning("Warning", "Select a macro from the list")
            return
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
            events = data.get("events", [])
        except Exception as ex:
            messagebox.showerror("Error", f"Unable to read macro: {ex}")
            return

        self.current_events = events
        self.lbl_count_var.set(f"Points in current macro: {len(self.current_events)}")
        self.set_status(f"Macro loaded: {fp.name}")

    def delete_selected_macro(self):
        fp = self._get_selected_macro_path()
        if not fp:
            messagebox.showwarning("Warning", "Select a macro from the list")
            return
        if not messagebox.askyesno("Confirm", f"Delete macro '{fp.name}'?"):
            return
        try:
            fp.unlink(missing_ok=True)
        except Exception as ex:
            messagebox.showerror("Error", f"Unable to delete macro: {ex}")
            return
        self.refresh_macro_list()
        self.set_status(f"Macro deleted: {fp.name}")

    def play_all(self):
        if not self.recorders:
            messagebox.showwarning("Warning", "Connect devices first")
            return
        if not self.current_events:
            messagebox.showwarning("Warning", "Record or load a macro first")
            return

        started = 0
        loop_mode = self.loop_var.get()
        for device, recorder in self.recorders.items():
            if not recorder.is_device_online():
                continue
            recorder.set_events(self.current_events)
            recorder.play(loop=loop_mode)
            started += 1
        mode = "loop" if loop_mode else "one time"
        self.set_status(f"Playback started on {started} devices ({mode})")

    def stop_play_all(self):
        for recorder in self.recorders.values():
            recorder.stop_play()
        self.set_status("Playback stopped on all devices")


if __name__ == "__main__":
    app = App()
    app.mainloop()
