"""
The character chooser for Open in the game client (ESI features plan 27.1): which pilot's
client gets the window or route. It opens on the line's pilot and shows who is in game,
asked of ESI as the dialog opens. Send sends to the chosen pilot; a pilot who isn't in game
gets nothing, and the dialog stays open to choose another.
"""
import asyncio
import threading
import tkinter as tk
from tkinter import ttk
from typing import Awaitable, Callable, Dict, List, Optional, Tuple

from app.esi_service.game_client import GameClient, Sent
from app.gui import style as ui_style
from app.gui.window_placement import place_window

CHECKING, IN_GAME, OFFLINE, UNKNOWN = "checking…", "in game", "offline", "can't tell"


def run_async(coro: Awaitable, done: Callable) -> None:
    """Runs a coroutine on a worker thread; done(result) is called on that thread."""
    def work():
        try:
            result = asyncio.run(coro)
        except Exception as e:          # an unexpected failure still closes the loop and reports
            result = Sent(False, f"Nothing was sent: {e}")
        done(result)
    threading.Thread(target=work, daemon=True).start()


class SendToClientDialog:
    """
    send(client, char_id) -> coroutine returning Sent. characters: {ID: name}, in the order
    shown. pilot: the line's pilot, chosen at first.
    """

    def __init__(self, app, title: str, what: str, characters: Dict[str, str],
                 send: Callable[[GameClient, str], Awaitable[Sent]], pilot: Optional[str] = None,
                 steps: Optional[List[Tuple[str, Callable]]] = None, start: int = 0):
        # steps: one window at a time (the client shows one contract window, lab test): after each
        # send the dialog stays open on the next, with Open Next (ESI features plan 28.5).
        self.steps = steps or []
        self.index = start
        self.app = app
        self.client: GameClient = app.game_client
        self.send = send
        self.characters = characters
        self.states: Dict[str, str] = {}
        self.window = tk.Toplevel(app.root)
        self.window.title(title)
        self.window.transient(app.root)
        self.window.resizable(False, False)
        body = ttk.Frame(self.window, padding=(14, 12))
        body.pack(fill=tk.BOTH, expand=True)
        self.lbl_what = ttk.Label(body, text=what, wraplength=420, justify=tk.LEFT,
                                  font=(ui_style.FONT_FAMILY, 10, "bold"))
        self.lbl_what.pack(anchor=tk.W)
        self.lbl_step = ttk.Label(body, text="", style=ui_style.HINT_LABEL)
        self.lbl_step.pack(anchor=tk.W)
        ttk.Label(body, text="Send to:").pack(anchor=tk.W, pady=(10, 2))
        self.choice = tk.StringVar(value=str(pilot) if str(pilot) in characters else next(iter(characters), ""))
        self.pilot_given = str(pilot) in characters
        self.rows: Dict[str, ttk.Label] = {}
        for char_id, name in characters.items():
            row = ttk.Frame(body)
            row.pack(anchor=tk.W, fill=tk.X)
            ttk.Radiobutton(row, text=name, value=char_id, variable=self.choice).pack(side=tk.LEFT)
            state = ttk.Label(row, text=CHECKING, foreground=ui_style.MUTED)
            state.pack(side=tk.LEFT, padx=(8, 0))
            self.rows[char_id] = state
        self.message = ttk.Label(body, text="", wraplength=420, justify=tk.LEFT, foreground=ui_style.ERROR)
        self.message.pack(anchor=tk.W, fill=tk.X, pady=(8, 0))
        buttons = ttk.Frame(body)
        buttons.pack(anchor=tk.E, pady=(10, 0))
        ttk.Button(buttons, text="Cancel", command=self.close).pack(side=tk.LEFT, padx=5)
        self.btn_send = ttk.Button(buttons, text="Send", command=self._send)
        self.btn_send.pack(side=tk.LEFT, padx=5)
        self._show_step()
        self.window.bind("<Return>", lambda e: self._send())
        self.window.bind("<Escape>", lambda e: self.close())
        try:
            self.window.grab_set()
        except tk.TclError:
            pass
        place_window(self.window)
        self.btn_send.focus_set()
        self._check_online()

    def _later(self, func, *args) -> None:
        """Back on the Tk thread, unless the dialog has closed since."""
        try:
            self.window.after(0, lambda: self.window.winfo_exists() and func(*args))
        except (tk.TclError, RuntimeError):
            pass

    def _check_online(self) -> None:
        async def check():
            ids = list(self.characters)
            answers = await asyncio.gather(*(self.client.online(c) for c in ids))
            return dict(zip(ids, answers))
        run_async(check(), lambda result: self._later(self._show_online, result))

    def _show_online(self, result) -> None:
        if not isinstance(result, dict):
            result = {}
        for char_id, label in self.rows.items():
            answer = result.get(char_id)
            state = IN_GAME if answer else OFFLINE if answer is False else UNKNOWN
            self.states[char_id] = state
            label.config(text=state, foreground=ui_style.OK if answer else ui_style.MUTED)
        # No pilot given (or theirs isn't in game while exactly one other is): choose the one who is.
        online = [c for c in self.rows if self.states.get(c) == IN_GAME]
        if self.states.get(self.choice.get()) != IN_GAME and len(online) == 1 and not self.pilot_given:
            self.choice.set(online[0])

    def _send(self) -> None:
        char_id = self.choice.get()
        if not char_id or str(self.btn_send.cget("state")) == tk.DISABLED:
            return
        allowed, why = self.app._may_call_ccp()
        if not allowed:
            self.message.config(text=why)
            return
        self.btn_send.config(state=tk.DISABLED)
        self.message.config(text="Sending…", foreground=ui_style.MUTED)
        run_async(self.send(self.client, char_id), lambda sent: self._later(self._sent, sent))

    def _sent(self, sent: Sent) -> None:
        self.app._log(("[SUCCESS] " if sent.ok else "[INFO] ") + sent.message)
        label = self.rows.get(self.choice.get())
        if label is not None and (sent.ok or "isn't in game" in sent.message):      # what the send just found
            self.states[self.choice.get()] = IN_GAME if sent.ok else OFFLINE
            label.config(text=IN_GAME if sent.ok else OFFLINE, foreground=ui_style.OK if sent.ok else ui_style.MUTED)
        if sent.ok and self.index + 1 < len(self.steps):
            self.index += 1             # on to the next: the same pilot, Open Next
            self._show_step()
            self.message.config(text=sent.message, foreground=ui_style.OK)
            self.btn_send.config(state=tk.NORMAL)
            return
        if sent.ok and not sent.keep_open:
            self.close()
            return
        if sent.ok:                     # sent, but it may need sending again (the market): say so, stay open
            self.btn_send.config(state=tk.NORMAL, text="Send Again")
            self.message.config(text=sent.message, foreground=ui_style.OK)
            return
        self.btn_send.config(state=tk.NORMAL)
        self.message.config(text=sent.message, foreground=ui_style.ERROR)

    def _show_step(self) -> None:
        if not self.steps:
            return
        what, self.send = self.steps[self.index]
        self.lbl_what.config(text=what)
        self.lbl_step.config(text=f"{self.index + 1} of {len(self.steps)}: one contract window at a time in game")
        self.btn_send.config(text="Open" if self.index == 0 else "Open Next")

    def describe(self) -> dict:
        """What the dialog shows, for the GUI harness."""
        return {"title": self.window.title(),
                "pilots": [[self.characters[c], label.cget("text"), c == self.choice.get()]
                           for c, label in self.rows.items()],
                "message": self.message.cget("text"),
                **({"what": self.lbl_what.cget("text"), "step": self.lbl_step.cget("text"),
                    "button": self.btn_send.cget("text")} if self.steps else {})}

    def close(self) -> None:
        self.window.destroy()
