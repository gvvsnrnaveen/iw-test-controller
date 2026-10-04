"""Tkinter GUI for iw-test-controller."""

import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
import webbrowser
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from . import __version__
from .config import (BANDWIDTHS, ENCRYPTIONS, MODE_LABELS, MODES, PHY_LABELS, PHY_MODES, RADIOS,
                     load_config, output_path, save_config, validate)
from .planner import build_plan
from .runner import TestRunner
from .server import ControllerServer

RESULT_COLS = [("test_id", "#", 45), ("mode", "Mode", 70), ("radio", "Radio", 55),
               ("channel", "Ch", 45), ("bandwidth", "BW", 55), ("htmode", "htmode", 65),
               ("dut1", "DUT1", 110), ("dut2", "DUT2", 110), ("result", "Result", 60),
               ("link_time_s", "Link s", 55), ("signal_dbm", "dBm", 50), ("loss_pct", "Loss %", 55),
               ("rtt_avg_ms", "RTT ms", 60), ("duration_s", "Dur s", 55), ("reason", "Reason", 360)]
PLAN_COLS = [("idx", "#", 45), ("mode", "Mode", 70), ("radio", "Radio", 55), ("band", "Band", 50),
             ("channel", "Ch", 45), ("freq", "MHz", 55), ("bandwidth", "BW", 55),
             ("htmode", "htmode", 70), ("dfs", "DFS", 45), ("dut1", "DUT1", 130),
             ("dut2", "DUT2", 130)]
AUTHOR = "G.Naveen Kumar"
ABOUT_LINKS = [("LinkedIn", "https://www.linkedin.com/in/naveen-kumar-gutti/"),
               ("GitHub", "https://github.com/gvvsnrnaveen"),
               ("iw-test-controller", "https://github.com/gvvsnrnaveen/iw-test-controller"),
               ("iw-test-agent", "https://github.com/gvvsnrnaveen/iw-test-agent")]
AVATAR_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "github_avatar.png")
DUT_COLS = [("name", "Name", 120), ("ip", "IP", 110), ("model", "Model", 160),
            ("radios", "Radios", 200), ("version", "Agent", 60), ("busy", "Busy", 70)]
# checkbox indicator state -> (fill, border, tick colour or None)
CHECKBOX_STATES = {
    "off": ("#ffffff", "#8a8f98", None),
    "off_hover": ("#eef4ff", "#2563eb", None),
    "off_disabled": ("#eceae6", "#bdbab4", None),
    "on": ("#2563eb", "#2563eb", "#ffffff"),
    "on_hover": ("#1d4ed8", "#1d4ed8", "#ffffff"),
    "on_disabled": ("#a9b9d8", "#a9b9d8", "#f4f4f4"),
}


def _hex_rgb(c):
    return tuple(int(c[i:i + 2], 16) for i in (1, 3, 5))


def _seg_dist(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return ((px - ax - t * dx) ** 2 + (py - ay - t * dy) ** 2) ** 0.5


def _checkbox_image(master, size, bg, fill, border, tick):
    """Draw an anti-aliased rounded checkbox (optionally ticked) into a PhotoImage, no PIL needed."""
    ss = 4  # supersamples per axis
    half = size / 2.0 - 0.5
    radius = size * 0.22
    bw = max(1.0, size / 12.0)
    tw = max(1.5, size * 0.13) / 2.0
    pts = [(0.24 * size, 0.52 * size), (0.42 * size, 0.70 * size), (0.77 * size, 0.31 * size)]
    cols = {"bg": _hex_rgb(bg), "fill": _hex_rgb(fill), "border": _hex_rgb(border),
            "tick": _hex_rgb(tick) if tick else None}
    img = tk.PhotoImage(master=master, width=size, height=size)
    rows = []
    transparent = []
    for y in range(size):
        row = []
        for x in range(size):
            acc = [0, 0, 0]
            outside = 0
            for sy in range(ss):
                for sx in range(ss):
                    px, py = x + (sx + 0.5) / ss, y + (sy + 0.5) / ss
                    qx = max(abs(px - size / 2.0) - (half - radius), 0.0)
                    qy = max(abs(py - size / 2.0) - (half - radius), 0.0)
                    d = (qx * qx + qy * qy) ** 0.5 - radius
                    if d > 0:
                        c = cols["bg"]
                        outside += 1
                    elif cols["tick"] and min(_seg_dist(px, py, *pts[0], *pts[1]),
                                              _seg_dist(px, py, *pts[1], *pts[2])) <= tw:
                        c = cols["tick"]
                    elif d > -bw:
                        c = cols["border"]
                    else:
                        c = cols["fill"]
                    acc[0] += c[0]
                    acc[1] += c[1]
                    acc[2] += c[2]
            n = ss * ss
            if outside == n:
                transparent.append((x, y))
            row.append("#%02x%02x%02x" % (acc[0] // n, acc[1] // n, acc[2] // n))
        rows.append("{" + " ".join(row) + "}")
    img.put(" ".join(rows))
    for x, y in transparent:
        img.transparency_set(x, y, True)
    return img


class ControllerApp:
    def __init__(self, root, cfg, start_server=True):
        self.root = root
        self.cfg = cfg
        self.events = queue.Queue()
        self.server = None
        self.runner = None
        self.auto_started = False
        self.result_rows = []

        root.title("iw-test-controller %s" % __version__)
        try:
            self.icon = tk.PhotoImage(file=AVATAR_PATH)
            root.iconphoto(True, self.icon)
        except tk.TclError:
            pass
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        root.geometry("%dx%d+0+0" % (sw, sh))
        root.minsize(min(1000, sw), min(700, sh))
        root.protocol("WM_DELETE_WINDOW", self.on_close)

        self._build_vars()
        self._build_ui()
        self._load_vars(cfg)
        self.root.after(100, self._poll_events)
        self.root.after(1000, self._refresh_duts_periodic)
        if start_server:
            self.start_server()

    # ------------------------------------------------------------------ vars
    def _build_vars(self):
        V = tk.StringVar
        self.v = {
            "listen": V(), "port": V(), "token": V(),
            "dut_a": V(), "dut_b": V(),
            "channels_2g": V(), "channels_5g": V(),
            "dfs_wait": V(), "encryption": V(), "key": V(), "country": V(),
            "subnet": V(), "assoc_timeout": V(), "ping_count": V(), "ping_size": V(),
            "max_loss": V(), "retries": V(), "settle_time": V(), "output": V(),
            "reconnect_timeout": V(),
        }
        B = tk.BooleanVar
        self.b = {k: B() for k in ("include_dfs", "swap_roles", "bidirectional", "verify_width",
                                   "restore_at_end", "auto_start")}
        self.mode_vars = {m: B() for m in MODES}
        self.radio_vars = {r: B() for r in RADIOS}
        self.bw_vars = {bw: B() for bw in BANDWIDTHS}
        self.phy_vars = {pm: B() for pm in PHY_MODES}

    def _load_vars(self, cfg):
        for k, var in self.v.items():
            var.set(str(cfg.get(k, "")))
        for k, var in self.b.items():
            var.set(bool(cfg[k]))
        for m, var in self.mode_vars.items():
            var.set(m in cfg["modes"])
        for r, var in self.radio_vars.items():
            var.set(r in cfg["radios"])
        for bw, var in self.bw_vars.items():
            var.set(bw in cfg["bandwidths"])
        for pm, var in self.phy_vars.items():
            var.set(pm in cfg["phy_modes"])

    def collect_config(self):
        cfg = dict(self.cfg)
        ints = ("port", "dfs_wait", "assoc_timeout", "ping_count", "ping_size", "retries",
                "settle_time", "reconnect_timeout")
        for k, var in self.v.items():
            val = var.get().strip()
            if k in ints:
                try:
                    val = int(val)
                except ValueError:
                    raise ValueError("%s must be an integer" % k)
            elif k == "max_loss":
                try:
                    val = float(val)
                except ValueError:
                    raise ValueError("max_loss must be a number")
            cfg[k] = val
        for k, var in self.b.items():
            cfg[k] = bool(var.get())
        cfg["modes"] = [m for m in MODES if self.mode_vars[m].get()]
        cfg["radios"] = [r for r in RADIOS if self.radio_vars[r].get()]
        cfg["bandwidths"] = [bw for bw in BANDWIDTHS if self.bw_vars[bw].get()]
        cfg["phy_modes"] = [pm for pm in PHY_MODES if self.phy_vars[pm].get()]
        errs = validate(cfg)
        if errs:
            raise ValueError("\n".join(errs))
        self.cfg = cfg
        return cfg

    # -------------------------------------------------------------------- ui
    def _build_ui(self):
        style = ttk.Style()
        if "clam" in style.theme_names():
            style.theme_use("clam")
        # clam uses a fixed ~20px Treeview row height, which clips text on HiDPI displays;
        # size rows from the actual font line height instead.
        linespace = tkfont.nametofont("TkDefaultFont").metrics("linespace")
        style.configure("Treeview", rowheight=linespace + 6)
        style.configure("Treeview.Heading", padding=(4, 3))
        self._style_checkbuttons(style, max(12, int(linespace * 0.6)))
        # Column widths below assume a ~17px line height (9pt at 96 DPI); scale them to the font.
        self.ui_scale = max(1.0, linespace / 17.0)
        style.configure("Pass.TLabel", foreground="#1b7f2a")
        style.configure("Fail.TLabel", foreground="#b3261e")

        menubar = tk.Menu(self.root)
        fm = tk.Menu(menubar, tearoff=0)
        fm.add_command(label="Load config...", command=self.load_cfg)
        fm.add_command(label="Save config...", command=self.save_cfg)
        fm.add_separator()
        fm.add_command(label="Open results folder", command=self.open_results_dir)
        fm.add_separator()
        fm.add_command(label="Quit", command=self.on_close)
        menubar.add_cascade(label="File", menu=fm)
        hm = tk.Menu(menubar, tearoff=0)
        hm.add_command(label="About", command=self.show_about)
        menubar.add_cascade(label="Help", menu=hm)
        self.root.config(menu=menubar)

        outer = ttk.Frame(self.root, padding=6)
        outer.pack(fill="both", expand=True)

        top = ttk.Frame(outer)
        top.pack(fill="x")
        self._build_server_frame(top).pack(side="left", fill="y", padx=(0, 6))
        self._build_dut_frame(top).pack(side="left", fill="both", expand=True)

        self._build_test_frame(outer).pack(fill="x", pady=6)
        self._build_control_frame(outer).pack(fill="x")

        nb = ttk.Notebook(outer)
        nb.pack(fill="both", expand=True, pady=(6, 0))
        self.nb = nb
        self.results_tv = self._make_tree(nb, RESULT_COLS, "Results")
        self.results_tv.tag_configure("PASS", background="#e3f4e5")
        self.results_tv.tag_configure("FAIL", background="#fbe3e1")
        self.results_tv.bind("<Double-1>", self._show_result_detail)
        self.plan_tv = self._make_tree(nb, PLAN_COLS, "Plan")
        logf = ttk.Frame(nb)
        self.log_text = ScrolledText(logf, height=10, wrap="none", font=("TkFixedFont", 9))
        self.log_text.pack(fill="both", expand=True)
        self.log_text.configure(state="disabled")
        nb.add(logf, text="Log")

        self.status = tk.StringVar(value="ready")
        ttk.Label(outer, textvariable=self.status, anchor="w", relief="sunken").pack(fill="x", pady=(4, 0))

    def _style_checkbuttons(self, style, size):
        """Replace clam's 'X' indicator with a rounded box + tick mark, sized to the font."""
        if "Tick.indicator" in style.element_names():
            return
        bg = style.lookup("TCheckbutton", "background") or "#dcdad5"
        img = {name: _checkbox_image(self.root, size, bg, fill, border, tick)
               for name, (fill, border, tick) in CHECKBOX_STATES.items()}
        self._checkbox_images = img  # keep references, Tk does not
        style.element_create(
            "Tick.indicator", "image", img["off"],
            ("disabled", "selected", img["on_disabled"]), ("disabled", img["off_disabled"]),
            ("selected", "pressed", img["on_hover"]), ("selected", "active", img["on_hover"]),
            ("selected", img["on"]), ("pressed", img["off_hover"]), ("active", img["off_hover"]),
            width=size + max(6, size // 3), sticky="w")
        style.layout("TCheckbutton", [("Checkbutton.padding", {"sticky": "nswe", "children": [
            ("Tick.indicator", {"side": "left", "sticky": ""}),
            ("Checkbutton.focus", {"side": "left", "sticky": "w",
                                   "children": [("Checkbutton.label", {"sticky": "nswe"})]})]})])

    def _build_server_frame(self, parent):
        f = ttk.LabelFrame(parent, text="Controller server", padding=6)
        for r, (label, key, w) in enumerate((("Listen", "listen", 14), ("Port", "port", 7),
                                             ("Token", "token", 14))):
            ttk.Label(f, text=label).grid(row=r, column=0, sticky="w")
            ttk.Entry(f, textvariable=self.v[key], width=w,
                      show="*" if key == "token" else "").grid(row=r, column=1, sticky="w", pady=1)
        bf = ttk.Frame(f)
        bf.grid(row=3, column=0, columnspan=2, pady=(6, 0), sticky="w")
        self.btn_srv_start = ttk.Button(bf, text="Start server", command=self.start_server)
        self.btn_srv_start.pack(side="left")
        self.btn_srv_stop = ttk.Button(bf, text="Stop", command=self.stop_server)
        self.btn_srv_stop.pack(side="left", padx=4)
        self.srv_state = tk.StringVar(value="stopped")
        ttk.Label(f, textvariable=self.srv_state).grid(row=4, column=0, columnspan=2, sticky="w", pady=(4, 0))
        return f

    def _build_dut_frame(self, parent):
        f = ttk.LabelFrame(parent, text="Connected DUTs", padding=6)
        tv = ttk.Treeview(f, columns=[c[0] for c in DUT_COLS], show="headings", height=4)
        for key, title, width in DUT_COLS:
            tv.heading(key, text=title)
            tv.column(key, width=self._px(width), anchor="w")
        tv.grid(row=0, column=0, columnspan=6, sticky="nsew")
        f.columnconfigure(5, weight=1)
        self.dut_tv = tv
        ttk.Label(f, text="DUT A (AP / mesh 1)").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.cb_a = ttk.Combobox(f, textvariable=self.v["dut_a"], width=18)
        self.cb_a.grid(row=1, column=1, sticky="w", pady=(6, 0))
        ttk.Label(f, text="DUT B (STA / mesh 2)").grid(row=1, column=2, sticky="w", padx=(12, 0), pady=(6, 0))
        self.cb_b = ttk.Combobox(f, textvariable=self.v["dut_b"], width=18)
        self.cb_b.grid(row=1, column=3, sticky="w", pady=(6, 0))
        ttk.Button(f, text="Refresh info", command=self.refresh_dut_info).grid(
            row=1, column=4, padx=(12, 0), pady=(6, 0))
        return f

    def _build_test_frame(self, parent):
        f = ttk.LabelFrame(parent, text="Test selection", padding=6)

        col = ttk.Frame(f)
        col.grid(row=0, column=0, sticky="nw", padx=(0, 16))
        ttk.Label(col, text="Modes", font=("TkDefaultFont", 9, "bold")).pack(anchor="w")
        for m in MODES:
            ttk.Checkbutton(col, text=MODE_LABELS[m], variable=self.mode_vars[m]).pack(anchor="w")
        ttk.Label(col, text="Radios", font=("TkDefaultFont", 9, "bold")).pack(anchor="w", pady=(6, 0))
        rf = ttk.Frame(col)
        rf.pack(anchor="w")
        for r in RADIOS:
            ttk.Checkbutton(rf, text=r, variable=self.radio_vars[r]).pack(side="left")

        col = ttk.Frame(f)
        col.grid(row=0, column=1, sticky="nw", padx=(0, 16))
        ttk.Label(col, text="Bandwidths", font=("TkDefaultFont", 9, "bold")).pack(anchor="w")
        notes = {"HT5": " (2.4G)", "HT10": " (2.4G)", "HT80": " (5G)"}
        for bw in BANDWIDTHS:
            ttk.Checkbutton(col, text=bw + notes.get(bw, ""), variable=self.bw_vars[bw]).pack(anchor="w")
        ttk.Label(col, text="PHY modes", font=("TkDefaultFont", 9, "bold")).pack(anchor="w", pady=(6, 0))
        pf = ttk.Frame(col)
        pf.pack(anchor="w")
        for i, pm in enumerate(PHY_MODES):
            ttk.Checkbutton(pf, text=PHY_LABELS[pm], variable=self.phy_vars[pm]).grid(
                row=i // 2, column=i % 2, sticky="w", padx=(0, 8))

        col = ttk.Frame(f)
        col.grid(row=0, column=2, sticky="nw", padx=(0, 16))
        rows = (("2.4G channels", "channels_2g", 18), ("5G channels", "channels_5g", 18),
                ("Country", "country", 6),
                ("Encryption", "encryption", None), ("Key", "key", 18), ("Test subnet", "subnet", 14))
        for r, (label, key, w) in enumerate(rows):
            ttk.Label(col, text=label).grid(row=r, column=0, sticky="w")
            if key == "encryption":
                wdg = ttk.Combobox(col, textvariable=self.v[key], values=ENCRYPTIONS, width=8, state="readonly")
            else:
                wdg = ttk.Entry(col, textvariable=self.v[key], width=w, show="*" if key == "key" else "")
            wdg.grid(row=r, column=1, sticky="w", pady=1)

        col = ttk.Frame(f)
        col.grid(row=0, column=3, sticky="nw", padx=(0, 16))
        rows = (("Assoc timeout s", "assoc_timeout"), ("DFS CAC wait s", "dfs_wait"),
                ("Ping count", "ping_count"), ("Ping size", "ping_size"),
                ("Max loss %", "max_loss"), ("Retries", "retries"), ("Settle s", "settle_time"),
                ("Reconnect wait s", "reconnect_timeout"))
        for r, (label, key) in enumerate(rows):
            ttk.Label(col, text=label).grid(row=r, column=0, sticky="w")
            ttk.Entry(col, textvariable=self.v[key], width=7).grid(row=r, column=1, sticky="w", pady=1)

        col = ttk.Frame(f)
        col.grid(row=0, column=4, sticky="nw")
        for key, label in (("include_dfs", "Include DFS channels"),
                           ("swap_roles", "Swap AP/STA roles too"),
                           ("bidirectional", "Bidirectional ping"),
                           ("verify_width", "Fail on width mismatch"),
                           ("restore_at_end", "Restore DUT config at end"),
                           ("auto_start", "Auto-start when DUTs connect")):
            ttk.Checkbutton(col, text=label, variable=self.b[key]).pack(anchor="w")
        of = ttk.Frame(col)
        of.pack(anchor="w", pady=(6, 0))
        ttk.Label(of, text="CSV").pack(side="left")
        ttk.Entry(of, textvariable=self.v["output"], width=34).pack(side="left", padx=4)
        ttk.Button(of, text="...", width=3, command=self.browse_output).pack(side="left")
        return f

    def _build_control_frame(self, parent):
        f = ttk.Frame(parent)
        self.btn_preview = ttk.Button(f, text="Preview plan", command=self.preview_plan)
        self.btn_preview.pack(side="left")
        self.btn_start = ttk.Button(f, text="Start tests", command=self.start_tests)
        self.btn_start.pack(side="left", padx=4)
        self.btn_pause = ttk.Button(f, text="Pause", command=self.toggle_pause, state="disabled")
        self.btn_pause.pack(side="left")
        self.btn_stop = ttk.Button(f, text="Stop", command=self.stop_tests, state="disabled")
        self.btn_stop.pack(side="left", padx=4)
        self.progress = ttk.Progressbar(f, mode="determinate", length=300)
        self.progress.pack(side="left", padx=12, fill="x", expand=True)
        self.prog_text = tk.StringVar(value="0 / 0")
        ttk.Label(f, textvariable=self.prog_text, width=10).pack(side="left")
        self.pass_text = tk.StringVar(value="PASS 0")
        self.fail_text = tk.StringVar(value="FAIL 0")
        ttk.Label(f, textvariable=self.pass_text, style="Pass.TLabel", width=9).pack(side="left")
        ttk.Label(f, textvariable=self.fail_text, style="Fail.TLabel", width=9).pack(side="left")
        return f

    def _px(self, width):
        return int(width * self.ui_scale)

    def _make_tree(self, nb, cols, title):
        frame = ttk.Frame(nb)
        tv = ttk.Treeview(frame, columns=[c[0] for c in cols], show="headings")
        for key, text, width in cols:
            tv.heading(key, text=text)
            tv.column(key, width=self._px(width), anchor="w", stretch=(key == "reason"))
        ys = ttk.Scrollbar(frame, orient="vertical", command=tv.yview)
        xs = ttk.Scrollbar(frame, orient="horizontal", command=tv.xview)
        tv.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        tv.grid(row=0, column=0, sticky="nsew")
        ys.grid(row=0, column=1, sticky="ns")
        xs.grid(row=1, column=0, sticky="ew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        nb.add(frame, text=title)
        return tv

    # --------------------------------------------------------------- events
    def emit(self, kind, **kw):
        """Thread-safe: called from server/runner threads."""
        self.events.put((kind, kw))

    def _poll_events(self):
        try:
            while True:
                kind, kw = self.events.get_nowait()
                self._handle_event(kind, kw)
        except queue.Empty:
            pass
        self.root.after(100, self._poll_events)

    def _handle_event(self, kind, kw):
        if kind == "log":
            self.log(kw["msg"])
        elif kind == "server":
            self.srv_state.set("listening on %s:%s" % (self.server.host, self.server.port)
                               if kw["running"] else "stopped")
        elif kind in ("dut_connected", "dut_disconnected", "dut_info"):
            self.refresh_duts()
            if kind == "dut_info":
                self._maybe_auto_start()
        elif kind == "state":
            self._set_state(kw["state"])
        elif kind == "test_start":
            tc = kw["test"]
            self.status.set("running %d/%d: %s" % (kw["index"], kw["total"], tc.label()))
        elif kind == "test_result":
            row = kw["row"]
            self.result_rows.append(row)
            self.results_tv.insert("", "end", values=[row.get(c[0], "") for c in RESULT_COLS],
                                   tags=(row["result"],))
            self.results_tv.yview_moveto(1.0)
            self.progress["value"] = kw["index"]
            self.prog_text.set("%d / %d" % (kw["index"], kw["total"]))
            self.pass_text.set("PASS %d" % kw["passed"])
            self.fail_text.set("FAIL %d" % kw["failed"])
        elif kind == "finished":
            how = "aborted: %s" % kw["aborted"] if kw["aborted"] else ("stopped" if kw["stopped"] else "completed")
            msg = "Run %s - %d/%d executed, %d PASS, %d FAIL\nCSV: %s" % (
                how, kw["executed"], kw["total"], kw["passed"], kw["failed"], kw["csv"])
            self.log(msg.replace("\n", " | "))
            self.status.set(msg.splitlines()[0])
            self.runner = None
            (messagebox.showwarning if kw["aborted"] else messagebox.showinfo)("iw-test-controller", msg)

    def log(self, msg):
        line = "%s  %s\n" % (time.strftime("%H:%M:%S"), msg)
        self.log_text.configure(state="normal")
        self.log_text.insert("end", line)
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _set_state(self, state):
        running = state in ("running", "paused")
        self.btn_start.configure(state="disabled" if running else "normal")
        self.btn_preview.configure(state="disabled" if running else "normal")
        self.btn_stop.configure(state="normal" if running else "disabled")
        self.btn_pause.configure(state="normal" if running else "disabled",
                                 text="Resume" if state == "paused" else "Pause")
        if state == "paused":
            self.status.set("paused")

    # ---------------------------------------------------------------- server
    def start_server(self):
        if self.server and self.server.running:
            return
        try:
            port = int(self.v["port"].get())
        except ValueError:
            messagebox.showerror("iw-test-controller", "port must be an integer")
            return
        self.server = ControllerServer(self.v["listen"].get().strip() or "0.0.0.0", port,
                                       self.v["token"].get(), emit=self.emit)
        try:
            self.server.start()
        except OSError as e:
            messagebox.showerror("iw-test-controller", "cannot listen on port %d: %s" % (port, e))
            self.server = None

    def stop_server(self):
        if self.runner:
            messagebox.showwarning("iw-test-controller", "stop the running tests first")
            return
        if self.server:
            self.server.stop()
        self.refresh_duts()

    def refresh_duts(self):
        self.dut_tv.delete(*self.dut_tv.get_children())
        names = self.server.names() if self.server else []
        for n in names:
            c = self.server.get(n)
            if c:
                s = c.summary()
                self.dut_tv.insert("", "end", values=[s[k[0]] for k in DUT_COLS])
        self.cb_a["values"] = names
        self.cb_b["values"] = names
        if len(names) >= 1 and self.v["dut_a"].get() not in names:
            self.v["dut_a"].set(names[0])
        if len(names) >= 2 and self.v["dut_b"].get() not in names:
            self.v["dut_b"].set(next(n for n in names if n != self.v["dut_a"].get()))

    def _refresh_duts_periodic(self):
        if self.server:
            for item, n in zip(self.dut_tv.get_children(), self.server.names()):
                c = self.server.get(n)
                if c:
                    self.dut_tv.set(item, "busy", c.busy)
        self.root.after(1000, self._refresh_duts_periodic)

    def refresh_dut_info(self):
        if not self.server:
            return
        for n in self.server.names():
            c = self.server.get(n)
            if c:
                threading.Thread(target=c._fetch_info, daemon=True).start()

    # ----------------------------------------------------------------- tests
    def _resolve_duts(self, cfg):
        names = self.server.ready_names() if self.server else []
        a, b = cfg["dut_a"], cfg["dut_b"]
        if not a or not b:
            raise ValueError("select DUT A and DUT B (connected: %s)" % (", ".join(names) or "none"))
        ca, cb = self.server.get(a), self.server.get(b)
        if not ca or not cb:
            raise ValueError("DUT A/B must be connected (connected: %s)" % (", ".join(names) or "none"))
        if ca is cb:
            raise ValueError("DUT A and DUT B must be different")
        if not ca.radios or not cb.radios:
            raise ValueError("radio info not yet received from the DUTs")
        return ca, cb

    def _make_plan(self):
        cfg = self.collect_config()
        ca, cb = self._resolve_duts(cfg)
        plan, notes = build_plan(cfg, ca.name, cb.name, ca.radios, cb.radios)
        self.plan_tv.delete(*self.plan_tv.get_children())
        for tc in plan:
            d = tc.as_dict()
            d["dut1"] = "%s (%s)" % (tc.dut1, tc.role1)
            d["dut2"] = "%s (%s)" % (tc.dut2, tc.role2)
            d["dfs"] = "yes" if tc.dfs else ""
            self.plan_tv.insert("", "end", values=[d[c[0]] for c in PLAN_COLS])
        for n in notes:
            self.log("plan: skipped %s" % n)
        return cfg, ca, cb, plan, notes

    def preview_plan(self):
        try:
            cfg, ca, cb, plan, notes = self._make_plan()
        except ValueError as e:
            messagebox.showerror("iw-test-controller", str(e))
            return
        self.nb.select(1)
        est = sum(self._estimate(cfg, tc) for tc in plan)
        self.status.set("plan: %d tests, %d skipped combinations, ~%d min" % (len(plan), len(notes), est // 60))

    @staticmethod
    def _estimate(cfg, tc):
        base = 25 + cfg["ping_count"] * (2 if cfg["bidirectional"] else 1) + cfg["settle_time"]
        return base + (60 if tc.dfs else 0)

    def start_tests(self):
        if self.runner:
            return
        try:
            cfg, ca, cb, plan, notes = self._make_plan()
        except ValueError as e:
            messagebox.showerror("iw-test-controller", str(e))
            return
        if not plan:
            messagebox.showerror("iw-test-controller", "the plan is empty - check the Log tab for skipped combinations")
            return
        self.results_tv.delete(*self.results_tv.get_children())
        self.result_rows = []
        self.progress["maximum"] = len(plan)
        self.progress["value"] = 0
        self.prog_text.set("0 / %d" % len(plan))
        self.pass_text.set("PASS 0")
        self.fail_text.set("FAIL 0")
        csv_path = output_path(cfg)
        self.runner = TestRunner(self.server, cfg, plan, ca.name, cb.name, csv_path, self.emit)
        self.log("starting %d tests: DUT A=%s DUT B=%s" % (len(plan), ca.name, cb.name))
        self.runner.start()
        self.nb.select(0)

    def _maybe_auto_start(self):
        if not self.b["auto_start"].get() or self.runner or self.auto_started:
            return
        if len(self.server.ready_names()) < 2:
            return
        self.refresh_duts()
        self.auto_started = True
        self.log("auto-start: DUTs connected")
        self.start_tests()

    def toggle_pause(self):
        if not self.runner:
            return
        if self.runner.paused:
            self.runner.resume()
        else:
            self.runner.pause()

    def stop_tests(self):
        if self.runner and messagebox.askyesno("iw-test-controller", "Stop after the current test and restore DUTs?"):
            self.runner.stop()
            self.status.set("stopping after current test...")

    def _show_result_detail(self, _event):
        sel = self.results_tv.selection()
        if not sel:
            return
        idx = self.results_tv.index(sel[0])
        if idx < len(self.result_rows):
            row = self.result_rows[idx]
            messagebox.showinfo("Test %s" % row["test_id"], "\n".join("%s: %s" % kv for kv in row.items()))

    # ----------------------------------------------------------------- misc
    def browse_output(self):
        path = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV", "*.csv")])
        if path:
            self.v["output"].set(path)

    def load_cfg(self):
        path = filedialog.askopenfilename(filetypes=[("JSON", "*.json")])
        if path:
            try:
                self.cfg = load_config(path)
                self._load_vars(self.cfg)
                self.log("loaded config %s" % path)
            except (OSError, ValueError) as e:
                messagebox.showerror("iw-test-controller", str(e))

    def save_cfg(self):
        try:
            cfg = self.collect_config()
        except ValueError as e:
            messagebox.showerror("iw-test-controller", str(e))
            return
        path = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON", "*.json")])
        if path:
            save_config(cfg, path)
            self.log("saved config %s" % path)

    def open_results_dir(self):
        d = os.path.dirname(os.path.abspath(output_path(self.cfg)))
        os.makedirs(d, exist_ok=True)
        opener = {"darwin": "open", "win32": "explorer"}.get(sys.platform, "xdg-open")
        try:
            subprocess.Popen([opener, d])
        except OSError as e:
            messagebox.showerror("iw-test-controller", str(e))

    def show_about(self):
        win = tk.Toplevel(self.root)
        win.title("About iw-test-controller")
        win.transient(self.root)
        win.resizable(False, False)
        f = ttk.Frame(win, padding=16)
        f.pack(fill="both", expand=True)
        try:
            win.avatar = tk.PhotoImage(file=AVATAR_PATH)
            ttk.Label(f, image=win.avatar).pack(pady=(0, 10))
        except tk.TclError:
            pass
        ttk.Label(f, text="iw-test-controller %s" % __version__, font=("TkDefaultFont", 12, "bold")).pack()
        ttk.Label(f, text='Developed by "%s"' % AUTHOR).pack(pady=(6, 8))
        for label, url in ABOUT_LINKS:
            row = ttk.Frame(f)
            row.pack(anchor="w")
            ttk.Label(row, text="%s: " % label).pack(side="left")
            link = tk.Label(row, text=url, fg="#1a5fb4", cursor="hand2", font=("TkDefaultFont", 9, "underline"))
            link.pack(side="left")
            link.bind("<Button-1>", lambda _e, u=url: webbrowser.open(u))
        ttk.Button(f, text="Close", command=win.destroy).pack(pady=(12, 0))
        win.bind("<Escape>", lambda _e: win.destroy())
        win.update_idletasks()
        win.geometry("+%d+%d" % (self.root.winfo_rootx() + (self.root.winfo_width() - win.winfo_width()) // 2,
                                 self.root.winfo_rooty() + (self.root.winfo_height() - win.winfo_height()) // 3))
        win.grab_set()

    def on_close(self):
        if self.runner and not messagebox.askyesno("iw-test-controller", "Tests are running. Stop and quit?"):
            return
        if self.runner:
            self.runner.stop()
            self.runner.join(timeout=5)
        if self.server:
            self.server.stop()
        self.root.destroy()


def run_gui(cfg):
    root = tk.Tk()
    ControllerApp(root, cfg)
    root.mainloop()
    return 0
