"""
hello_tune-player GUI (tkinter).

A dark, SGI-flavored graphical front end:

* searchable catalog of every machine + tune
* live waveform preview with progress + playhead
* volume / normalization / peak / seed controls
* WAV export (16/24-bit) and batch export-all
* boot/shutdown power-cycle simulator
* keyboard shortcuts and persistent settings

Run with:  hellotune gui
"""

from __future__ import annotations

import json
import os
import queue
import random
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Dict, List, Optional, Tuple

from . import __version__
from .players import Playback, PlaybackError, available_backends, temp_wav_path
from .render import RenderOptions, RenderResult, format_analysis, render_tune
from .tunes import (KIND_LABELS, MACHINES, TUNES, Tune, default_tune,
                    find_tune, tunes_for)
from .wavio import write_wav

# ---------------------------------------------------------------------------
# theme
# ---------------------------------------------------------------------------

BG = "#12121a"          # window background
PANEL = "#1a1a26"        # panels / list background
FIELD = "#232335"        # entry / combobox background
FG = "#e8e8f2"           # main text
DIM = "#8b90a8"          # secondary text
ACCENT = "#41d6ff"       # SGI-ish cyan
ACCENT2 = "#ff5fd2"      # magenta
WAVE = "#37d2ff"
PLAYHEAD = "#ff5fd2"
KIND_COLORS = {
    "boot": "#41d6ff",
    "shutdown": "#ff5fd2",
    "badgfx": "#ff8a4b",
    "variant": "#9dff5f",
}


def _db_to_peak(db: float) -> float:
    return 10.0 ** (db / 20.0)


def _peak_to_db(peak: float) -> float:
    import math
    return 20.0 * math.log10(max(peak, 1e-6))


# ---------------------------------------------------------------------------
# app
# ---------------------------------------------------------------------------

class HelloTuneApp(tk.Tk):

    APP_DIR = os.path.join(
        os.environ.get("XDG_CONFIG_HOME",
                       os.path.expanduser("~/.config")),
        "hellotune")
    SETTINGS = os.path.join(APP_DIR, "settings.json")

    def __init__(self):
        super().__init__()
        self.title("hello_tune-player -- SGI PROM chimes")
        self.configure(bg=BG)
        self.geometry("1080x680")

        # state
        self.tunes: List[Tune] = list(TUNES)
        self.selected: Optional[Tune] = None
        self.result: Optional[RenderResult] = None
        self.render_cache: Dict[Tuple, RenderResult] = {}
        self.render_queue: "queue.Queue[Tuple[str, object]]" = queue.Queue()
        self.render_thread: Optional[threading.Thread] = None
        self.playback = Playback()
        self.play_wav: Optional[str] = None
        self.play_started: Optional[float] = None
        self.play_seconds = 0.0
        self._boot_sim_running = False
        self._closing = False

        self._load_settings()
        self._build_style()
        self._build_ui()
        self._populate()
        self._select_initial()
        self.after(60, self._poll)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------ style

    def _build_style(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(".", background=PANEL, foreground=FG,
                        fieldbackground=FIELD, bordercolor=PANEL,
                        lightcolor=PANEL, darkcolor=PANEL,
                        troughcolor=FIELD, arrowcolor=ACCENT)
        style.configure("TButton", background=FIELD, foreground=FG,
                        padding=(10, 5))
        style.map("TButton",
                  background=[("active", "#2e2e4a"), ("pressed", "#27273d")],
                  foreground=[("active", ACCENT)])
        style.configure("Toolbar.TButton", font=("Helvetica", 10, "bold"))
        style.configure("TLabel", background=PANEL, foreground=DIM)
        style.configure("TCheckbutton", background=PANEL, foreground=FG)
        style.map("TCheckbutton", background=[("active", PANEL)])
        style.configure("TScale", background=PANEL)
        style.configure("Treeview", background=PANEL, foreground=FG,
                        fieldbackground=PANEL, rowheight=24,
                        borderwidth=0)
        style.configure("Treeview.Heading", background=FIELD, foreground=ACCENT,
                        font=("Helvetica", 10, "bold"))
        style.map("Treeview", background=[("selected", "#2b3a55")],
                  foreground=[("selected", "#ffffff")])
        style.configure("TProgressbar", troughcolor=FIELD,
                        background=ACCENT, borderwidth=0)
        style.configure("TCombobox", fieldbackground=FIELD,
                        background=FIELD, foreground=FG)
        style.configure("TNotebook", background=PANEL, borderwidth=0)
        style.configure("TNotebook.Tab", background=FIELD, foreground=DIM,
                        padding=(12, 5))
        style.map("TNotebook.Tab", foreground=[("selected", ACCENT)])

    # -------------------------------------------------------------------- ui

    def _build_ui(self):
        # -- toolbar ---------------------------------------------------------
        bar = tk.Frame(self, bg=PANEL)
        bar.pack(fill="x", padx=8, pady=(8, 4))

        self.btn_play = ttk.Button(bar, text="\u25b6 Play", style="Toolbar.TButton",
                                   command=self._on_play, width=9)
        self.btn_play.pack(side="left")
        ttk.Button(bar, text="\u25a0 Stop", style="Toolbar.TButton",
                   command=self._on_stop, width=9).pack(side="left", padx=(6, 0))
        ttk.Button(bar, text="\u21bb Re-roll", style="Toolbar.TButton",
                   command=self._on_reroll, width=9).pack(side="left", padx=(6, 0))
        ttk.Button(bar, text="\U0001f4be Export WAV\u2026", style="Toolbar.TButton",
                   command=self._on_export, width=16).pack(side="left", padx=(18, 0))
        ttk.Button(bar, text="\U0001f4e6 Export all\u2026", style="Toolbar.TButton",
                   command=self._on_export_all, width=14).pack(side="left", padx=(6, 0))
        ttk.Button(bar, text="\u23fb Boot sim", style="Toolbar.TButton",
                   command=self._on_boot_sim, width=11).pack(side="left", padx=(18, 0))
        ttk.Button(bar, text="About", style="Toolbar.TButton",
                   command=self._on_about, width=7).pack(side="right")

        # -- main split ------------------------------------------------------
        main = ttk.Panedwindow(self, orient="horizontal")
        main.pack(fill="both", expand=True, padx=8, pady=4)

        # left: catalog
        left = tk.Frame(main, bg=PANEL)
        main.add(left, weight=1)

        filt = tk.Frame(left, bg=PANEL)
        filt.pack(fill="x", padx=6, pady=(6, 2))
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self._populate())
        search_entry = tk.Entry(filt, textvariable=self.search_var,
                                bg=FIELD, fg=FG, insertbackground=ACCENT,
                                relief="flat", highlightthickness=1,
                                highlightbackground="#2c2c44",
                                highlightcolor=ACCENT)
        search_entry.pack(side="left", fill="x", expand=True, ipady=3)
        tk.Label(filt, text="  kind ", bg=PANEL, fg=DIM).pack(side="left")
        self.kind_var = tk.StringVar(value="all")
        self.kind_var.trace_add("write", lambda *_: self._populate())
        kindbox = ttk.Combobox(filt, textvariable=self.kind_var, state="readonly",
                               width=12, values=("all", "boot", "shutdown",
                                                 "badgfx", "variant"))
        kindbox.pack(side="left")

        cols = ("machine", "tune", "kind", "secs")
        self.tree = ttk.Treeview(left, columns=cols, show="headings",
                                 selectmode="browse")
        self.tree.heading("machine", text="Machine")
        self.tree.heading("tune", text="Tune")
        self.tree.heading("kind", text="Kind")
        self.tree.heading("secs", text="Length")
        self.tree.column("machine", width=110, anchor="w")
        self.tree.column("tune", width=190, anchor="w")
        self.tree.column("kind", width=110, anchor="w")
        self.tree.column("secs", width=60, anchor="e")
        self.tree.pack(fill="both", expand=True, padx=6, pady=(4, 6))
        self.tree.tag_configure("boot", foreground=KIND_COLORS["boot"])
        self.tree.tag_configure("shutdown", foreground=KIND_COLORS["shutdown"])
        self.tree.tag_configure("badgfx", foreground=KIND_COLORS["badgfx"])
        self.tree.tag_configure("variant", foreground=KIND_COLORS["variant"])
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._on_select())

        # right: details + waveform
        right = tk.Frame(main, bg=PANEL)
        main.add(right, weight=2)

        self.details = tk.Text(right, height=11, bg="#0d0d14", fg="#cfd6e4",
                               relief="flat", font=("Courier", 9),
                               state="disabled", wrap="none")
        self.details.pack(fill="x", padx=6, pady=(6, 4))

        self.wave = tk.Canvas(right, bg="#0d0d14", height=260,
                              highlightthickness=0)
        self.wave.pack(fill="both", expand=True, padx=6, pady=(0, 6))
        self.wave.bind("<Configure>", lambda e: self._draw_wave())

        self.progress = ttk.Progressbar(right, maximum=1.0)
        self.progress.pack(fill="x", padx=6, pady=(0, 8))

        # -- controls --------------------------------------------------------
        ctl = tk.Frame(self, bg=PANEL)
        ctl.pack(fill="x", padx=8, pady=(0, 2))

        tk.Label(ctl, text="Volume", bg=PANEL, fg=DIM).pack(side="left")
        self.vol_var = tk.DoubleVar(value=self.settings.get("volume", 100.0))
        vol = ttk.Scale(ctl, from_=0, to=100, variable=self.vol_var,
                        command=lambda v: self._on_volume_change(float(v)))
        vol.pack(side="left", padx=(6, 18))
        self.vol_label = tk.Label(ctl, text="100%", bg=PANEL, fg=ACCENT,
                                  width=4)
        self.vol_label.pack(side="left")

        self.norm_var = tk.BooleanVar(value=self.settings.get("normalize", True))
        norm = ttk.Checkbutton(ctl, text="Normalize",
                               variable=self.norm_var,
                               command=self._on_option_change)
        norm.pack(side="left", padx=(10, 6))

        tk.Label(ctl, text="Peak", bg=PANEL, fg=DIM).pack(side="left")
        self.peak_var = tk.DoubleVar(
            value=self.settings.get("peak_db", -1.5))
        peak = ttk.Scale(ctl, from_=-24, to=-0.5, variable=self.peak_var,
                         command=lambda v: self._on_peak_change(float(v)))
        peak.pack(side="left", padx=(6, 2))
        self.peak_label = tk.Label(ctl, text="-1.5 dB", bg=PANEL, fg=ACCENT)
        self.peak_label.pack(side="left", padx=(0, 14))

        tk.Label(ctl, text="Seed", bg=PANEL, fg=DIM).pack(side="left")
        self.seed_var = tk.StringVar(value=self.settings.get("seed", "random"))
        seed_entry = tk.Entry(ctl, textvariable=self.seed_var, width=9,
                              bg=FIELD, fg=ACCENT, relief="flat",
                              insertbackground=ACCENT,
                              highlightthickness=1,
                              highlightbackground="#2c2c44",
                              highlightcolor=ACCENT)
        seed_entry.pack(side="left", padx=(4, 2))
        seed_entry.bind("<Return>", lambda e: self._on_option_change())
        ttk.Button(ctl, text="\U0001f3b2", width=3,
                   command=self._on_reroll).pack(side="left", padx=(0, 10))

        tk.Label(ctl, text="WAV", bg=PANEL, fg=DIM).pack(side="left")
        self.bits_var = tk.StringVar(value=str(self.settings.get("bits", 16)))
        bitsbox = ttk.Combobox(ctl, textvariable=self.bits_var, width=4,
                               state="readonly", values=("16", "24"))
        bitsbox.pack(side="left", padx=(4, 14))

        tk.Label(ctl, text="Backend", bg=PANEL, fg=DIM).pack(side="left")
        backends = ["auto"] + [b for b in available_backends() if b != "custom"]
        self.backend_var = tk.StringVar(value=self.settings.get("backend", "auto"))
        backendbox = ttk.Combobox(ctl, textvariable=self.backend_var,
                                  values=tuple(backends), width=9,
                                  state="readonly")
        backendbox.pack(side="left", padx=(4, 0))

        # -- status bar ------------------------------------------------------
        status = tk.Frame(self, bg=BG)
        status.pack(fill="x", side="bottom")
        self.status_var = tk.StringVar(value="ready -- pick a chime, press Play "
                                             "(Space)")
        self.status = tk.Label(status, textvariable=self.status_var, anchor="w",
                               bg=BG, fg=DIM, font=("Helvetica", 9))
        self.status.pack(fill="x", side="left", padx=10, pady=3)
        self.clock_var = tk.StringVar(value="")
        self.clock = tk.Label(status, textvariable=self.clock_var, anchor="e",
                               bg=BG, fg=DIM, font=("Helvetica", 9))
        self.clock.pack(side="right", padx=10)

        # -- keys ------------------------------------------------------------
        self.bind("<space>", lambda e: self._on_play())
        self.bind("<Escape>", lambda e: self._on_stop())
        self.bind("r", lambda e: self._on_reroll())
        self.bind("e", lambda e: self._on_export())
        self.bind("b", lambda e: self._on_boot_sim())
        self.bind("<F1>", lambda e: self._on_about())
        self.bind("<Up>", self._on_arrow)
        self.bind("<Down>", self._on_arrow)

    # ------------------------------------------------------------ catalog ui

    def _populate(self):
        text = self.search_var.get().strip().lower()
        kind = self.kind_var.get()
        self.tree.delete(*self.tree.get_children())
        self._rowmap: Dict[str, Tune] = {}
        for m_id, m in MACHINES.items():
            for t in tunes_for(m_id):
                if kind != "all" and t.kind != kind:
                    continue
                hay = f"{t.id} {t.name} {m.name} {t.kind} {t.prom_name}".lower()
                if text and text not in hay:
                    continue
                tags = (t.kind,)
                iid = self.tree.insert("", "end", values=(
                    m.short, t.name, KIND_LABELS[t.kind], t.duration), tags=tags)
                self._rowmap[iid] = t

    def _select_initial(self):
        want = self.settings.get("selected")
        for iid, t in self._rowmap.items():
            if t.id == want:
                self.tree.selection_set(iid)
                self.tree.see(iid)
                return
        if self._rowmap:
            first = next(iter(self._rowmap))
            self.tree.selection_set(first)

    # -------------------------------------------------------------- options

    def _seed_value(self) -> Optional[int]:
        txt = self.seed_var.get().strip().lower()
        if txt in ("", "random", "rand", "?"):
            return None
        try:
            return int(txt) & 0xFFFFFFFF
        except ValueError:
            self.status_var.set(
                f"seed {txt!r} not a number -- using random seeds")
            return None

    def _render_opts(self, volume: Optional[float] = None) -> RenderOptions:
        return RenderOptions(
            seed=self._seed_value(),
            normalize=self.norm_var.get(),
            peak=_db_to_peak(self.peak_var.get()),
            volume=100.0 if volume is None else volume,  # applied post-render
        )

    # ------------------------------------------------------------ selection

    def _on_select(self):
        sel = self.tree.selection()
        if not sel:
            return
        tune = self._rowmap.get(sel[0])
        if tune is None or tune == self.selected:
            return
        self._load_tune(tune)

    def _load_tune(self, tune: Tune, keep_playing: bool = False):
        if not keep_playing:
            self._stop_playback()
        self.selected = tune
        self.settings["selected"] = tune.id
        self._set_details(format_analysis(tune))
        self._ensure_render(tune)

    def _set_details(self, text: str):
        self.details.configure(state="normal")
        self.details.delete("1.0", "end")
        self.details.insert("1.0", text)
        self.details.configure(state="disabled")

    # ------------------------------------------------------------- rendering

    def _cache_key(self, tune: Tune):
        return (tune.id, self._seed_value(), self.norm_var.get(),
                round(self.peak_var.get(), 2))

    def _ensure_render(self, tune: Tune) -> Optional[RenderResult]:
        """Return a cached render immediately, else start one in a thread."""
        key = self._cache_key(tune)
        hit = self.render_cache.get(key)
        if hit is not None:
            self.result = hit
            self._draw_wave()
            self.progress.configure(value=1.0)
            return hit
        if self.render_thread is not None and self.render_thread.is_alive():
            return None  # a render is already running; queue handled below
        self._start_render(tune)
        return None

    def _start_render(self, tune: Tune):
        opts = self._render_opts()
        self.progress.configure(value=0.0, mode="determinate")
        self.status_var.set(f"synthesizing {tune.name} \u2026")
        self.render_thread = threading.Thread(
            target=self._render_worker, args=(tune, opts), daemon=True)
        self.render_thread.start()

    def _render_worker(self, tune: Tune, opts: RenderOptions):
        try:
            res = render_tune(tune, opts, progress_cb=self._emit_progress)
            self.render_queue.put(("done", res))
        except Exception as e:  # pragma: no cover - surfaced in the UI
            self.render_queue.put(("error", e))

    def _emit_progress(self, frac: float):
        # called from the render thread
        self.render_queue.put(("progress", frac))

    def _poll(self):
        if self._closing:
            return
        try:
            while True:
                kind, payload = self.render_queue.get_nowait()
                if kind == "progress":
                    self.progress.configure(value=float(payload))
                elif kind == "done":
                    res: RenderResult = payload
                    key = (res.tune.id, res.seed
                           if res.tune.engine == "karplus" else 0,
                           self.norm_var.get(), round(self.peak_var.get(), 2))
                    # store under the *current* option key as the user may
                    # have nudged a control mid-render; re-key by id+options
                    self.render_cache[self._cache_key(res.tune)] = res
                    if self.selected and res.tune.id == self.selected.id:
                        self.result = res
                        self._draw_wave()
                        self.progress.configure(value=1.0)
                        self.status_var.set(
                            f"{res.tune.name} -- {res.seconds:.2f} s ready, "
                            f"seed {res.seed}")
                elif kind == "error":
                    self.status_var.set(f"render failed: {payload}")
        except queue.Empty:
            pass

        # playhead ticker
        if self.play_started is not None and self.result is not None:
            elapsed = time.monotonic() - self.play_started
            self._tick_playhead(elapsed)

        self.after(60, self._poll)

    def _on_volume_change(self, value: float):
        self.vol_label.configure(text=f"{int(round(value))}%")

    def _on_peak_change(self, value: float):
        self.peak_label.configure(text=f"{value:.1f} dB")

    def _on_option_change(self):
        # normalize/peak/seed changed -> refresh render if needed
        if self.selected is not None:
            self._ensure_render(self.selected)

    def _on_reroll(self):
        self.seed_var.set(str(random.randrange(1 << 32)))
        if self.selected is not None:
            self._start_render(self.selected)
            self.status_var.set("re-rolled the boot noise \u2026")

    # ------------------------------------------------------------- waveform

    def _draw_wave(self):
        c = self.wave
        c.delete("all")
        w = max(int(c.winfo_width()), 10)
        h = max(int(c.winfo_height()), 10)
        mid = h // 2
        c.create_line(0, mid, w, mid, fill="#22223a")
        res = self.result
        if res is None or not res.mono:
            c.create_text((w // 2, mid - 16), text="no render yet",
                          fill=DIM, font=("Helvetica", 10))
            return
        mono = res.mono
        n = len(mono)
        step = max(1, n // w)
        pts_min, pts_max = [], []
        for x in range(0, w):
            i0 = (x * n) // w
            i1 = min(i0 + step, n)
            lo = 1.0
            hi = -1.0
            for v in mono[i0:i1]:
                if v < lo:
                    lo = v
                if v > hi:
                    hi = v
            if hi < lo:
                lo = hi = 0.0
            pts_min.append((x, mid - int(hi * (mid - 4))))
            pts_max.append((x, mid - int(lo * (mid - 4))))
        c.create_line(pts_max, fill=WAVE, width=1)
        c.create_line(pts_min, fill=WAVE, width=1)
        # machine tint stripe
        if self.selected is not None:
            color = KIND_COLORS.get(self.selected.kind, ACCENT)
            c.create_line(0, 2, w, 2, fill=color)
        self._wave_width = w
        self._draw_playhead(None)

    def _draw_playhead(self, frac: Optional[float]):
        c = self.wave
        c.delete("playhead")
        if frac is None:
            return
        w = getattr(self, "_wave_width", max(int(c.winfo_width()), 10))
        x = int(frac * w)
        c.create_line(x, 0, x, int(c.winfo_height()), fill=PLAYHEAD,
                      tags="playhead", width=2)

    # ------------------------------------------------------------- playback

    def _scaled_mono(self) -> Tuple[List[float], float]:
        """Apply the volume slider to the cached render (post-render gain)."""
        vol = max(0.0, min(100.0, self.vol_var.get())) / 100.0
        if self.result is None:
            return [], vol
        if vol >= 0.999:
            return self.result.mono, vol
        return [x * vol for x in self.result.mono], vol

    def _on_play(self):
        if self._boot_sim_running:
            return
        if self.play_started is not None:
            self._stop_playback()
            return
        res = self.result
        if res is None:
            if self.selected is not None:
                self._ensure_render(self.selected)
                self.status_var.set("still synthesizing \u2026 try again in a "
                                    "moment")
            return
        mono, vol = self._scaled_mono()
        if vol <= 0.0:
            self.status_var.set(
                "volume 0 -- the PROM would stay silent (authentic!)")
            return
        path = temp_wav_path()
        try:
            write_wav(path, mono, res.rate, res.channels, bits=16)
        except Exception as e:
            self.status_var.set(f"failed writing temp wav: {e}")
            return
        self.play_wav = path
        try:
            backend = self.backend_var.get()
            if backend == "auto":
                self.playback.play_file(path)
            else:
                self.playback.play_file(path, backend=backend)
        except PlaybackError as e:
            self.status_var.set(str(e))
            try:
                os.unlink(path)
            except OSError:
                pass
            self.play_wav = None
            return
        self.play_started = time.monotonic()
        self.play_seconds = len(mono) / float(res.rate)
        self.btn_play.configure(text="\u25a0 Stop")
        t = res.tune
        self.status_var.set(
            f"\u25b6 {t.name} [{t.id}] -- {self.play_seconds:.2f} s, "
            f"seed {res.seed}")

    def _stop_playback(self):
        self.playback.stop()
        if self.play_wav:
            try:
                os.unlink(self.play_wav)
            except OSError:
                pass
        self.play_wav = None
        self.play_started = None
        self.btn_play.configure(text="\u25b6 Play")
        self._draw_playhead(None)
        self.clock_var.set("")

    def _on_stop(self):
        if self._boot_sim_running:
            self._boot_sim_running = False
            self.status_var.set("boot sim cancelled")
        self._stop_playback()

    def _tick_playhead(self, elapsed: float):
        frac = elapsed / max(self.play_seconds, 1e-9)
        if frac >= 1.0:
            if not self.playback.is_playing():
                self._stop_playback()
                return
            frac = 1.0
        self._draw_playhead(min(frac, 1.0))
        self.clock_var.set(
            f"{elapsed:5.1f} / {self.play_seconds:4.1f} s")

    # --------------------------------------------------------------- export

    def _on_export(self):
        if self.result is None:
            self.status_var.set("nothing rendered yet")
            return
        tune = self.result.tune
        bits = int(self.bits_var.get())
        mono, vol = self._scaled_mono()
        default = f"{tune.id.replace('/', '-')}-{bits}bit.wav"
        path = filedialog.asksaveasfilename(
            title="Export chime as WAV",
            defaultextension=".wav",
            initialfile=default,
            filetypes=[("WAV files", "*.wav"), ("All files", "*")])
        if not path:
            return
        try:
            n = write_wav(path, mono, self.result.rate,
                          self.result.channels, bits=bits)
        except Exception as e:
            messagebox.showerror("Export failed", str(e))
            return
        self.status_var.set(
            f"exported {path} ({self.result.seconds:.2f} s, "
            f"{'stereo' if self.result.channels == 2 else 'mono'}, "
            f"{bits}-bit, {n} bytes)")

    def _on_export_all(self):
        directory = filedialog.askdirectory(title="Export all tunes into\u2026")
        if not directory:
            return
        bits = int(self.bits_var.get())
        mono_gain = max(0.0, min(100.0, self.vol_var.get())) / 100.0
        seed = self._seed_value()
        normalize = self.norm_var.get()
        peak = _db_to_peak(self.peak_var.get())
        ok = 0
        try:
            for i, tune in enumerate(TUNES, 1):
                opts = RenderOptions(seed=seed, normalize=normalize,
                                     peak=peak, volume=100.0)
                res = render_tune(tune, opts)
                mono = (res.mono if mono_gain >= 0.999
                        else [x * mono_gain for x in res.mono])
                name = f"{tune.id.replace('/', '-')}-{bits}bit.wav"
                write_wav(os.path.join(directory, name), mono, res.rate,
                          res.channels, bits=bits)
                ok += 1
                self.status_var.set(f"export-all: {i}/{len(TUNES)} "
                                    f"({tune.id})")
                self.update_idletasks()
        except Exception as e:
            messagebox.showerror("Export failed", str(e))
            return
        self.status_var.set(f"exported {ok} tunes to {directory}")

    # ------------------------------------------------------------- boot sim

    def _on_boot_sim(self):
        if self._boot_sim_running:
            self._boot_sim_running = False
            self.status_var.set("boot sim cancelled")
            return
        machine = (self.selected.machine if self.selected else "ip32")
        m = MACHINES[machine]
        try:
            boot = default_tune(machine, "boot")
            stop = default_tune(machine, "shutdown")
        except KeyError as e:
            self.status_var.set(str(e))
            return
        self._boot_sim_running = True
        self.status_var.set(f"{m.name}: powering on \u2026")
        self._load_tune(boot)
        self.after(150, lambda: self._boot_sim_play(boot, stop))

    def _boot_sim_play(self, boot: Tune, stop: Tune):
        if not self._boot_sim_running:
            return
        if self.result is None or self.result.tune.id != boot.id:
            self.after(200, lambda: self._boot_sim_play(boot, stop))
            return
        self._on_play()
        self.after(int(self.play_seconds * 1000) + 1200,
                   lambda: self._boot_sim_wait(boot, stop))

    def _boot_sim_wait(self, boot: Tune, stop: Tune):
        if not self._boot_sim_running:
            return
        self._stop_playback()
        self.status_var.set("IRIX boots, you log in, you get some work "
                            "done \u2026")
        self.after(2600, lambda: self._boot_sim_off(boot, stop))

    def _boot_sim_off(self, boot: Tune, stop: Tune):
        if not self._boot_sim_running:
            return
        self.status_var.set(f"{MACHINES[stop.machine].name}: powering off "
                            f"\u2026")
        self._load_tune(stop)
        self.after(150, lambda: self._boot_sim_shutdown(stop))

    def _boot_sim_shutdown(self, stop: Tune):
        if not self._boot_sim_running:
            return
        if self.result is None or self.result.tune.id != stop.id:
            self.after(200, lambda: self._boot_sim_shutdown(stop))
            return
        self._on_play()
        self.after(int(self.play_seconds * 1000) + 800,
                   self._boot_sim_done)

    def _boot_sim_done(self):
        self._boot_sim_running = False
        self._stop_playback()
        self.status_var.set("halted. (press b to run the boot sim again)")

    # ----------------------------------------------------------------- misc

    def _on_arrow(self, event):
        sel = self.tree.selection()
        if not sel:
            return
        item = self.tree.prev(sel[0]) if event.keysym == "Up" \
            else self.tree.next(sel[0])
        if item:
            self.tree.selection_set(item)
            self.tree.see(item)

    def _on_about(self):
        messagebox.showinfo(
            "About hello_tune-player",
            "hello_tune-player " + __version__ + "\n\n"
            "Native Python resynthesis of the boot, shutdown and "
            "graphics-failure chimes of Silicon Graphics PROMs -- "
            "Indigo (R4000), Power Indigo2, Indigo2 Impact and O2.\n\n"
            "Engines:\n"
            "  \u2022 Karplus-Strong plucked string (IP26/IP28/IP32), "
            "ported from the PROM C sources\n"
            "  \u2022 Four-voice sine fugue (IP20), ported from the "
            "DSP56001 assembly\n\n"
            "The original hello_tune.c pluck synthesis and the "
            "'Hollywood Boot Tune' asm come from SGI's PROM tree; "
            "every boot sounded slightly different because the noise "
            "seed was uninitialized stack memory. Re-roll \u21bb for a "
            "fresh 'boot'.\n\n"
            "Keys: Space play/stop  Esc stop  R re-roll seed\n"
            "        E export  B boot sim  F1 about")

    # ------------------------------------------------------------- settings

    def _load_settings(self):
        self.settings: Dict = {}
        try:
            with open(self.SETTINGS) as f:
                self.settings = json.load(f)
        except (OSError, ValueError):
            pass
        geo = self.settings.get("geometry")
        if geo:
            try:
                self.geometry(geo)
            except tk.TclError:
                pass

    def _save_settings(self):
        self.settings.update(
            volume=self.vol_var.get(),
            normalize=self.norm_var.get(),
            peak_db=self.peak_var.get(),
            seed=self.seed_var.get(),
            bits=int(self.bits_var.get()),
            backend=self.backend_var.get(),
            selected=self.selected.id if self.selected else None,
            geometry=self.geometry(),
        )
        try:
            os.makedirs(self.APP_DIR, exist_ok=True)
            with open(self.SETTINGS, "w") as f:
                json.dump(self.settings, f, indent=2)
        except OSError:
            pass

    def _on_close(self):
        self._closing = True
        self._boot_sim_running = False
        self._stop_playback()
        self._save_settings()
        self.destroy()


def run_gui() -> int:
    try:
        app = HelloTuneApp()
        app.mainloop()
    except tk.TclError as e:
        print(f"hellotune: cannot start GUI: {e}", file=sys.stderr)
        print("hint: the GUI needs a display and python3-tk installed",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(run_gui())
