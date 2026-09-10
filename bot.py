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
import sys
from datetime import date, datetime
from pathlib import Path

# HARUS di atas semua import lokal. Modul bersama (cloud, render, periods) ada
# di web/ supaya serverless Vercel bisa memakai kode yang sama; folder itu perlu
# masuk path sebelum modul mana pun yang mengimpornya. Jangan pindahkan blok ini
# ke bawah - `import cloud` akan gagal karena urutan alfabetis mendahului config.
sys.path.insert(0, str(Path(__file__).resolve().parent / "web"))

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update  # noqa: E402
from telegram.constants import ChatAction  # noqa: E402
from telegram.ext import (  # noqa: E402
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import cloud  # noqa: E402
import config  # noqa: E402
import dashboard  # noqa: E402
import db  # noqa: E402
import excel_report  # noqa: E402
import extractor  # noqa: E402
import mailer  # noqa: E402
import receipt_image  # noqa: E402

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


def caller_id(update: Update) -> int | None:
    user = update.effective_user
    return user.id if user else None


def is_allowed(update: Update) -> bool:
    """Akses ditentukan tabel users, bukan .env - supaya admin bisa
    menambah dan mencabut tanpa menyunting berkas atau me-restart bot."""
    uid = caller_id(update)
    if uid is None:
        return False
    row = db.get_user(uid)
    return row is not None and row["status"] == db.STATUS_ACTIVE


def is_admin(update: Update) -> bool:
    uid = caller_id(update)
    if uid is None:
        return False
    row = db.get_user(uid)
    return (row is not None and row["status"] == db.STATUS_ACTIVE
            and row["role"] == db.ROLE_ADMIN)


async def deny(update: Update) -> None:
    """Balasan untuk yang belum berhak - isinya menyesuaikan status."""
    uid = caller_id(update)
    row = db.get_user(uid) if uid else None
    status = row["status"] if row else None

    if status == db.STATUS_PENDING:
        pesan = ("⏳ Permintaan aksesmu sudah dikirim ke admin dan sedang menunggu "
                 "persetujuan.\n\nKamu akan diberi tahu begitu disetujui.")
    elif status == db.STATUS_REVOKED:
        pesan = ("🚫 Aksesmu ke bot ini sudah dicabut.\n\n"
                 "Hubungi admin kalau menurutmu ini keliru.")
    else:
        pesan = ("🚫 Kamu belum terdaftar.\n\n"
                 "Kirim /start untuk meminta akses ke admin.")

    log.warning("Akses ditolak untuk user id=%s (status=%s)", uid, status)
    await update.effective_message.reply_text(pesan)


async def notify_admins(context: ContextTypes.DEFAULT_TYPE, text: str,
                        markup: InlineKeyboardMarkup | None = None) -> int:
    """Kirim pesan ke semua admin aktif. Kembalikan berapa yang berhasil."""
    terkirim = 0
    for admin_id in db.admin_ids():
        try:
            await context.bot.send_message(
                chat_id=admin_id, text=text,
                parse_mode="Markdown", reply_markup=markup,
            )
            terkirim += 1
        except Exception:
            # Admin yang memblokir bot tidak boleh menggagalkan yang lain.
            log.exception("Gagal memberi tahu admin %s", admin_id)
    return terkirim


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
/excel `[YYYY-MM]` — kirim form Excel + lampiran struk ke chat ini
/lembar `[YYYY-MM]` — lembar struk saja (PDF, urut tanggal, siap cetak)
/dashboard `[YYYY-MM]` — dashboard tracking (HTML, buka di browser)
/sync — kirim ulang data ke database cloud
/kirim `[YYYY-MM]` — email form ke petugas pengumpul
/list — 10 entri terakhir
/hapus `<id>` — hapus satu entri
/profil — lihat / isi NPK, jabatan, departemen
/batal — batalkan struk yang sedang ditanyakan

Tanpa argumen tanggal, semua perintah memakai bulan berjalan."""


ADMIN_HELP = """

*Perintah admin:*
/users — daftar karyawan & statusnya
/cabut `<id>` — cabut akses karyawan
/aktifkan `<id>` — pulihkan akses
/jadikanadmin `<id>` — beri hak admin"""


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Pintu masuk semua orang: yang sudah aktif dapat menu, yang belum
    otomatis mengajukan permintaan akses ke admin."""
    user = update.effective_user
    if user is None:
        return

    if is_allowed(update):
        teks = f"Halo {display_name(update)} 👋\n\n" + HELP_TEXT
        if is_admin(update):
            teks += ADMIN_HELP
        await update.message.reply_text(teks, parse_mode="Markdown")
        return

    status = db.request_access(user.id, display_name(update), user.username or "")

    if status == db.STATUS_REVOKED:
        return await deny(update)

    if status == db.STATUS_PENDING:
        markup = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Setujui", callback_data=f"acc:ok:{user.id}"),
            InlineKeyboardButton("❌ Tolak", callback_data=f"acc:no:{user.id}"),
        ]])
        terkirim = await notify_admins(
            context,
            "👤 *Permintaan akses baru*\n\n"
            f"Nama : {display_name(update)}\n"
            f"User : @{user.username or '—'}\n"
            f"ID   : `{user.id}`",
            markup,
        )
        if terkirim:
            await update.message.reply_text(
                "⏳ Permintaan aksesmu sudah dikirim ke admin.\n"
                "Kamu akan diberi tahu begitu disetujui."
            )
        else:
            # Tanpa admin aktif, permintaan tidak akan pernah diputuskan -
            # lebih baik dikatakan terus terang daripada menunggu selamanya.
            await update.message.reply_text(
                "⚠️ Belum ada admin aktif di sistem ini, jadi permintaanmu "
                "belum bisa diteruskan.\n\nHubungi pengelola bot secara langsung."
            )


async def cmd_users(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update):
        return await deny(update)

    rows = db.list_users()
    if not rows:
        await update.message.reply_text("Belum ada karyawan terdaftar.")
        return

    tanda = {db.STATUS_PENDING: "⏳", db.STATUS_ACTIVE: "✅", db.STATUS_REVOKED: "🚫"}
    lines = ["*Karyawan terdaftar:*", ""]
    for r in rows:
        mark = tanda.get(r["status"], "•")
        gelar = " 👑" if r["role"] == db.ROLE_ADMIN else ""
        lines.append(f"{mark} *{r['nama']}*{gelar}  `{r['telegram_user_id']}`")
        detail = " · ".join(p for p in (r["npk"], r["jabatan"], r["departemen"]) if p)
        if detail:
            lines.append(f"      {detail}")
        elif r["status"] == db.STATUS_ACTIVE:
            lines.append("      _profil belum diisi_")

    lines += ["", "`/cabut <id>` · `/aktifkan <id>` · `/jadikanadmin <id>`"]
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def _ubah_status(update: Update, context: ContextTypes.DEFAULT_TYPE,
                       status: str, kata: str) -> None:
    """Tulang punggung /cabut dan /aktifkan."""
    if not is_admin(update):
        return await deny(update)

    if not context.args or not context.args[0].lstrip("#").isdigit():
        await update.message.reply_text(
            f"Format: `/{kata} 89525770`\nLihat ID lewat /users", parse_mode="Markdown"
        )
        return

    target = int(context.args[0].lstrip("#"))
    row = db.get_user(target)
    if row is None:
        await update.message.reply_text(f"Karyawan `{target}` tidak ditemukan.",
                                        parse_mode="Markdown")
        return

    if status == db.STATUS_REVOKED and target == caller_id(update):
        await update.message.reply_text(
            "Kamu tidak bisa mencabut aksesmu sendiri — nanti tidak ada yang "
            "bisa mengelola bot ini."
        )
        return

    if (status == db.STATUS_REVOKED and row["role"] == db.ROLE_ADMIN
            and len(db.admin_ids()) <= 1):
        await update.message.reply_text(
            "Itu satu-satunya admin aktif. Angkat admin lain dulu sebelum "
            "mencabut yang ini."
        )
        return

    db.decide_access(target, status, caller_id(update))
    await update.message.reply_text(
        f"{'🚫 Akses dicabut' if status == db.STATUS_REVOKED else '✅ Akses dipulihkan'} "
        f"untuk *{row['nama']}*.\n\n_Data dan struk lamanya tetap tersimpan._",
        parse_mode="Markdown",
    )

    kabar = ("🚫 Aksesmu ke bot reimbursement telah dicabut oleh admin."
             if status == db.STATUS_REVOKED else
             "✅ Aksesmu telah dipulihkan. Kirim /start untuk mulai lagi.")
    try:
        await context.bot.send_message(chat_id=target, text=kabar)
    except Exception:
        log.warning("Tidak bisa memberi tahu karyawan %s", target)


async def cmd_cabut(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _ubah_status(update, context, db.STATUS_REVOKED, "cabut")


async def cmd_aktifkan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _ubah_status(update, context, db.STATUS_ACTIVE, "aktifkan")


async def cmd_jadikanadmin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update):
        return await deny(update)

    if not context.args or not context.args[0].lstrip("#").isdigit():
        await update.message.reply_text("Format: `/jadikanadmin 89525770`",
                                        parse_mode="Markdown")
        return

    target = int(context.args[0].lstrip("#"))
    row = db.get_user(target)
    if row is None or row["status"] != db.STATUS_ACTIVE:
        await update.message.reply_text(
            "Karyawan itu belum terdaftar aktif. Setujui aksesnya dulu."
        )
        return

    db.set_role(target, db.ROLE_ADMIN)
    await update.message.reply_text(f"👑 *{row['nama']}* sekarang admin.",
                                    parse_mode="Markdown")
    try:
        await context.bot.send_message(
            chat_id=target,
            text="👑 Kamu sekarang admin bot reimbursement. Kirim /start untuk "
                 "melihat perintah tambahannya.",
        )
    except Exception:
        log.warning("Tidak bisa memberi tahu admin baru %s", target)


async def cmd_profil(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Lihat atau isi identitas yang mengisi kepala form Excel."""
    if not is_allowed(update):
        return await deny(update)

    uid = caller_id(update)
    teks = " ".join(context.args) if context.args else ""

    if teks:
        bagian = [b.strip() for b in teks.split("|")]
        if len(bagian) < 3:
            await update.message.reply_text(
                "Format: `/profil <NPK> | <Jabatan> | <Departemen> | <Perusahaan>`\n\n"
                "Contoh:\n`/profil 1908005 | President Director | BOD | "
                "PT. Triputra Energi Megatara`",
                parse_mode="Markdown",
            )
            return
        bagian += [""] * (4 - len(bagian))
        db.update_profile(uid, npk=bagian[0], jabatan=bagian[1],
                          departemen=bagian[2], perusahaan=bagian[3])

    row = db.get_user(uid)
    kurang = [f for f in ("npk", "jabatan", "departemen") if not row[f]]
    lines = [
        "👤 *Profil kamu*", "",
        f"Nama       : {row['nama']}",
        f"NPK        : {row['npk'] or '—'}",
        f"Jabatan    : {row['jabatan'] or '—'}",
        f"Departemen : {row['departemen'] or '—'}",
        f"Perusahaan : {row['perusahaan'] or '—'}",
    ]
    if kurang:
        lines += ["", "⚠️ Kepala form Excel-mu akan kosong sampai ini diisi:",
                  "`/profil 1908005 | President Director | BOD | PT. Triputra Energi Megatara`"]
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed(update):
        return await deny(update)
    await update.message.reply_text(HELP_TEXT, parse_mode="Markdown")


async def cmd_rekap(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed(update):
        return await deny(update)

    period = parse_period(context.args)
    summary = db.summary_by_period(period, caller_id(update))

    lines = [f"📊 *Rekap {excel_report.period_label(period)}*", ""]
    grand = 0.0
    for payment_type, sheet in config.SHEET_NAMES.items():
        stats = summary.get(payment_type, {"count": 0, "total": 0.0})
        icon = "💵" if payment_type == config.PAYMENT_REIMBURSEMENT else "💳"
        lines.append(f"{icon} *{sheet}*\n     {stats['count']} struk · {money(stats['total'])}")
        grand += stats["total"]

    lines += ["", f"*GRAND TOTAL: {money(grand)}*"]

    belum = db.incomplete_count(caller_id(update))
    if belum:
        lines += ["", f"⚠️ {belum} struk belum lengkap datanya."]
    if grand > 0:
        lines += ["", "/excel untuk lihat filenya · /kirim untuk email ke petugas"]

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_excel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed(update):
        return await deny(update)

    period = parse_period(context.args)
    uid = caller_id(update)
    if not db.summary_by_period(period, uid):
        await update.message.reply_text(
            f"Belum ada data untuk {excel_report.period_label(period)}."
        )
        return

    await update.message.chat.send_action(ChatAction.UPLOAD_DOCUMENT)
    excel_path, zip_path = await asyncio.to_thread(excel_report.build_all, period, uid)
    pdf_path = await asyncio.to_thread(receipt_image.build_contact_sheet, period, uid)

    await update.message.reply_document(
        document=excel_path.open("rb"), filename=excel_path.name,
        caption=f"📗 Form {excel_report.period_label(period)}",
    )
    if pdf_path is not None:
        await update.message.reply_document(
            document=pdf_path.open("rb"), filename=pdf_path.name,
            caption="📄 Lampiran struk — sudah dipotong & urut tanggal, siap cetak",
        )
    if zip_path is not None:
        await update.message.reply_document(
            document=zip_path.open("rb"), filename=zip_path.name,
            caption="🖼 Foto asli resolusi penuh (untuk audit)",
        )


async def _push_to_cloud() -> int | None:
    """Salin baris lokal ke Turso. Diam saja kalau cloud belum dikonfigurasi.

    Dipanggil setelah tiap entri selesai supaya dashboard Vercel ikut terbarui.
    Kegagalan sengaja tidak dilempar ke atas - bot harus tetap jalan meski
    internet mati; SQLite lokal tetap sumber kebenarannya.
    """
    if not cloud.is_configured():
        return None
    try:
        rows = await asyncio.to_thread(dashboard.local_rows)
        return await asyncio.to_thread(cloud.sync_from_local, rows)
    except Exception:
        log.exception("Sinkronisasi ke cloud gagal")
        return None


async def cmd_sync(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Kirim ulang seluruh data lokal ke database cloud."""
    if not is_allowed(update):
        return await deny(update)

    if not cloud.is_configured():
        await update.message.reply_text(
            "⚙️ Database cloud belum diatur.\n"
            "Isi `TURSO_DATABASE_URL` dan `TURSO_AUTH_TOKEN` di file .env, "
            "lalu restart bot.",
            parse_mode="Markdown",
        )
        return

    status = await update.message.reply_text("☁️ Mengirim data ke cloud...")
    try:
        rows = await asyncio.to_thread(dashboard.local_rows)
        count = await asyncio.to_thread(cloud.sync_from_local, rows)
    except Exception as exc:
        log.exception("Sinkronisasi manual gagal")
        await status.edit_text(f"❌ Gagal:\n`{exc}`", parse_mode="Markdown")
        return

    await status.edit_text(
        f"✅ {count} baris terkirim ke database cloud.\n"
        "Dashboard di Vercel sudah menampilkan data terbaru."
    )


async def cmd_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Kirim dashboard HTML — dibuka di browser, tidak butuh internet."""
    if not is_allowed(update):
        return await deny(update)

    period = parse_period(context.args)
    # Admin melihat seluruh karyawan; yang lain hanya dirinya sendiri.
    lingkup = None if is_admin(update) else caller_id(update)

    await update.message.chat.send_action(ChatAction.UPLOAD_DOCUMENT)
    path = await asyncio.to_thread(dashboard.build_dashboard, 6, period, lingkup)

    await update.message.reply_document(
        document=path.open("rb"), filename=path.name,
        caption=(f"📊 Dashboard {excel_report.period_label(period)}"
                 f" — {'semua karyawan' if lingkup is None else 'data kamu'}\n"
                 "Buka dengan browser. Datanya tertanam di file."),
    )


async def cmd_lembar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Kirim hanya lembar lampiran struk, tanpa Excel."""
    if not is_allowed(update):
        return await deny(update)

    period = parse_period(context.args)
    await update.message.chat.send_action(ChatAction.UPLOAD_DOCUMENT)
    pdf_path = await asyncio.to_thread(
        receipt_image.build_contact_sheet, period, caller_id(update))

    if pdf_path is None:
        await update.message.reply_text(
            f"Belum ada struk untuk {excel_report.period_label(period)}."
        )
        return

    await update.message.reply_document(
        document=pdf_path.open("rb"), filename=pdf_path.name,
        caption=f"📄 Lampiran struk {excel_report.period_label(period)} — urut tanggal",
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

    summary = db.summary_by_period(period, caller_id(update))
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
    await asyncio.to_thread(excel_report.build_workbook, row["period"],
                            row["telegram_user_id"])
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

    # Struk yang sama difoto dua kali lolos dari hash file, tapi ketahuan
    # lewat kecocokan merchant + tanggal + nominal.
    twin = db.find_similar(data.merchant, data.txn_date, data.jumlah, exclude_id=expense_id)
    if twin is not None:
        detail.append(
            f"\n🔁 *Mirip `#{twin['id']}`* yang sudah tercatat "
            f"({twin['merchant']}, {twin['txn_date']}, {money(twin['jumlah'])}).\n"
            "_Kalau ini struk yang sama, tekan Batalkan._"
        )

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
    await asyncio.to_thread(excel_report.build_workbook, row["period"],
                            row["telegram_user_id"])
    await _push_to_cloud()

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

async def _putuskan_akses(query, context: ContextTypes.DEFAULT_TYPE,
                          keputusan: str, target: int) -> None:
    """Admin menekan Setujui / Tolak pada notifikasi permintaan akses."""
    penekan = query.from_user.id
    admin = db.get_user(penekan)
    if admin is None or admin["role"] != db.ROLE_ADMIN or admin["status"] != db.STATUS_ACTIVE:
        await query.edit_message_text("🚫 Hanya admin yang bisa memutuskan ini.")
        return

    row = db.get_user(target)
    if row is None:
        await query.edit_message_text("Permintaan itu sudah tidak ada.")
        return

    # Admin lain mungkin sudah memutuskan lebih dulu.
    if row["status"] != db.STATUS_PENDING:
        sudah = "disetujui" if row["status"] == db.STATUS_ACTIVE else "ditolak"
        await query.edit_message_text(
            f"ℹ️ Permintaan *{row['nama']}* sudah {sudah} sebelumnya.",
            parse_mode="Markdown",
        )
        return

    setuju = keputusan == "ok"
    db.decide_access(target, db.STATUS_ACTIVE if setuju else db.STATUS_REVOKED, penekan)

    await query.edit_message_text(
        f"{'✅ Disetujui' if setuju else '❌ Ditolak'}: *{row['nama']}* "
        f"(`{target}`)\n_oleh {admin['nama']}_",
        parse_mode="Markdown",
    )

    kabar = (
        "✅ Aksesmu disetujui!\n\nLangkah pertama: isi identitasmu supaya kepala "
        "form Excel terisi benar —\n"
        "`/profil <NPK> | <Jabatan> | <Departemen> | <Perusahaan>`\n\n"
        "Setelah itu kirim /start untuk melihat semua perintah."
        if setuju else
        "❌ Permintaan aksesmu ditolak admin."
    )
    try:
        await context.bot.send_message(chat_id=target, text=kabar, parse_mode="Markdown")
    except Exception:
        log.warning("Tidak bisa memberi tahu karyawan %s", target)


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    if not is_allowed(update):
        await query.edit_message_text("🚫 Akses ditolak.")
        return

    action, _, payload = query.data.partition(":")

    # Persetujuan akses ditangani lebih dulu: penekannya harus admin, dan
    # pemeriksaan is_allowed di bawah tidak berlaku untuk yang diputuskan.
    if action == "acc":
        keputusan, _, raw_id = payload.partition(":")
        await _putuskan_akses(query, context, keputusan, int(raw_id))
        return

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
        pdf_path = await asyncio.to_thread(receipt_image.build_contact_sheet, period)
        recipients = await asyncio.to_thread(
            mailer.send_report, period, excel_path, zip_path, pdf_path,
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

    # Semai admin pertama dari .env. Tanpa ini sistem persetujuan buntu:
    # tidak ada yang berwenang menyetujui permintaan akses pertama.
    for admin_id in config.ALLOWED_USER_IDS:
        db.ensure_admin(admin_id)
    if db.admin_ids():
        log.info("Admin aktif: %s", db.admin_ids())
    else:
        log.warning("Belum ada admin. Isi ALLOWED_TELEGRAM_USER_IDS di .env.")

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
    app.add_handler(CommandHandler("lembar", cmd_lembar))
    app.add_handler(CommandHandler("dashboard", cmd_dashboard))
    app.add_handler(CommandHandler("sync", cmd_sync))
    app.add_handler(CommandHandler("profil", cmd_profil))
    app.add_handler(CommandHandler("users", cmd_users))
    app.add_handler(CommandHandler("cabut", cmd_cabut))
    app.add_handler(CommandHandler("aktifkan", cmd_aktifkan))
    app.add_handler(CommandHandler("jadikanadmin", cmd_jadikanadmin))
    app.add_handler(CommandHandler("kirim", cmd_kirim))
    app.add_handler(CommandHandler("list", cmd_list))
    app.add_handler(CommandHandler("hapus", cmd_hapus))
    app.add_handler(CommandHandler("batal", cmd_batal))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # Python 3.12+ tidak lagi membuat event loop otomatis di main thread,
    # sementara run_polling() masih memanggil asyncio.get_event_loop().
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())

    log.info("Bot berjalan. Tekan Ctrl+C untuk berhenti.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
