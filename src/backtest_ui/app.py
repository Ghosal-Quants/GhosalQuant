from __future__ import annotations

import sys
import threading
import subprocess
from pathlib import Path
from queue import Queue, Empty

import customtkinter as ctk
from tkinter import filedialog, messagebox

"""
WARNING: VIBED because I hate UI development and this is a prototype for now

This is a rough experimental prototype of a UI to abstract backtesting from QRs.
"""

def repo_root() -> Path:
    # src/backtest_ui/app.py -> repo root is 3 parents up
    return Path(__file__).resolve().parents[2]


class App(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title("GhosalQuant Backtest (Prototype)")
        self.geometry("900x600")

        self.root = repo_root()
        self.default_cfg = self.root / "examples" / "config.yaml"

        self.cfg_var = ctk.StringVar(value=str(self.default_cfg) if self.default_cfg.exists() else "")

        self.log_q: Queue[str] = Queue()

        top = ctk.CTkFrame(self)
        top.pack(fill="x", padx=12, pady=12)

        row1 = ctk.CTkFrame(top)
        row1.pack(fill="x")

        row2 = ctk.CTkFrame(top)
        row2.pack(fill="x")

        # Config + Run
        ctk.CTkLabel(row1, text="Config:").pack(side="left", padx=(10, 8), pady=10)
        self.cfg_entry = ctk.CTkEntry(row1, textvariable=self.cfg_var, width=560)
        self.cfg_entry.pack(side="left", padx=8, pady=10)
        ctk.CTkButton(row1, text="Browse…", command=self.browse).pack(side="left", padx=8, pady=10)
        self.run_btn = ctk.CTkButton(row1, text="Run", command=self.run)
        self.run_btn.pack(side="left", padx=8, pady=10)

        # Strategy
        self.strategy_var = ctk.StringVar(value="")
        ctk.CTkLabel(row2, text="Strategy:").pack(side="left", padx=(10, 8), pady=10)
        self.strategy_entry = ctk.CTkEntry(row2, textvariable=self.strategy_var, width=560)
        self.strategy_entry.pack(side="left", padx=8, pady=10)
        ctk.CTkButton(row2, text="Browse…", command=self.browse_strategy).pack(side="left", padx=8, pady=10)

        self.log = ctk.CTkTextbox(self, wrap="none")
        self.log.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        self.after(100, self.drain_logs)

    def browse(self) -> None:
        p = filedialog.askopenfilename(
            title="Select config.yaml",
            filetypes=[("YAML files", "*.yaml *.yml"), ("All files", "*.*")]
        )
        if p:
            self.cfg_var.set(p)

    def browse_strategy(self) -> None:
        p = filedialog.askopenfilename(
            title="Select strategy .py",
            filetypes=[("Python files", "*.py"), ("All files", "*.*")]
        )
        if p:
            self.strategy_var.set(p)

    def append(self, line: str) -> None:
        self.log.insert("end", line + "\n")
        self.log.see("end")

    def drain_logs(self) -> None:
        try:
            while True:
                self.append(self.log_q.get_nowait())
        except Empty:
            pass
        self.after(100, self.drain_logs)

    def run(self) -> None:
        cfg = Path(self.cfg_var.get().strip())
        if not cfg.exists():
            messagebox.showerror("Missing config", f"Config not found:\n{cfg}")
            return

        strategy = Path(self.strategy_var.get().strip())
        if not strategy.exists():
            messagebox.showerror("Missing strategy", f"Strategy not found:\n{strategy}")
            self.run_btn.configure(state="normal")
            return

        self.run_btn.configure(state="disabled")
        self.append(f"Running with config: {cfg}")

        strategy = Path(self.strategy_var.get().strip())  # add a strategy picker var in UI
        cmd = [sys.executable, "-m", "backtest_ui", str(cfg), str(strategy)]

        def worker() -> None:
            try:
                proc = subprocess.Popen(
                    cmd,
                    cwd=str(self.root),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                )
                assert proc.stdout is not None
                for line in proc.stdout:
                    self.log_q.put(line.rstrip("\n"))
                rc = proc.wait()
                self.log_q.put(f"[done] exit code = {rc}")
            except Exception as e:
                self.log_q.put(f"[error] {e}")
            finally:
                self.log_q.put("__enable__")

        def enable_later() -> None:
            # wait for the marker then re-enable the button in UI thread
            while True:
                msg = self.log_q.get()
                if msg == "__enable__":
                    self.run_btn.configure(state="normal")
                    break
                else:
                    self.append(msg)

        threading.Thread(target=worker, daemon=True).start()
        threading.Thread(target=enable_later, daemon=True).start()


def main() -> None:
    ctk.set_appearance_mode("System")
    App().mainloop()


if __name__ == "__main__":
    main()
