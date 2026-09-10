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


def local_rows(telegram_user_id: int | None = None) -> list[dict[str, Any]]:
    """Baris dari SQLite sebagai dict biasa.

    Tanpa `telegram_user_id`, seluruh karyawan ikut - itu tampilan admin.
    Karyawan biasa hanya boleh melihat datanya sendiri.
    """
    query = "SELECT * FROM expenses WHERE status != 'deleted'"
    params: list[Any] = []
    if telegram_user_id is not None:
        query += " AND telegram_user_id = ?"
        params.append(telegram_user_id)
    with db.connect() as conn:
        return [dict(r) for r in conn.execute(query, params)]


def build_dashboard(months: int = 6, period: str | None = None,
                    telegram_user_id: int | None = None) -> Path:
    """Tulis dashboard sebagai file HTML mandiri. Kembalikan lokasinya."""
    period = period or date.today().strftime("%Y-%m")
    page = render(summarize(local_rows(telegram_user_id), period, months))
    suffix = f"_{telegram_user_id}" if telegram_user_id is not None else ""
    out_path = config.EXPORT_DIR / f"Dashboard_{period}{suffix}.html"
    out_path.write_text(page, encoding="utf-8")
    return out_path
