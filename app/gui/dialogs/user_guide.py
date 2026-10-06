"""
Help ▸ User Guide (F1): Assets/user_guide.md, shown in a window with its contents on the left.

The guide is written in a small part of Markdown: # headings, paragraphs, "- " and "1. "
lists (indented two spaces for a nested list), **bold**, `code`, and [links](#heading) to
other headings, which scroll the guide there. parse() turns the text into blocks without Tk,
so the tests can check every link has a heading to go to.
"""
import re
import tkinter as tk
from dataclasses import dataclass, field
from pathlib import Path
from tkinter import ttk
from typing import Dict, List, Optional, Tuple

from app import paths
from app.gui import style as ui_style
from app.gui.window_placement import place_window

GUIDE_FILE = "user_guide.md"
INLINE = re.compile(r"\*\*(.+?)\*\*|`(.+?)`|\[([^\]]+)\]\(#([^)]+)\)")

Span = Tuple[str, str, Optional[str]]       # (text, kind: "" | "bold" | "code" | "link", link anchor)


@dataclass
class Block:
    kind: str                       # "h1", "h2", "h3", "p", "bullet", "number"
    spans: List[Span] = field(default_factory=list)
    level: int = 0                  # list nesting (0 or 1)
    number: str = ""                # "1." for a numbered item
    anchor: str = ""                # headings: what links point at


def slug(title: str) -> str:
    """GitHub's heading anchors: lower case, punctuation dropped, spaces to hyphens."""
    return re.sub(r"[^a-z0-9 -]", "", title.lower()).strip().replace(" ", "-")


def spans(text: str) -> List[Span]:
    found: List[Span] = []
    at = 0
    for m in INLINE.finditer(text):
        if m.start() > at:
            found.append((text[at:m.start()], "", None))
        if m.group(1) is not None:
            found.append((m.group(1), "bold", None))
        elif m.group(2) is not None:
            found.append((m.group(2), "code", None))
        else:
            found.append((m.group(3), "link", m.group(4)))
        at = m.end()
    if at < len(text):
        found.append((text[at:], "", None))
    return found


def parse(markdown: str) -> List[Block]:
    blocks: List[Block] = []
    paragraph: List[str] = []

    def flush():
        if paragraph:
            blocks.append(Block("p", spans(" ".join(paragraph))))
            paragraph.clear()

    for raw in markdown.splitlines():
        line = raw.rstrip()
        stripped = line.lstrip()
        indent = 1 if len(line) - len(stripped) >= 2 else 0
        heading = re.match(r"(#{1,3}) (.+)", line)
        number = re.match(r"(\d+\.) (.+)", stripped)
        if not stripped:
            flush()
        elif heading:
            flush()
            title = heading.group(2)
            blocks.append(Block(f"h{len(heading.group(1))}", spans(title), anchor=slug(title)))
        elif stripped.startswith("- "):
            flush()
            blocks.append(Block("bullet", spans(stripped[2:]), level=indent))
        elif number:
            flush()
            blocks.append(Block("number", spans(number.group(2)), level=indent, number=number.group(1)))
        elif blocks and blocks[-1].kind in ("bullet", "number") and not paragraph and indent:
            blocks[-1].spans += spans(" " + stripped)           # a list item carried on to the next line
        else:
            paragraph.append(stripped)
    flush()
    return blocks


def links(blocks: List[Block]) -> List[str]:
    return [anchor for b in blocks for _, kind, anchor in b.spans if kind == "link"]


def load_guide(path: Optional[Path] = None) -> str:
    path = path or (paths.ASSETS_DIR / GUIDE_FILE)
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return "# User Guide\n\nThe guide file is missing from this installation (Assets/user_guide.md)."


class UserGuideWindow:
    """One window; opening the guide again brings it to the front."""

    _open: Optional["UserGuideWindow"] = None

    @classmethod
    def show(cls, root) -> "UserGuideWindow":
        if cls._open is not None and cls._open.window.winfo_exists():
            cls._open.window.deiconify()
            cls._open.window.lift()
            cls._open.window.focus_set()
            return cls._open
        cls._open = cls(root)
        return cls._open

    def __init__(self, root, markdown: Optional[str] = None):
        self.blocks = parse(markdown if markdown is not None else load_guide())
        self.window = tk.Toplevel(root)
        self.window.title("User Guide")
        self.window.geometry("1000x720")
        paned = tk.PanedWindow(self.window, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        contents = ttk.Frame(paned)
        paned.add(contents, width=240)
        ttk.Label(contents, text="Contents", style=ui_style.HEADING_LABEL).pack(anchor=tk.W, pady=(0, 4))
        self.contents = ttk.Treeview(contents, show="tree", selectmode="browse")
        self.contents.pack(fill=tk.BOTH, expand=True)
        self.contents.bind("<<TreeviewSelect>>", self._on_contents)
        self.contents.bind("<<ThemeChanged>>", lambda e: self._style(), add="+")     # colours follow the theme

        body = ttk.Frame(paned)
        paned.add(body)
        self.text = tk.Text(body, wrap=tk.WORD, borderwidth=0, padx=18, pady=12, cursor="arrow",
                            font=(ui_style.FONT_FAMILY, 10), spacing1=2, spacing3=2)
        scroll = ttk.Scrollbar(body, orient=tk.VERTICAL, command=self.text.yview)
        self.text.configure(yscrollcommand=scroll.set)
        self.text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.text.tag_bind("link", "<Enter>", lambda e: self.text.config(cursor="hand2"))
        self.text.tag_bind("link", "<Leave>", lambda e: self.text.config(cursor="arrow"))
        self.window.bind("<Escape>", lambda e: self.window.destroy())
        self.contents_items: Dict[str, str] = {}        # contents row -> anchor
        self._render()
        place_window(self.window)        # centred on the app, not top left

    # --- drawing ---------------------------------------------------------------------------

    def _style(self):
        p = ui_style.THEMES.get(ui_style.current_theme or ui_style.DEFAULT_THEME, ui_style.THEMES[ui_style.DEFAULT_THEME])
        family = ui_style.FONT_FAMILY
        heading = p.get("heading_text", p["accent"])
        t = self.text
        t.tag_configure("h1", font=(family, 18, "bold"), foreground=heading, spacing1=4, spacing3=10)
        t.tag_configure("h2", font=(family, 14, "bold"), foreground=heading, spacing1=18, spacing3=6)
        t.tag_configure("h3", font=(family, 11, "bold"), foreground=heading, spacing1=12, spacing3=4)
        t.tag_configure("p", spacing3=8)
        t.tag_configure("list0", lmargin1=14, lmargin2=34, spacing3=3)
        t.tag_configure("list1", lmargin1=38, lmargin2=56, spacing3=3)
        t.tag_configure("bold", font=(family, 10, "bold"))
        t.tag_configure("code", font=ui_style.FONT_MONO, background=p["heading"])
        t.tag_configure("link", foreground=p["info"], underline=True)

    def _render(self):
        self._style()
        t = self.text
        t.configure(state=tk.NORMAL)
        t.delete("1.0", tk.END)
        parents = {}
        for block in self.blocks:
            if block.kind.startswith("h"):
                t.mark_set(f"anchor:{block.anchor}", t.index("end-1c"))
                t.mark_gravity(f"anchor:{block.anchor}", tk.LEFT)
                self._spans(block.spans, (block.kind,))
                t.insert(tk.END, "\n", (block.kind,))
                title = "".join(s[0] for s in block.spans)
                if block.kind == "h2":
                    parents["h2"] = self.contents.insert("", tk.END, text=title, open=True)
                    self.contents_items[parents["h2"]] = block.anchor
                elif block.kind == "h3":
                    row = self.contents.insert(parents.get("h2", ""), tk.END, text=title)
                    self.contents_items[row] = block.anchor
                continue
            if block.kind == "p":
                self._spans(block.spans, ("p",))
                t.insert(tk.END, "\n", ("p",))
                continue
            marker = "•" if block.kind == "bullet" else block.number
            if block.level:
                marker = "◦" if block.kind == "bullet" else block.number
            tag = f"list{block.level}"
            t.insert(tk.END, f"{marker}  ", (tag,))
            self._spans(block.spans, (tag,))
            t.insert(tk.END, "\n", (tag,))
        t.configure(state=tk.DISABLED)

    def _spans(self, spans_: List[Span], base: tuple):
        for index, (text, kind, anchor) in enumerate(spans_):
            tags = base + ((kind,) if kind else ())
            if kind == "link":
                link_tag = f"to:{anchor}:{index}:{self.text.index('end-1c')}"
                tags += (link_tag,)
                self.text.tag_bind(link_tag, "<Button-1>", lambda e, a=anchor: self.go_to(a))
            self.text.insert(tk.END, text, tags)

    # --- moving around -----------------------------------------------------------------------

    def go_to(self, anchor: str) -> bool:
        mark = f"anchor:{anchor}"
        if mark not in self.text.mark_names():
            return False
        self.text.yview(mark)
        return True

    def _on_contents(self, event=None):
        anchor = self.contents_items.get(next(iter(self.contents.selection()), ""))
        if anchor:
            self.go_to(anchor)
