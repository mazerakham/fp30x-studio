"""FP-30X Studio — a small desktop recorder for the Roland FP-30X.

Press Record, play the piano, press Stop. It writes a MIDI file, renders it to
audio and plays it back.
"""

from __future__ import annotations

import queue
import threading
import time
import tkinter as tk
from datetime import datetime
from tkinter import ttk

from . import core

BG = "#0f1115"
CARD = "#171a21"
LINE = "#2a2f3a"
FG = "#e6e8ec"
DIM = "#9aa3b2"
ACC = "#6cb6ff"
OK = "#3fb950"
BAD = "#f85149"
WARN = "#d29922"

MONO = ("SF Mono", 11)
UI = ("SF Pro Text", 13)
UI_B = ("SF Pro Text", 13, "bold")
BIG = ("SF Pro Display", 30, "bold")


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("FP-30X Studio")
        self.configure(bg=BG)
        self.geometry("560x620")
        self.minsize(500, 560)

        self.events: queue.Queue = queue.Queue()
        self.capture: core.Capture | None = None
        self.player = core.Player()
        self.last_wav = None
        self.port = None
        self.note_total = 0
        self.t0 = None

        self._build()
        self._poll_device()
        self._drain()

    # ---------- layout ----------

    def _card(self, parent, pady=(0, 10)):
        f = tk.Frame(parent, bg=CARD, highlightbackground=LINE,
                     highlightthickness=1, bd=0)
        f.pack(fill="x", padx=16, pady=pady)
        return f

    def _build(self):
        tk.Label(self, text="FP-30X Studio", bg=BG, fg=FG,
                 font=("SF Pro Display", 20, "bold")).pack(anchor="w", padx=16, pady=(16, 2))
        tk.Label(self, text="Record what you play. It becomes a MIDI file and audio.",
                 bg=BG, fg=DIM, font=("SF Pro Text", 12)).pack(anchor="w", padx=16, pady=(0, 14))

        # device status
        c = self._card(self)
        self.dot = tk.Canvas(c, width=10, height=10, bg=CARD, highlightthickness=0)
        self.dot.pack(side="left", padx=(14, 8), pady=13)
        self.dot_id = self.dot.create_oval(1, 1, 9, 9, fill=BAD, outline="")
        self.status = tk.Label(c, text="looking for the piano...", bg=CARD, fg=DIM, font=UI)
        self.status.pack(side="left", pady=13)

        # counter
        c = self._card(self)
        self.timer = tk.Label(c, text="0:00", bg=CARD, fg=FG, font=BIG)
        self.timer.pack(side="left", padx=(16, 0), pady=(10, 6))
        self.counter = tk.Label(c, text="0 notes", bg=CARD, fg=DIM, font=UI)
        self.counter.pack(side="left", padx=12, pady=(22, 10))
        self.pedal = tk.Label(c, text="", bg=CARD, fg=ACC, font=UI_B)
        self.pedal.pack(side="right", padx=16, pady=(22, 10))

        # transport
        c = self._card(self)
        inner = tk.Frame(c, bg=CARD)
        inner.pack(fill="x", padx=14, pady=12)
        self.rec_btn = tk.Button(inner, text="Record", command=self.toggle_record,
                                 font=UI_B, bg=BAD, fg="white", relief="flat",
                                 activebackground="#c2352f", activeforeground="white",
                                 highlightthickness=0, bd=0, padx=22, pady=9,
                                 state="disabled")
        self.rec_btn.pack(side="left")
        self.play_btn = tk.Button(inner, text="Play", command=self.toggle_play,
                                  font=UI_B, bg="#222833", fg=FG, relief="flat",
                                  activebackground="#2c3441", activeforeground=FG,
                                  highlightthickness=0, bd=0, padx=22, pady=9,
                                  state="disabled")
        self.play_btn.pack(side="left", padx=8)
        self.reveal_btn = tk.Button(inner, text="Show files", command=self.reveal,
                                    font=UI, bg="#222833", fg=DIM, relief="flat",
                                    activebackground="#2c3441", activeforeground=FG,
                                    highlightthickness=0, bd=0, padx=16, pady=9)
        self.reveal_btn.pack(side="right")

        # live note feed
        tk.Label(self, text="LIVE", bg=BG, fg=DIM,
                 font=("SF Pro Text", 10, "bold")).pack(anchor="w", padx=18, pady=(6, 4))
        c = self._card(self, pady=(0, 10))
        self.feed = tk.Text(c, height=9, bg="#0b0d11", fg="#d7dce4", font=MONO,
                            relief="flat", highlightthickness=0, bd=0, padx=12, pady=10,
                            state="disabled", wrap="none")
        self.feed.pack(fill="both", expand=True, padx=1, pady=1)

        # takes
        tk.Label(self, text="TAKES", bg=BG, fg=DIM,
                 font=("SF Pro Text", 10, "bold")).pack(anchor="w", padx=18, pady=(4, 4))
        c = self._card(self, pady=(0, 16))
        self.takes = tk.Listbox(c, height=5, bg="#0b0d11", fg="#d7dce4", font=MONO,
                                relief="flat", highlightthickness=0, bd=0,
                                selectbackground="#243044", selectforeground=FG,
                                activestyle="none")
        self.takes.pack(fill="both", expand=True, padx=1, pady=1)
        self.takes.bind("<Double-Button-1>", self.play_selected)
        self.refresh_takes()

    # ---------- device ----------

    def _poll_device(self):
        # Never re-enumerate CoreMIDI while a take is open: creating a new
        # client mid-capture can disturb the input port we are reading from.
        if self.capture is not None:
            self.after(1500, self._poll_device)
            return
        port = core.find_port()
        if port != self.port:
            self.port = port
            if port:
                self.dot.itemconfig(self.dot_id, fill=OK)
                self.status.config(text=f"connected — {port}", fg=FG)
                if not self.capture:
                    self.rec_btn.config(state="normal")
            else:
                self.dot.itemconfig(self.dot_id, fill=BAD)
                self.status.config(
                    text="piano not found — pair Bluetooth MIDI in Audio MIDI Setup", fg=WARN)
                self.rec_btn.config(state="disabled")
        self.after(1500, self._poll_device)

    # ---------- transport ----------

    def toggle_record(self):
        if self.capture:
            self.stop_record()
        else:
            self.start_record()

    def start_record(self):
        if not self.port:
            return
        self.player.stop()
        self.note_total = 0
        self.counter.config(text="0 notes")
        self.feed.config(state="normal")
        self.feed.delete("1.0", "end")
        self.feed.config(state="disabled")
        self.capture = core.Capture(self.port, self.events)
        self.capture.start()
        self.rec_btn.config(text="Stop", bg="#c2352f")
        self.play_btn.config(state="disabled")
        self.t0 = time.time()
        self._tick()

    def _tick(self):
        if self.capture and self.t0:
            el = int(time.time() - self.t0)
            self.timer.config(text=f"{el // 60}:{el % 60:02d}")
            self.after(200, self._tick)

    def stop_record(self):
        cap, self.capture = self.capture, None
        if not cap:
            return
        cap.stop()
        cap.join(timeout=2)
        self.rec_btn.config(text="Record", bg=BAD)
        self.pedal.config(text="")

        if cap.note_count == 0:
            self.log("nothing captured — was the piano connected?")
            return

        stamp = datetime.now().strftime("%Y-%m-%d %H.%M.%S")
        mid = core.takes_dir() / f"{stamp}.mid"
        wav = core.takes_dir() / f"{stamp}.wav"
        cap.save_midi(mid)
        self.log(f"saved {mid.name} ({cap.note_count} notes) — rendering...")
        self.rec_btn.config(state="disabled")
        threading.Thread(target=self._render, args=(mid, wav), daemon=True).start()

    def _render(self, mid, wav):
        try:
            if not core.have_fluidsynth():
                self.events.put(("log", "fluidsynth missing — run: brew install fluid-synth"))
                return
            sf = core.ensure_soundfont(
                lambda f: self.events.put(("log", f"downloading instruments {f * 100:.0f}%")))
            core.render(mid, wav, sf)
            self.events.put(("rendered", wav))
        except Exception as exc:  # noqa: BLE001
            self.events.put(("log", f"render failed: {exc}"))
        finally:
            self.events.put(("render_done", None))

    def toggle_play(self):
        if self.player.playing:
            self.player.stop()
            self.play_btn.config(text="Play")
        elif self.last_wav:
            self.player.play(self.last_wav,
                             on_done=lambda: self.events.put(("play_done", None)))
            self.play_btn.config(text="Stop")

    def play_selected(self, _evt=None):
        sel = self.takes.curselection()
        if not sel:
            return
        takes = core.list_takes()
        if sel[0] < len(takes):
            self.last_wav = takes[sel[0]]
            self.player.play(self.last_wav,
                             on_done=lambda: self.events.put(("play_done", None)))
            self.play_btn.config(state="normal", text="Stop")

    def reveal(self):
        import subprocess
        subprocess.run(["open", str(core.takes_dir())])

    # ---------- events ----------

    def log(self, text):
        self.feed.config(state="normal")
        self.feed.insert("end", text + "\n")
        self.feed.see("end")
        self.feed.config(state="disabled")

    def refresh_takes(self):
        self.takes.delete(0, "end")
        for p in core.list_takes():
            self.takes.insert("end", f"  {p.stem}")

    def _drain(self):
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "note":
                    note, vel = payload
                    self.note_total += 1
                    self.counter.config(text=f"{self.note_total} notes")
                    self.log(f"{core.note_name(note):>4}  vel {vel:3}  {'|' * (vel // 6)}")
                elif kind == "pedal":
                    self.pedal.config(text="pedal" if payload else "")
                elif kind == "rendered":
                    self.last_wav = payload
                    self.log(f"rendered {payload.name} — playing")
                    self.refresh_takes()
                    self.play_btn.config(state="normal", text="Stop")
                    self.player.play(payload,
                                     on_done=lambda: self.events.put(("play_done", None)))
                elif kind == "play_done":
                    self.play_btn.config(text="Play")
                elif kind == "render_done":
                    self.rec_btn.config(state="normal" if self.port else "disabled")
                elif kind in ("log", "error"):
                    self.log(str(payload))
        except queue.Empty:
            pass
        self.after(40, self._drain)


def main():
    App().mainloop()


if __name__ == "__main__":
    main()
