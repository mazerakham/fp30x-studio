"""FP-30X Studio — a small desktop recorder for the Roland FP-30X.

Press Record, play the piano, press Stop. It writes a MIDI file, renders it to
audio and plays it back.
"""

from __future__ import annotations

import queue
import subprocess
import threading
import time
import tkinter as tk
from datetime import datetime

from . import core

BG = "#0f1115"
CARD = "#171a21"
LINE = "#2a2f3a"
FG = "#e6e8ec"
DIM = "#9aa3b2"
MUTED = "#5b6472"
ACC = "#6cb6ff"
OK = "#3fb950"
BAD = "#f85149"
WARN = "#d29922"

REC = "#c9372f"
REC_HOVER = "#e04a41"
NEUTRAL = "#222833"
NEUTRAL_HOVER = "#2c3441"

MONO = ("SF Mono", 11)
UI = ("SF Pro Text", 13)
UI_B = ("SF Pro Text", 13, "bold")
BIG = ("SF Pro Display", 30, "bold")


class Btn(tk.Label):
    """A button built from a Label.

    macOS renders tk.Button natively and silently ignores -background, which
    leaves light text on a white button. Labels honour their colours, so we
    style one and bind the click ourselves.
    """

    def __init__(self, parent, text, command, bg=NEUTRAL, fg=FG,
                 hover=NEUTRAL_HOVER, font=UI_B, padx=22):
        super().__init__(parent, text=text, bg=bg, fg=fg, font=font,
                         padx=padx, pady=9, cursor="pointinghand")
        self._bg, self._fg, self._hover = bg, fg, hover
        self._command = command
        self._enabled = True
        self.bind("<Button-1>", self._on_click)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)

    def _on_click(self, _evt):
        if self._enabled:
            self._command()

    def _on_enter(self, _evt):
        if self._enabled:
            self.config(bg=self._hover)

    def _on_leave(self, _evt):
        self.config(bg=self._bg)

    def style(self, text=None, bg=None, fg=None, hover=None):
        if text is not None:
            self.config(text=text)
        if bg is not None:
            self._bg = bg
        if fg is not None:
            self._fg = fg
        if hover is not None:
            self._hover = hover
        self._repaint()

    def enable(self, on: bool):
        self._enabled = on
        self.config(cursor="pointinghand" if on else "arrow")
        self._repaint()

    def _repaint(self):
        self.config(bg=self._bg, fg=self._fg if self._enabled else MUTED)


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

        c = self._card(self)
        self.dot = tk.Canvas(c, width=10, height=10, bg=CARD, highlightthickness=0)
        self.dot.pack(side="left", padx=(14, 8), pady=13)
        self.dot_id = self.dot.create_oval(1, 1, 9, 9, fill=BAD, outline="")
        self.status = tk.Label(c, text="looking for the piano...", bg=CARD, fg=DIM, font=UI)
        self.status.pack(side="left", pady=13)

        c = self._card(self)
        self.timer = tk.Label(c, text="0:00", bg=CARD, fg=FG, font=BIG)
        self.timer.pack(side="left", padx=(16, 0), pady=(10, 6))
        self.counter = tk.Label(c, text="0 notes", bg=CARD, fg=DIM, font=UI)
        self.counter.pack(side="left", padx=12, pady=(22, 10))
        self.pedal = tk.Label(c, text="", bg=CARD, fg=ACC, font=UI_B)
        self.pedal.pack(side="right", padx=16, pady=(22, 10))

        c = self._card(self)
        inner = tk.Frame(c, bg=CARD)
        inner.pack(fill="x", padx=14, pady=12)
        self.rec_btn = Btn(inner, "Record", self.toggle_record,
                           bg=REC, fg="#ffffff", hover=REC_HOVER)
        self.rec_btn.pack(side="left")
        self.rec_btn.enable(False)
        self.play_btn = Btn(inner, "Play", self.toggle_play)
        self.play_btn.pack(side="left", padx=8)
        self.play_btn.enable(False)
        self.reveal_btn = Btn(inner, "Show files", self.reveal, fg=DIM, font=UI, padx=16)
        self.reveal_btn.pack(side="right")

        tk.Label(self, text="LIVE", bg=BG, fg=DIM,
                 font=("SF Pro Text", 10, "bold")).pack(anchor="w", padx=18, pady=(6, 4))
        c = self._card(self, pady=(0, 10))
        self.feed = tk.Text(c, height=9, bg="#0b0d11", fg="#d7dce4", font=MONO,
                            relief="flat", highlightthickness=0, bd=0, padx=12, pady=10,
                            state="disabled", wrap="none")
        self.feed.pack(fill="both", expand=True, padx=1, pady=1)

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
                self.rec_btn.enable(True)
            else:
                self.dot.itemconfig(self.dot_id, fill=BAD)
                self.status.config(
                    text="piano not found — pair Bluetooth MIDI in Audio MIDI Setup", fg=WARN)
                self.rec_btn.enable(False)
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
        # Stopping playback must also reset the Play button, or the window
        # ends up showing two buttons that both say Stop.
        self.player.stop()
        self.play_btn.style(text="Play")
        self.play_btn.enable(False)

        self.note_total = 0
        self.counter.config(text="0 notes")
        self.feed.config(state="normal")
        self.feed.delete("1.0", "end")
        self.feed.config(state="disabled")
        self.capture = core.Capture(self.port, self.events)
        self.capture.start()
        self.rec_btn.style(text="Stop")
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
        self.rec_btn.style(text="Record")
        self.pedal.config(text="")

        if cap.note_count == 0:
            self.log("nothing captured — was the piano connected?")
            return

        stamp = datetime.now().strftime("%Y-%m-%d %H.%M.%S")
        mid = core.takes_dir() / f"{stamp}.mid"
        wav = core.takes_dir() / f"{stamp}.wav"
        cap.save_midi(mid)
        self.log(f"saved {mid.name} ({cap.note_count} notes) — rendering...")
        self.rec_btn.enable(False)
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

    def _start_playback(self, wav):
        self.last_wav = wav
        self.player.play(wav, on_done=lambda: self.events.put(("play_done", None)))
        self.play_btn.enable(True)
        self.play_btn.style(text="Stop")

    def toggle_play(self):
        if self.player.playing:
            self.player.stop()
            self.play_btn.style(text="Play")
        elif self.last_wav:
            self._start_playback(self.last_wav)

    def play_selected(self, _evt=None):
        if self.capture:
            return
        sel = self.takes.curselection()
        if not sel:
            return
        takes = core.list_takes()
        if sel[0] < len(takes):
            self._start_playback(takes[sel[0]])

    def reveal(self):
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
                    self.log(f"rendered {payload.name} — playing")
                    self.refresh_takes()
                    self._start_playback(payload)
                elif kind == "play_done":
                    self.play_btn.style(text="Play")
                elif kind == "render_done":
                    self.rec_btn.enable(bool(self.port))
                elif kind in ("log", "error"):
                    self.log(str(payload))
        except queue.Empty:
            pass
        self.after(40, self._drain)


def main():
    App().mainloop()


if __name__ == "__main__":
    main()
