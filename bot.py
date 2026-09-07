"""Bot Telegram untuk sistem otomasi reimbursement.

Tiga tahap:
  1. User foto struk  -> dibaca otomatis  -> user pilih Reimbursement / Kartu Kredit,
     foto dipindah ke folder R atau K sesuai pilihan.
  2. Bot minta satu kalimat detail (siapa yang dijamu, dari mana, siapa yang ikut).
     Kalimat itu dipilah otomatis ke kolom-kolom form.
  3. Saat rekap siap, user menjalankan /kirim untuk mengirim form Excel beserta
     foto struk ke email yang sudah didaftarkan.
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
    format="%(asctime)s %(levelname)s %(name)s - %(message)s", level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("reimbursement")

SKIP_TOKENS = {"-", "skip", "lewati", "tidak ada", "kosong", "n/a"}


# --- Utilitas ------------------------------------------------------------

def money(amount: float) -> str:
    return f"Rp {amount:,.0f}".replace(",", ".")


def current_period() -> str:
    return date.today().strftime("%Y-%m")


def parse_period(args: list[str]) -> str:
    if args:
        try:
            datetime.strptime(args[0].strip(), "%Y-%m")
            return args[0].strip()
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
        "Tambahkan ID itu ke `ALLOWED_TELEGRAM_USER_IDS` di file .env, lalu restart bot.",
        parse_mode="Markdown",
    )


def display_name(update: Update) -> str:
    user = update.effective_user
    if user is None:
        return "Tidak dikenal"
    return user.full_name or user.username or str(user.id)


# --- Command -------------------------------------------------------------

HELP_TEXT = """*Bot Reimbursement*

Kirim *foto struk* — bot membaca tanggal, merchant, dan jumlahnya,
lalu kamu pilih *Reimbursement* atau *Kartu Kredit*.
Setelah itu ketik satu kalimat berisi detail jamuan (atau `-` kalau bukan jamuan).

*Perintah:*
/rekap `[YYYY-MM]` — ringkasan bulan ini
/excel `[YYYY-MM]` — kirim form Excel + foto ke chat ini
/kirim `[YYYY-MM]` — email form ke petugas pengumpul
/list — 10 entri terakhir
/hapus `<id>` — hapus satu entri
/batal — batalkan struk yang sedang ditanyakan

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
    grand = 0.0
    for payment_type, sheet in config.SHEET_NAMES.items():
        stats = summary.get(payment_type, {"count": 0, "total": 0.0})
        icon = "💵" if payment_type == config.PAYMENT_REIMBURSEMENT else "💳"
        lines.append(f"{icon} *{sheet}*\n     {stats['count']} struk · {money(stats['total'])}")
        grand += stats["total"]

    lines += ["", f"*GRAND TOTAL: {money(grand)}*"]

    belum = db.incomplete_count(update.effective_user.id)
    if belum:
        lines += ["", f"⚠️ {belum} struk belum lengkap datanya."]
    if grand > 0:
        lines += ["", "/excel untuk lihat filenya · /kirim untuk email ke petugas"]

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_excel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed(update):
        return await deny(update)

    period = parse_period(context.args)
    if not db.summary_by_period(period):
        await update.message.reply_text(
            f"Belum ada data untuk {excel_report.period_label(period)}."
        )
        return

    await update.message.chat.send_action(ChatAction.UPLOAD_DOCUMENT)
    excel_path, zip_path = await asyncio.to_thread(excel_report.build_all, period)

    await update.message.reply_document(
        document=excel_path.open("rb"), filename=excel_path.name,
        caption=f"📗 Form {excel_report.period_label(period)}",
    )
    if zip_path is not None:
        await update.message.reply_document(
            document=zip_path.open("rb"), filename=zip_path.name,
            caption="🖼 Foto struk (nomor file = nomor baris di form)",
        )


async def cmd_kirim(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Minta konfirmasi dulu — email tidak bisa ditarik kembali."""
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

    grand = sum(s["total"] for s in summary.values())
    count = sum(s["count"] for s in summary.values())

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Ya, kirim", callback_data=f"send:{period}"),
        InlineKeyboardButton("❌ Batal", callback_data="cancel"),
    ]])
    await update.message.reply_text(
        f"📧 Kirim form *{excel_report.period_label(period)}*?\n\n"
        f"Isi: {count} struk · {money(grand)}\n"
        f"Ke: `{', '.join(config.EMAIL_TO)}`",
        parse_mode="Markdown", reply_markup=keyboard,
    )


async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed(update):
        return await deny(update)

    rows = db.list_recent(update.effective_user.id)
    if not rows:
        await update.message.reply_text("Belum ada entri.")
        return

    marks = {
        "pending_payment": "⏳", "pending_detail": "✏️",
        config.PAYMENT_CREDIT_CARD: "💳", config.PAYMENT_REIMBURSEMENT: "💵",
    }
    lines = ["*10 entri terakhir:*", ""]
    for row in rows:
        mark = marks.get(row["status"]) or marks.get(row["payment_type"], "•")
        lines.append(
            f"{mark} `#{row['id']}` {row['txn_date']} · {row['merchant']} · {money(row['jumlah'])}"
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
    await update.message.reply_text(f"🗑 Entri #{expense_id} ({row['merchant']}) dihapus.")


async def cmd_batal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed(update):
        return await deny(update)

    row = db.awaiting_detail(update.effective_user.id)
    if row is None:
        await update.message.reply_text("Tidak ada struk yang sedang ditanyakan.")
        return
    db.soft_delete(row["id"])
    await update.message.reply_text(f"🗑 Struk {row['merchant']} dibatalkan.")


# --- Tahap 1: foto struk -------------------------------------------------

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed(update):
        return await deny(update)

    message = update.message
    photo = message.photo[-1]  # resolusi terbesar

    if photo.file_size and photo.file_size > config.MAX_PHOTO_BYTES:
        await message.reply_text("Foto terlalu besar (maksimal 10 MB).")
        return

    status = await message.reply_text("⏳ Membaca struk...")
    await message.chat.send_action(ChatAction.TYPING)

    tg_file = await context.bot.get_file(photo.file_id)
    photo_bytes = bytes(await tg_file.download_as_bytearray())
    sha256 = hashlib.sha256(photo_bytes).hexdigest()

    existing = db.find_by_hash(sha256)
    if existing is not None:
        await status.edit_text(
            f"♻️ Struk ini sudah pernah diunggah sebagai `#{existing['id']}` "
            f"({existing['merchant']}, {money(existing['jumlah'])}).",
            parse_mode="Markdown",
        )
        return

    # Simpan sementara di inbox; dipindah ke folder R atau K setelah user memilih.
    photo_path = config.INBOX_DIR / f"{sha256[:16]}.jpg"
    photo_path.write_bytes(photo_bytes)

    try:
        data = await asyncio.to_thread(extractor.extract_receipt, photo_path)
    except Exception:
        log.exception("Ekstraksi gagal untuk %s", photo_path)
        photo_path.unlink(missing_ok=True)
        await status.edit_text(
            "❌ Gagal membaca struk. Coba foto ulang dengan pencahayaan lebih baik, "
            "atau pastikan seluruh struk masuk frame."
        )
        return

    if not data.is_receipt:
        photo_path.unlink(missing_ok=True)
        await status.edit_text(
            f"🤔 Ini sepertinya bukan struk.\n_{data.notes}_", parse_mode="Markdown"
        )
        return

    expense_id = db.insert_expense(
        {
            "telegram_user_id": update.effective_user.id,
            "user_name": display_name(update),
            "period": data.txn_date[:7],
            "txn_date": data.txn_date,
            "merchant": data.merchant,
            "alamat": data.alamat,
            "category": data.category,
            "jumlah": data.jumlah,
            "currency": data.currency or config.CURRENCY,
            "payment_type": None,
            "photo_path": str(photo_path),
            "status": "pending_payment",
            "confidence": data.confidence,
            "raw_json": data.model_dump_json(),
            "created_at": datetime.now().isoformat(timespec="seconds"),
        },
        photo_sha256=sha256,
    )

    # Tombol tebakan model ditaruh di kiri, tapi keputusan tetap di user —
    # struk tidak selalu menunjukkan siapa yang membayar.
    btn_r = InlineKeyboardButton(
        "💵 Reimbursement", callback_data=f"pay:{config.PAYMENT_REIMBURSEMENT}:{expense_id}"
    )
    btn_k = InlineKeyboardButton(
        "💳 Kartu Kredit", callback_data=f"pay:{config.PAYMENT_CREDIT_CARD}:{expense_id}"
    )
    first = [btn_k, btn_r] if data.payment_hint == "kartu_kredit" else [btn_r, btn_k]

    detail = [
        f"🧾 *{data.merchant}*",
        f"📅 {data.txn_date}",
        f"📍 {data.alamat or '—'}",
        f"🏷 {data.category}",
        f"💰 *{money(data.jumlah)}*",
    ]
    if data.confidence < 0.7:
        detail.append("\n⚠️ _Hasil bacaan kurang yakin — mohon dicek._")
    detail.append("\nMasuk ke mana?")

    await status.edit_text(
        "\n".join(detail), parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            first, [InlineKeyboardButton("🗑 Batalkan", callback_data=f"drop:{expense_id}")]
        ]),
    )


# --- Tahap 2: pilih R / K, foto dipindah --------------------------------

async def _choose_payment(query, payment_type: str, expense_id: int) -> None:
    row = db.get_expense(expense_id)
    if row is None or row["status"] == "deleted":
        await query.edit_message_text("Entri sudah tidak ada.")
        return

    # Pindahkan foto ke folder R atau K sesuai pilihan.
    src = Path(row["photo_path"])
    dest_dir = config.PHOTO_FOLDERS[payment_type] / row["period"]
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name
    if src.exists():
        src.replace(dest)

    db.set_payment_type(expense_id, payment_type, str(dest))

    folder = "R" if payment_type == config.PAYMENT_REIMBURSEMENT else "K"
    sheet = config.SHEET_NAMES[payment_type]

    await query.edit_message_text(
        f"✅ *{sheet}* — foto disimpan di folder *{folder}*\n"
        f"🧾 {row['merchant']} · {money(row['jumlah'])}\n\n"
        "Sekarang ketik *satu kalimat* detail jamuannya, contoh:\n"
        "_Chen Yifei dan Huang Xinmin dari JA Solar, Executive, industri pendidikan, "
        "yang ikut Ariadi, Alex Janu, Galih_\n\n"
        "Kalau ini bukan jamuan klien (taksi, bensin, ATK), ketik `-` saja.",
        parse_mode="Markdown",
    )


# --- Tahap 2b: kalimat detail dari user ---------------------------------

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed(update):
        return await deny(update)

    pending = db.awaiting_detail(update.effective_user.id)
    if pending is None:
        await update.message.reply_text(
            "Kirim *foto struk* untuk dicatat, atau /help untuk daftar perintah.",
            parse_mode="Markdown",
        )
        return

    text = update.message.text.strip()

    # Struk non-jamuan: lewati kolom tamu, isi tipe/tujuan seadanya.
    if text.lower() in SKIP_TOKENS:
        db.save_details(pending["id"], {
            "tipe": pending["category"], "tujuan": "", "tamu_nama": "",
            "tamu_posisi": "", "tamu_perusahaan": "", "industri": "",
            "karyawan_internal": "",
        })
        await _finish(update, pending)
        return

    await update.message.chat.send_action(ChatAction.TYPING)
    try:
        detail = await asyncio.to_thread(extractor.parse_details, text)
    except Exception:
        log.exception("Gagal memilah detail untuk entri #%s", pending["id"])
        await update.message.reply_text(
            "❌ Gagal memahami kalimatnya. Coba tulis ulang lebih jelas, "
            "atau ketik `-` untuk melewati.", parse_mode="Markdown"
        )
        return

    db.save_details(pending["id"], detail.model_dump())

    # User boleh menimpa lokasi/alamat hasil bacaan struk lewat kalimatnya.
    overrides = {}
    if detail.lokasi_override.strip():
        overrides["merchant"] = detail.lokasi_override.strip()
    if detail.alamat_override.strip():
        overrides["alamat"] = detail.alamat_override.strip()
    if overrides:
        db.update_fields(pending["id"], **overrides)

    await _finish(update, pending)


async def _finish(update: Update, pending) -> None:
    """Tutup satu entri: bangun ulang Excel, lalu laporkan posisi terbaru."""
    row = db.get_expense(pending["id"])
    await asyncio.to_thread(excel_report.build_workbook, row["period"])

    sheet = config.SHEET_NAMES[row["payment_type"]]
    stats = db.summary_by_period(row["period"]).get(
        row["payment_type"], {"count": 0, "total": 0.0}
    )

    lines = [
        f"✅ Masuk sheet *{sheet}* baris ke-{stats['count']} (`#{row['id']}`)",
        "",
        f"🧾 {row['merchant']} · {money(row['jumlah'])}",
    ]
    if row["tamu_nama"]:
        lines.append(f"👥 {row['tamu_nama']} — {row['tamu_posisi']} ({row['tamu_perusahaan']})")
    if row["karyawan_internal"]:
        lines.append(f"🏢 Internal: {row['karyawan_internal']}")
    lines += ["", f"Total {sheet} {excel_report.period_label(row['period'])}: *{money(stats['total'])}*"]

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


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
        await _choose_payment(query, payment_type, int(raw_id))

    elif action == "drop":
        row = db.get_expense(int(payload))
        if row is not None:
            Path(row["photo_path"]).unlink(missing_ok=True)
            db.soft_delete(int(payload))
        await query.edit_message_text("🗑 Struk dibatalkan, tidak masuk rekap.")

    elif action == "send":
        await _send_email(query, period=payload)


# --- Tahap 3: kirim email ------------------------------------------------

async def _send_email(query, period: str) -> None:
    await query.edit_message_text("📤 Menyiapkan dan mengirim form...")

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
        await query.edit_message_text(
            f"❌ Gagal mengirim email:\n`{exc}`", parse_mode="Markdown"
        )
        return

    await query.edit_message_text(
        f"✅ Form *{excel_report.period_label(period)}* terkirim ke:\n"
        f"`{', '.join(recipients)}`",
        parse_mode="Markdown",
    )


# --- Entry point ---------------------------------------------------------

def main() -> None:
    db.init()

    missing = config.missing_settings()
    if missing:
        raise SystemExit(
            "Setting berikut belum diisi di file .env: " + ", ".join(missing)
            + "\nSalin .env.example menjadi .env lalu lengkapi isinya."
        )

    app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("rekap", cmd_rekap))
    app.add_handler(CommandHandler("excel", cmd_excel))
    app.add_handler(CommandHandler("kirim", cmd_kirim))
    app.add_handler(CommandHandler("list", cmd_list))
    app.add_handler(CommandHandler("hapus", cmd_hapus))
    app.add_handler(CommandHandler("batal", cmd_batal))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    log.info("Bot berjalan. Tekan Ctrl+C untuk berhenti.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
