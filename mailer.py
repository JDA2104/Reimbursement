"""Pengiriman rekap lewat email (SMTP)."""

from __future__ import annotations

import mimetypes
import smtplib
from email.message import EmailMessage
from pathlib import Path

import config
import db
import excel_report


class MailNotConfigured(RuntimeError):
    """SMTP atau penerima belum diisi di .env."""


def check_config() -> None:
    missing = [
        name
        for name, value in (
            ("SMTP_HOST", config.SMTP_HOST),
            ("SMTP_USER", config.SMTP_USER),
            ("SMTP_PASSWORD", config.SMTP_PASSWORD),
            ("EMAIL_FROM", config.EMAIL_FROM),
            ("EMAIL_TO", config.EMAIL_TO),
        )
        if not value
    ]
    if missing:
        raise MailNotConfigured(
            "Setting email belum lengkap di .env: " + ", ".join(missing)
        )


def _attach(msg: EmailMessage, path: Path) -> None:
    ctype, _ = mimetypes.guess_type(path.name)
    maintype, _, subtype = (ctype or "application/octet-stream").partition("/")
    msg.add_attachment(
        path.read_bytes(),
        maintype=maintype,
        subtype=subtype,
        filename=path.name,
    )


def _body(period: str) -> str:
    summary = db.summary_by_period(period)
    lines = [
        f"Berikut rekap pengeluaran periode {excel_report.period_label(period)}.",
        "",
    ]
    grand_total = 0.0
    for payment_type, sheet_name in config.SHEET_NAMES.items():
        stats = summary.get(payment_type, {"count": 0, "total": 0.0})
        lines.append(
            f"  {sheet_name:<16} : {stats['count']:>3} struk   "
            f"{config.CURRENCY} {stats['total']:,.0f}"
        )
        grand_total += stats["total"]
    lines += [
        "",
        f"  {'GRAND TOTAL':<16} : {config.CURRENCY} {grand_total:,.0f}",
        "",
        "Lampiran:",
        "  - Form Excel berisi 2 sheet: Reimbursement dan Kartu Kredit.",
        "  - PDF lampiran struk: semua struk sudah dipotong dari latar dan disusun",
        "    berurutan tanggal, siap dicetak. Label R-01, K-01, dst. sesuai nomor",
        "    baris di sheet yang bersangkutan.",
        "  - File ZIP berisi foto struk asli resolusi penuh, untuk keperluan audit.",
        "",
        "Dikirim otomatis oleh bot reimbursement.",
    ]
    if config.KARYAWAN_NAMA:
        lines.insert(1, f"Karyawan: {config.KARYAWAN_NAMA} ({config.KARYAWAN_NPK})")
    return "\n".join(lines)


def send_report(period: str, excel_path: Path, zip_path: Path | None,
                pdf_path: Path | None = None, sender_name: str = "") -> list[str]:
    """Kirim rekap ke penerima yang dikonfigurasi. Kembalikan daftar penerima."""
    check_config()

    msg = EmailMessage()
    subject = f"Rekap Pengeluaran {excel_report.period_label(period)}"
    if sender_name:
        subject += f" - {sender_name}"
    msg["Subject"] = subject
    msg["From"] = config.EMAIL_FROM
    msg["To"] = ", ".join(config.EMAIL_TO)
    msg.set_content(_body(period))

    _attach(msg, excel_path)
    if pdf_path is not None:
        _attach(msg, pdf_path)
    if zip_path is not None:
        _attach(msg, zip_path)

    if config.SMTP_PORT == 465:
        with smtplib.SMTP_SSL(config.SMTP_HOST, config.SMTP_PORT, timeout=60) as smtp:
            smtp.login(config.SMTP_USER, config.SMTP_PASSWORD)
            smtp.send_message(msg)
    else:
        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=60) as smtp:
            smtp.starttls()
            smtp.login(config.SMTP_USER, config.SMTP_PASSWORD)
            smtp.send_message(msg)

    return list(config.EMAIL_TO)
