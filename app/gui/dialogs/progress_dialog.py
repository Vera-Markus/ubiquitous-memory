"""
A small window that shows a long job is still running (UI rework step 5.5): a
progress bar with a percentage when the size is known, a moving bar when it
isn't, and a status line. It can't be closed while the job runs.

    dialog = ProgressDialog(root, "Downloading the EVE database")
    worker calls dialog.report(stage, done, total)    # safe from any thread
    worker calls dialog.close()                        # safe from any thread
    dialog.wait()                                      # Tk thread: until closed

Workers never call Tk. Before mainloop() starts (the first-time database
download at startup), Tkinter refuses calls such as root.after() from other
threads ("main thread is not in main loop"). So workers only put updates on a
queue, and the dialog reads it on the Tk thread every POLL_MS.
"""
import queue
import tkinter as tk
from tkinter import ttk
from app.gui import style as ui_style
from app.gui.window_placement import place_window
from typing import Dict, Iterable, Optional

MB = 1024 * 1024
POLL_MS = 100
_CLOSE = object()


class ProgressDialog:
    def __init__(self, root, title: str, stages: Optional[Dict[str, str]] = None, status: str = "Starting…",
                 sized: Optional[Iterable[str]] = None):
        """
        stages maps a stage name to the words shown for it, e.g. {"download": "Downloading"}.
        sized names the stages whose counts are worth showing in MB (a download); the
        others show only a percentage. None shows MB for every stage.
        """
        self.root = root
        self.stages = stages or {}
        self.sized = None if sized is None else set(sized)
        self._updates: "queue.Queue" = queue.Queue()
        self.window = tk.Toplevel(root)
        self.window.title(title)
        self.window.resizable(False, False)
        self.window.protocol("WM_DELETE_WINDOW", lambda: None)   # can't be closed mid-job
        if root.winfo_viewable():
            self.window.transient(root)
        body = ttk.Frame(self.window, padding=(15, 15))
        body.pack(fill=tk.BOTH, expand=True)

        ttk.Label(body, text=title, font=(ui_style.FONT_FAMILY, 10, "bold")).pack(anchor=tk.W)
        self.bar = ttk.Progressbar(body, length=360, mode="indeterminate")
        self.bar.pack(fill=tk.X, pady=(10, 5))
        self.status = ttk.Label(body, text=status, anchor=tk.W, justify=tk.LEFT)
        self.status.pack(fill=tk.X)
        self.bar.start(15)
        self._indeterminate = True
        self._closed = False
        self.window.lift()
        self.window.after(POLL_MS, self._poll)
        place_window(self.window)        # centred on the app, not top left

    # --- any thread -------------------------------------------------------------------

    def report(self, stage: str, done: int, total: Optional[int]) -> None:
        self._updates.put(("progress", stage, done, total))

    def close(self) -> None:
        self._updates.put(_CLOSE)

    # --- Tk thread -----------------------------------------------------------------------

    def _poll(self) -> None:
        if self._closed:
            return
        latest = None
        while True:
            try:
                update = self._updates.get_nowait()
            except queue.Empty:
                break
            if update is _CLOSE:
                if latest is not None:
                    self._show(*latest[1:])
                self._close()
                return
            if update[0] == "status":
                self.status.config(text=update[1])
            else:
                latest = update               # only the newest progress is worth drawing
        if latest is not None:
            self._show(*latest[1:])
        self.window.after(POLL_MS, self._poll)

    def _show(self, stage: str, done: int, total: Optional[int]) -> None:
        words = self.stages.get(stage, stage.capitalize())
        in_mb = self.sized is None or stage in self.sized
        if total:
            if self._indeterminate:
                self.bar.stop()
                self.bar.config(mode="determinate", maximum=100)
                self._indeterminate = False
            percent = min(100.0, done * 100.0 / total)
            self.bar["value"] = percent
            self.status.config(text=f"{words}… {percent:.0f}%" + (f" ({done / MB:.0f} of {total / MB:.0f} MB)" if in_mb else ""))
        else:
            if not self._indeterminate:
                self.bar.config(mode="indeterminate")
                self.bar.start(15)
                self._indeterminate = True
            self.status.config(text=f"{words}… {done / MB:.0f} MB" if in_mb else f"{words}…")

    def _close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.bar.stop()
        self.window.destroy()

    def wait(self) -> None:
        """Runs the event loop until the dialog closes (works before mainloop starts)."""
        if not self._closed:
            self.window.wait_window()
