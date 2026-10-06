import sys
import tkinter as tk
from app.gui.main_window import EVEFleetGUI
from app.logging_config import setup_logging
from app.paths import initialize_runtime_directories

if __name__ == "__main__":
    # The GUI mirrors its log lines to stdout. A redirected console (e.g. cp1252 on
    # Windows) can't encode every character it uses (❌, ▸); replace those rather than crash.
    # The packaged build has no stdout at all.
    if sys.stdout is not None:
        sys.stdout.reconfigure(errors="replace")
    setup_logging()
    # Ensure all writable directories exist before initializing the GUI
    initialize_runtime_directories()
    
    root = tk.Tk()
    app = EVEFleetGUI(root)
    root.mainloop()
