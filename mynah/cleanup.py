"""Uninstall cleanup helpers.

The owner's rule: the uninstaller **automatically removes everything app-specific** — app
files, the engine **runtime packs**, config, and logs — with *no* prompt for those. The
**only** thing it asks about is **models**, because those live in the shared Hugging Face
cache and may be used by other apps. So:

  * :func:`purge_runtime` — silent removal of the runtime packs + app-data (config/logs). The
    Inno uninstaller runs this; no model is ever touched here.
  * :func:`run_model_cleanup` — a tiny Tk dialog listing installed GGML models with checkboxes
    (**all unchecked by default**) + sizes, flagged "may be shared." Deletes only the checked
    ones. Launched after the uninstaller removed the app, via ``mynah --purge-ui``.
"""

from __future__ import annotations

import shutil

from .platform_layer import app_data_dir, runtime_data_dir, set_run_at_login


def purge_runtime() -> int:
    """Delete the engine runtime packs + app-data (config + logs) and clear the run-at-login
    entry. Returns bytes freed. Never touches the shared HF model cache (that's the per-model
    checklist's job)."""
    try:
        set_run_at_login(False)
    except Exception:
        pass
    freed = 0
    for d in (runtime_data_dir(), app_data_dir()):
        if not d.is_dir():
            continue
        for p in d.rglob("*"):
            try:
                if p.is_file():
                    freed += p.stat().st_size
            except OSError:
                pass
        shutil.rmtree(d, ignore_errors=True)
    print(f"Removed Mynah runtime + config (freed {freed/1e6:.0f} MB).")
    return freed


def run_model_cleanup() -> int:
    """Per-model delete checklist (none checked by default). Returns the count deleted."""
    from . import models

    installed = models.installed_asr_models()
    if not installed:
        print("No downloaded Mynah models found in the shared cache — nothing to clean up.")
        return 0

    try:
        import tkinter as tk
        from tkinter import ttk
    except Exception:
        print("No Tk available — leaving the shared-cache models in place. "
              "Delete them manually from ~/.cache/huggingface/hub if you want the space back.")
        return 0

    rows = [(name, *models.model_status(name)) for name in installed]  # (name, installed, size)
    deleted = {"count": 0}

    root = tk.Tk()
    root.title("Mynah — remove downloaded models?")
    root.resizable(False, False)
    frm = ttk.Frame(root, padding=14)
    frm.grid(sticky="nsew")

    ttk.Label(frm, justify="left", wraplength=420,
              text="Mynah has been uninstalled. These speech models were downloaded into the "
                   "shared Hugging Face cache. They may be used by other apps — tick only the "
                   "ones you want to delete (nothing is checked by default).").grid(
        row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))

    vars_by_name: dict[str, tk.BooleanVar] = {}
    for i, (name, _inst, size) in enumerate(rows, start=1):
        var = tk.BooleanVar(value=False)
        vars_by_name[name] = var
        ttk.Checkbutton(frm, text=name, variable=var).grid(row=i, column=0, sticky="w", pady=2)
        ttk.Label(frm, text=f"{size/1e9:.1f} GB", foreground="#555").grid(
            row=i, column=1, sticky="e", padx=8)

    btns = ttk.Frame(frm)
    btns.grid(row=len(rows) + 1, column=0, columnspan=2, sticky="e", pady=(12, 0))

    def do_delete():
        for name, var in vars_by_name.items():
            if var.get():
                freed = models.remove_model(name)
                print(f"Deleted {name} (freed {freed/1e9:.1f} GB).")
                deleted["count"] += 1
        root.destroy()

    def keep_all():
        root.destroy()

    ttk.Button(btns, text="Keep all", command=keep_all).grid(row=0, column=0, padx=4)
    ttk.Button(btns, text="Delete selected", command=do_delete).grid(row=0, column=1, padx=4)

    root.update_idletasks()
    w, h = root.winfo_reqwidth(), root.winfo_reqheight()
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    root.geometry(f"+{(sw - w) // 2}+{(sh - h) // 3}")
    root.mainloop()
    return deleted["count"]
