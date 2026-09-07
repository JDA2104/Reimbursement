"""Bot Telegram untuk sistem otomasi reimbursement.

Alur: user foto struk -> Claude membaca isinya -> user pilih Reimbursement atau
Kartu Kredit -> data masuk SQLite -> Excel di-generate ulang otomatis ->
user menjalankan /kirim saat rekap siap diemail ke petugas pengumpul.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import date, datetime
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import config
import db
import excel_report
import extractor
import mailer

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("reimbursement")


# --- Utilitas ------------------------------------------------------------

def money(amount: float, currency: str = config.CURRENCY) -> str:
    if currency.upper() == "IDR":
        return f"Rp {amount:,.0f}".replace(",", ".")
    return f"{currency} {amount:,.2f}"


def current_period() -> str:
    return date.today().strftime("%Y-%m")


def parse_period(args: list[str]) -> str:
    """Ambil periode dari argumen command, contoh: /rekap 2026-08."""
    if args:
        candidate = args[0].strip()
        try:
            datetime.strptime(candidate, "%Y-%m")
            return candidate
        except ValueError:
            pass
    return current_period()


def is_allowed(update: Update) -> bool:
    user = update.effective_user
    return user is not None and user.id in config.ALLOWED_USER_IDS


async def deny(update: Update) -> None:
    user = update.effective_user
    log.warning("Akses ditolak untuk user id=%s", user.id if user else "?")
    await update.effective_message.reply_text(
        "🚫 Kamu belum terdaftar sebagai pengguna bot ini.\n\n"
        f"Telegram user ID kamu: `{user.id if user else '?'}`\n"
        "Minta admin menambahkan ID tersebut ke `ALLOWED_TELEGRAM_USER_IDS` di file .env.",
        parse_mode="Markdown",
    )


def display_name(update: Update) -> str:
    user = update.effective_user
    if user is None:
        return "Tidak dikenal"
    return user.full_name or user.username or str(user.id)


# --- Command handlers ----------------------------------------------------

HELP_TEXT = """*Bot Reimbursement*

Kirim *foto struk* kapan saja — bot akan membaca isinya otomatis, lalu kamu tinggal
pilih apakah masuk *Reimbursement* atau *Kartu Kredit*.

*Perintah:*
/rekap `[YYYY-MM]` — ringkasan pengeluaran bulan ini
/excel `[YYYY-MM]` — kirim file Excel + ZIP foto ke chat ini
/kirim `[YYYY-MM]` — email rekap ke petugas pengumpul
/list — 10 entri terakhir milikmu
/hapus `<id>` — hapus satu entri
/help — tampilkan bantuan ini

Tanpa argumen tanggal, semua perintah memakai bulan berjalan."""


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed(update):
        return await deny(update)
    await update.message.reply_text(
        f"Halo {display_name(update)} 👋\n\n" + HELP_TEXT, parse_mode="Markdown"
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed(update):
        return await deny(update)
    await update.message.reply_text(HELP_TEXT, parse_mode="Markdown")


async def cmd_rekap(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed(update):
        return await deny(update)

    period = parse_period(context.args)
    summary = db.summary_by_period(period)

    lines = [f"📊 *Rekap {excel_report.period_label(period)}*", ""]
    grand_total = 0.0
    for payment_type, sheet_name in config.SHEET_NAMES.items():
        stats = summary.get(payment_type, {"count": 0, "total": 0.0})
        icon = "💵" if payment_type == config.PAYMENT_REIMBURSEMENT else "💳"
        lines.append(
            f"{icon} *{sheet_name}*\n"
            f"    {stats['count']} struk · {money(stats['total'])}"
        )
        grand_total += stats["total"]

    lines += ["", f"*TOTAL: {money(grand_total)}*"]

    pending = db.pending_count(update.effective_user.id)
    if pending:
        lines += ["", f"⚠️ Ada {pending} struk belum dipilih jenis pembayarannya."]

    if grand_total > 0:
        lines += ["", "Kirim /excel untuk file rekapnya, atau /kirim untuk email ke petugas."]

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_excel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed(update):
        return await deny(update)

    period = parse_period(context.args)
    summary = db.summary_by_period(period)
    if not summary:
        await update.message.reply_text(
            f"Belum ada data untuk {excel_report.period_label(period)}."
        )
        return

    await update.message.chat.send_action(ChatAction.UPLOAD_DOCUMENT)
    excel_path, zip_path = await asyncio.to_thread(excel_report.build_all, period)

    await update.message.reply_document(
        document=excel_path.open("rb"),
        filename=excel_path.name,
        caption=f"📗 Rekap {excel_report.period_label(period)}",
    )
    if zip_path is not None:
        await update.message.reply_document(
            document=zip_path.open("rb"),
            filename=zip_path.name,
            caption="🖼 Foto struk resolusi penuh",
        )


async def cmd_kirim(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Minta konfirmasi dulu — mengirim email itu aksi yang tidak bisa dibatalkan."""
    if not is_allowed(update):
        return await deny(update)

    period = parse_period(context.args)

    try:
        mailer.check_config()
    except mailer.MailNotConfigured as exc:
        await update.message.reply_text(f"⚙️ {exc}")
        return

    summary = db.summary_by_period(period)
    if not summary:
        await update.message.reply_text(
            f"Belum ada data untuk {excel_report.period_label(period)}."
        )
        return

    grand_total = sum(s["total"] for s in summary.values())
    count = sum(s["count"] for s in summary.values())
    penerima = ", ".join(config.EMAIL_TO)

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Ya, kirim", callback_data=f"send:{period}"),
        InlineKeyboardButton("❌ Batal", callback_data="cancel"),
    ]])
    await update.message.reply_text(
        f"📧 Kirim rekap *{excel_report.period_label(period)}*?\n\n"
        f"Isi: {count} struk · {money(grand_total)}\n"
        f"Ke: `{penerima}`",
        parse_mode="Markdown",
        reply_markup=keyboard,
    )


async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed(update):
        return await deny(update)

    rows = db.list_recent(update.effective_user.id)
    if not rows:
        await update.message.reply_text("Belum ada entri.")
        return

    lines = ["*10 entri terakhir:*", ""]
    for row in rows:
        if row["status"] == "pending":
            mark = "⏳"
        elif row["payment_type"] == config.PAYMENT_CREDIT_CARD:
            mark = "💳"
        else:
            mark = "💵"
        lines.append(
            f"{mark} `#{row['id']}` {row['txn_date']} · {row['merchant']}\n"
            f"     {money(row['total'], row['currency'])} · {row['category']}"
        )
    lines += ["", "Hapus dengan `/hapus <id>`"]
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_hapus(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed(update):
        return await deny(update)

    if not context.args or not context.args[0].lstrip("#").isdigit():
        await update.message.reply_text("Format: `/hapus 12`", parse_mode="Markdown")
        return

    expense_id = int(context.args[0].lstrip("#"))
    row = db.get_expense(expense_id)
    if row is None or row["status"] == "deleted":
        await update.message.reply_text(f"Entri #{expense_id} tidak ditemukan.")
        return
    if row["telegram_user_id"] != update.effective_user.id:
        await update.message.reply_text("Kamu hanya bisa menghapus entri milikmu sendiri.")
        return

    db.soft_delete(expense_id)
    await asyncio.to_thread(excel_report.build_workbook, row["period"])
    await update.message.reply_text(
        f"🗑 Entri #{expense_id} ({row['merchant']}) dihapus dari rekap."
    )


# --- Foto struk ----------------------------------------------------------

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed(update):
        return await deny(update)

    message = update.message
    photo = message.photo[-1]  # resolusi terbesar

    if photo.file_size and photo.file_size > config.MAX_PHOTO_BYTES:
        await message.reply_text("Foto terlalu besar (maksimal 10 MB).")
        return

    status_msg = await message.reply_text("⏳ Membaca struk...")
    await message.chat.send_action(ChatAction.TYPING)

    # Unduh foto
    tg_file = await context.bot.get_file(photo.file_id)
    photo_bytes = bytes(await tg_file.download_as_bytearray())
    sha256 = hashlib.sha256(photo_bytes).hexdigest()

    existing = db.find_by_hash(sha256)
    if existing is not None:
        await status_msg.edit_text(
            f"♻️ Struk ini sudah pernah diunggah sebagai `#{existing['id']}` "
            f"({existing['merchant']}, {money(existing['total'], existing['currency'])}).",
            parse_mode="Markdown",
        )
        return

    period = current_period()
    folder = config.PHOTO_DIR / period
    folder.mkdir(parents=True, exist_ok=True)
    photo_path = folder / f"{sha256[:16]}.jpg"
    photo_path.write_bytes(photo_bytes)

    # Ekstraksi dengan Claude (blocking -> jalankan di thread terpisah)
    try:
        data = await asyncio.to_thread(extractor.extract, photo_path)
    except Exception:
        log.exception("Ekstraksi gagal untuk %s", photo_path)
        photo_path.unlink(missing_ok=True)
        await status_msg.edit_text(
            "❌ Gagal membaca struk. Coba foto ulang dengan pencahayaan lebih baik, "
            "atau pastikan seluruh struk masuk ke dalam frame."
        )
        return

    if not data.is_receipt:
        photo_path.unlink(missing_ok=True)
        await status_msg.edit_text(
            f"🤔 Ini sepertinya bukan struk atau nota.\n_{data.notes}_",
            parse_mode="Markdown",
        )
        return

    expense_id = db.insert_expense(
        {
            "telegram_user_id": update.effective_user.id,
            "user_name": display_name(update),
            "period": data.txn_date[:7],
            "txn_date": data.txn_date,
            "merchant": data.merchant,
            "description": data.description,
            "category": data.category,
            "subtotal": data.subtotal,
            "tax": data.tax,
            "total": data.total,
            "currency": data.currency or config.CURRENCY,
            "payment_type": None,
            "photo_path": str(photo_path),
            "status": "pending",
            "confidence": data.confidence,
            "raw_json": data.model_dump_json(),
            "created_at": datetime.now().isoformat(timespec="seconds"),
        },
        photo_sha256=sha256,
    )

    # Urutan tombol mengikuti tebakan model, supaya pilihan paling mungkin ada di kiri.
    reimburse_btn = InlineKeyboardButton(
        "💵 Reimbursement", callback_data=f"pay:{config.PAYMENT_REIMBURSEMENT}:{expense_id}"
    )
    card_btn = InlineKeyboardButton(
        "💳 Kartu Kredit", callback_data=f"pay:{config.PAYMENT_CREDIT_CARD}:{expense_id}"
    )
    first_row = (
        [card_btn, reimburse_btn]
        if data.payment_hint == "kartu_kredit"
        else [reimburse_btn, card_btn]
    )
    keyboard = InlineKeyboardMarkup([
        first_row,
        [InlineKeyboardButton("🗑 Batalkan", callback_data=f"drop:{expense_id}")],
    ])

    detail = [
        f"🧾 *{data.merchant}*",
        f"📅 {data.txn_date}",
        f"🏷 {data.category}",
        f"💰 *{money(data.total, data.currency)}*",
    ]
    if data.tax:
        detail.append(f"    _(termasuk PPN {money(data.tax, data.currency)})_")
    if data.description:
        detail.append(f"📝 {data.description}")
    if data.confidence < 0.7:
        detail.append("\n⚠️ _Hasil bacaan kurang yakin — mohon dicek dulu._")
    detail.append("\nMasuk ke mana?")

    await status_msg.edit_text(
        "\n".join(detail), parse_mode="Markdown", reply_markup=keyboard
    )


# --- Tombol inline -------------------------------------------------------

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    if not is_allowed(update):
        await query.edit_message_text("🚫 Akses ditolak.")
        return

    action, _, payload = query.data.partition(":")

    if action == "cancel":
        await query.edit_message_text("Dibatalkan.")

    elif action == "pay":
        payment_type, _, raw_id = payload.partition(":")
        await _confirm_payment(query, payment_type, int(raw_id))

    elif action == "drop":
        expense_id = int(payload)
        row = db.get_expense(expense_id)
        if row is not None:
            db.soft_delete(expense_id)
        await query.edit_message_text("🗑 Struk dibatalkan, tidak masuk rekap.")

    elif action == "send":
        await _send_email(query, context, period=payload)


async def _confirm_payment(query, payment_type: str, expense_id: int) -> None:
    row = db.get_expense(expense_id)
    if row is None or row["status"] == "deleted":
        await query.edit_message_text("Entri sudah tidak ada.")
        return

    db.confirm_expense(expense_id, payment_type)

    # Excel selalu dibuat ulang dari database, jadi file di disk selalu terkini.
    await asyncio.to_thread(excel_report.build_workbook, row["period"])

    sheet = config.SHEET_NAMES[payment_type]
    stats = db.summary_by_period(row["period"]).get(
        payment_type, {"count": 0, "total": 0.0}
    )
    icon = "💵" if payment_type == config.PAYMENT_REIMBURSEMENT else "💳"

    await query.edit_message_text(
        f"✅ Tersimpan di sheet *{sheet}* sebagai `#{expense_id}`\n\n"
        f"🧾 {row['merchant']} · {money(row['total'], row['currency'])}\n"
        f"{icon} Total {sheet} {excel_report.period_label(row['period'])}: "
        f"*{money(stats['total'])}* ({stats['count']} struk)",
        parse_mode="Markdown",
    )


async def _send_email(query, context: ContextTypes.DEFAULT_TYPE, period: str) -> None:
    await query.edit_message_text("📤 Menyiapkan dan mengirim rekap...")

    try:
        excel_path, zip_path = await asyncio.to_thread(excel_report.build_all, period)
        recipients = await asyncio.to_thread(
            mailer.send_report, period, excel_path, zip_path,
            query.from_user.full_name or "",
        )
    except mailer.MailNotConfigured as exc:
        await query.edit_message_text(f"⚙️ {exc}")
        return
    except Exception as exc:
        log.exception("Pengiriman email gagal untuk periode %s", period)
        await query.edit_message_text(f"❌ Gagal mengirim email:\n`{exc}`", parse_mode="Markdown")
        return

    await query.edit_message_text(
        f"✅ Rekap *{excel_report.period_label(period)}* terkirim ke:\n"
        f"`{', '.join(recipients)}`",
        parse_mode="Markdown",
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed(update):
        return await deny(update)
    await update.message.reply_text(
        "Kirim *foto struk* untuk dicatat, atau ketik /help untuk daftar perintah.",
        parse_mode="Markdown",
    )


# --- Entry point ---------------------------------------------------------

def main() -> None:
    db.init()

    missing = config.missing_settings()
    if missing:
        raise SystemExit(
            "Setting berikut belum diisi di file .env: " + ", ".join(missing) +
            "\nSalin .env.example menjadi .env lalu lengkapi isinya."
        )

    app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("rekap", cmd_rekap))
    app.add_handler(CommandHandler("excel", cmd_excel))
    app.add_handler(CommandHandler("kirim", cmd_kirim))
    app.add_handler(CommandHandler("list", cmd_list))
    app.add_handler(CommandHandler("hapus", cmd_hapus))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    log.info("Bot berjalan. Tekan Ctrl+C untuk berhenti.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
