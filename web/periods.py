"""Penamaan bulan. Tanpa dependency, dipakai bot maupun serverless."""

from __future__ import annotations

from datetime import date

MONTH_ID = [
    "Januari", "Februari", "Maret", "April", "Mei", "Juni",
    "Juli", "Agustus", "September", "Oktober", "November", "Desember",
]

MONTH_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def period_label(period: str) -> str:
    """'2026-08' -> 'Agustus 2026'."""
    year, month = period.split("-")
    return f"{MONTH_ID[int(month) - 1]} {year}"


def fmt_date(iso: str) -> str:
    """'2026-08-14' -> '14-Aug-26', mengikuti format di form asli."""
    try:
        d = date.fromisoformat(iso)
    except (ValueError, TypeError):
        return iso
    return f"{d.day:02d}-{MONTH_ABBR[d.month - 1]}-{d:%y}"
