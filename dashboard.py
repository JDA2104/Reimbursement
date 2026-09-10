"""Jalur lokal dashboard: baca SQLite, tulis file HTML mandiri.

Perenderannya sendiri ada di `web/render.py` - berkas itu hanya memakai
pustaka standar, sehingga halaman yang sama bisa dirender oleh serverless
Vercel dari database cloud. Di sini kita cuma menyediakan datanya.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from typing import Any

WEB_DIR = Path(__file__).resolve().parent / "web"
if str(WEB_DIR) not in sys.path:
    sys.path.insert(0, str(WEB_DIR))

import config
import db
from render import render, summarize   # noqa: E402  (perlu WEB_DIR lebih dulu)

__all__ = ["build_dashboard", "local_rows", "render", "summarize"]


def local_rows() -> list[dict[str, Any]]:
    """Semua baris dari SQLite, sebagai dict biasa."""
    with db.connect() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM expenses WHERE status != 'deleted'"
        )]


def build_dashboard(months: int = 6, period: str | None = None) -> Path:
    """Tulis dashboard sebagai file HTML mandiri. Kembalikan lokasinya."""
    period = period or date.today().strftime("%Y-%m")
    page = render(summarize(local_rows(), period, months))
    out_path = config.EXPORT_DIR / f"Dashboard_{period}.html"
    out_path.write_text(page, encoding="utf-8")
    return out_path
