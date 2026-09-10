# 🧾 Sistem Otomasi Reimbursement

Foto struk lewat Telegram → dibaca otomatis oleh OpenAI → masuk form Excel
**Daftar Nominatif** (2 sheet: Reimbursement & Kartu Kredit) → dikirim via email
ke petugas pengumpul saat kamu memberi perintah.

- **Author:** Juan Davis
- **Status:** Implementasi awal, siap dipakai internal

---

## Tiga Tahap

```mermaid
flowchart TD
    A[Terima invoice] -->|Foto via Telegram| B[OpenAI baca struk]
    B --> C{Reimbursement<br/>atau Kartu Kredit?}
    C -->|💵 R| D[Foto disimpan<br/>di folder R]
    C -->|💳 K| E[Foto disimpan<br/>di folder K]
    D --> F[Bot minta 1 kalimat detail]
    E --> F
    F -->|"Chen Yifei dari JA Solar,<br/>Executive, industri pendidikan"| G[AI pilah ke kolom form]
    G --> H[(SQLite)]
    H --> I[Form Excel 2 sheet]
    H --> J[ZIP foto struk]
    I --> K["/kirim"]
    J --> K
    K --> L[Email ke petugas]
```

**Tahap 1 — Capture.** Foto struk → dibaca: tanggal, merchant, alamat, kategori, jumlah.
Kamu pilih Reimbursement atau Kartu Kredit; foto langsung dipindah ke folder **R** atau **K**.

**Tahap 2 — Lengkapi.** Bot minta satu kalimat bebas, contoh:

> *Chen Yifei dan Huang Xinmin dari JA Solar, Executive, industri pendidikan, yang ikut Ariadi, Alex Janu, Galih*

AI memilahnya ke kolom Nama / Posisi / Perusahaan / Industri / Karyawan Internal.
Untuk struk non-jamuan (taksi, bensin, ATK) ketik `-` saja — kolom tamu dikosongkan.

**Tahap 3 — Kirim.** `/kirim` → konfirmasi → form Excel + ZIP foto dikirim ke email
yang terdaftar di `EMAIL_TO`.

---

## Bentuk Form Excel

Meniru template Daftar Nominatif perusahaan, dua sheet dengan struktur identik:

```
                          FORM REIMBURSEMENT
                          Periode Agustus 2026

NPK        : 1908005
JABATAN    : Directur                    setelah karyawan selesai melakukan
DEPARTEMEN : BOD                         perjalanan dinas dengan melampirkan
PERUSAHAAN : PT. Triputra Energi ...     semua bukti transaksi asli

┌────┬──────────┬──────────────────────────────┬──────────────────────┬────────┬────────┬──────────┐
│ NO │ Tanggal  │      Orang yang dijamu       │ Menjamu klien/mitra  │ Tujuan │ Jumlah │ Karyawan │
│    │          ├───────┬───────┬────────┬─────┼───────┬───────┬──────┤        │        │ Internal │
│    │          │ Nama  │Posisi │Perush. │Indus│Lokasi │Alamat │ Tipe │        │        │          │
└────┴──────────┴───────┴───────┴────────┴─────┴───────┴───────┴──────┴────────┴────────┴──────────┘

                                              SUB TOTAL              9,205,000
                                              GRAND TOTAL        Rp  9,205,000
                                              PEMBAYARAN DIMUKA  Rp          0
                                              SALDO AKHIR        Rp  9,205,000

  Diajukan oleh,        Disetujui oleh,          Diketahui oleh,
  Karyawan              Atasan                   HRGA Senior Manager
```

**Merchant dari struk masuk ke kolom `Lokasi`** — di form aslimu kolom itu memang berisi
nama restoran (Remboelan, Pagi Sore, Ko.Kuu).

Foto **tidak** disisipkan ke dalam form, supaya layout dokumen resmi tidak berubah.
Foto dikirim terpisah dalam ZIP, dinamai sesuai nomor barisnya —
`Reimbursement/003_2026-08-18_Lapis - Lapis.jpg` = baris 3 di sheet Reimbursement.

---

## Struktur File

| File | Fungsi |
|---|---|
| [bot.py](bot.py) | Bot Telegram — alur 3 tahap, command, tombol inline |
| [extractor.py](extractor.py) | OpenAI vision (baca struk) + pemilah kalimat bebas |
| [excel_report.py](excel_report.py) | Bangun form Excel 2 sheet + ZIP foto |
| [receipt_image.py](receipt_image.py) | Potong struk dari latar + susun lembar lampiran PDF |
| [dashboard.py](dashboard.py) | Jalur lokal dashboard: baca SQLite, tulis HTML |
| [web/render.py](web/render.py) | Perenderan dashboard (murni, nol dependency) — dipakai lokal & Vercel |
| [web/cloud.py](web/cloud.py) | Klien Turso lewat HTTP API |
| [web/api/index.py](web/api/index.py) | Serverless function Vercel |
| [web/middleware.js](web/middleware.js) | Basic auth di edge |
| [db.py](db.py) | Skema & query SQLite |
| [mailer.py](mailer.py) | Kirim form via SMTP dengan lampiran |
| [config.py](config.py) | Semua setting, dibaca dari `.env` |

Data tersimpan di `data/` (otomatis dibuat, sudah di-`.gitignore`):

```
data/
├── reimbursement.db
├── photos/
│   ├── inbox/          # sementara, sebelum user pilih R atau K
│   ├── R/2026-08/      # Reimbursement
│   └── K/2026-08/      # Kartu Kredit
└── exports/            # Reimbursement Juan Davis Aug 2026.xlsx, Struk_2026-08.zip
```

**SQLite adalah sumber kebenaran, bukan Excel.** File Excel di-generate ulang setiap
ada entri baru atau dihapus, jadi rekap selalu akurat dan bisa dibuat ulang kapan saja.

---

## Setup

### 1. Install dependency

```powershell
cd "C:\Users\juan.davis\Downloads\Reimbursement"
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Buat bot Telegram

Chat **@BotFather** → `/newbot` → salin token.

### 3. Isi konfigurasi

```powershell
copy .env.example .env
notepad .env
```

Wajib: `TELEGRAM_BOT_TOKEN`, `OPENAI_API_KEY`, `ALLOWED_TELEGRAM_USER_IDS`.
Isi juga identitas karyawan (`KARYAWAN_NPK`, `KARYAWAN_JABATAN`, dst) — itu yang
mengisi kepala form Excel.

> **Belum tahu Telegram user ID kamu?** Isi `ALLOWED_TELEGRAM_USER_IDS=0` dulu,
> jalankan bot, kirim `/start` — bot membalas dengan ID kamu. Masukkan ke `.env`, restart.

### 4. Jalankan

```powershell
python bot.py
```

Bot memakai **long polling** — tidak butuh server publik atau domain. Cukup jalan di
laptop atau PC kantor yang menyala.

---

## Perintah

| Perintah | Fungsi |
|---|---|
| *(kirim foto)* | Catat struk baru |
| `/rekap` | Ringkasan bulan ini |
| `/rekap 2026-07` | Ringkasan bulan tertentu |
| `/excel` | Form Excel + lampiran PDF + ZIP foto asli |
| `/lembar` | Lembar lampiran struk saja (PDF, siap cetak) |
| `/dashboard` | Dashboard tracking (HTML, buka di browser) |
| `/sync` | Kirim ulang data ke database cloud |
| `/kirim` | Email form ke `EMAIL_TO` (minta konfirmasi dulu) |
| `/list` | 10 entri terakhir beserta ID-nya |
| `/hapus 12` | Hapus entri `#12` |
| `/batal` | Batalkan struk yang sedang ditanyakan |

---

## Lembar Lampiran Struk

`/lembar` menghasilkan PDF A4 berisi semua struk satu periode: **dipotong dari
latar** (tangan, meja, lantai), diurutkan tanggal, diberi label `R-01`, `K-01`
yang cocok dengan nomor baris di form Excel. Tata letaknya adaptif — tiga struk
melebar satu baris, sembilan struk jadi grid 3×3, lebih dari itu pindah halaman.

Pemotongannya memakai fakta bahwa **kertas struk hampir tidak punya saturasi
warna**, sedangkan kulit tangan dan meja kayu selalu hangat. Deteksi lewat kanal
saturasi HSV jauh lebih andal daripada lewat kecerahan — lantai kayu yang terang
ikut terbaca sebagai "putih" kalau memakai ambang kecerahan.

Ini heuristik, bukan model AI. Akan meleset kalau struk difoto di atas **meja
putih**, atau kertasnya kusam kekuningan. Kalau gagal, kode jatuh ke foto asli
tanpa dipotong — tidak pernah memotong struknya sendiri. Parameternya
(`SAT_MAX`, `VAL_MIN`, `ROW_MAX_FRAC`) ada di atas [receipt_image.py](receipt_image.py).

> Hasil terbaik: foto di atas permukaan **gelap dan kontras**, tanpa tangan menutupi.

---

## Dashboard

Isinya:

- **Hero** — total bulan berjalan + perubahan % dari bulan lalu
- **Stat tiles** — jumlah struk, total Reimbursement, total Kartu Kredit, struk belum lengkap
- **Grafik batang** — pengeluaran per bulan, ditumpuk per jenis pembayaran, dengan tooltip
- **Rekap per karyawan** — tiap karyawan × bulan, dipecah R/K
- **Tabel struk** bulan berjalan

Ikut mode gelap/terang browser. Warna dua deretnya sudah divalidasi aman untuk
buta warna di kedua mode.

Bisa dipakai dua cara, dengan **kode perenderan yang sama persis**:

| | Lokal | Vercel |
|---|---|---|
| Perintah | `/dashboard` | buka URL-nya |
| Sumber data | SQLite di laptop | Turso (database cloud) |
| Hasil | file HTML mandiri | halaman live |
| Akses | file di mesinmu | basic auth |

`web/render.py` berisi `summarize()` dan `render()` yang murni — bekerja di atas
daftar dict biasa, tidak tahu-menahu soal sumber datanya. `dashboard.py` memberinya
baris dari SQLite; `web/api/index.py` memberinya baris dari Turso. Satu kode, dua jalur.

---

## Deploy Dashboard ke Vercel

> ⚠️ Dashboard berisi **nominal asli, nama merchant, nama klien yang dijamu, dan NPK**.
> Vercel itu hosting publik. Basic auth di `web/middleware.js` berjalan di *edge* —
> sebelum serverless function mana pun dijalankan — jadi pengunjung yang belum lolos
> tidak menerima satu byte pun data. Kalau `DASH_USER`/`DASH_PASS` belum diisi,
> seluruh situs ditutup, bukan dibuka.

### 1. Buat database cloud (Turso, gratis)

1. Daftar di https://turso.tech
2. **Create Database** → pilih region terdekat (Singapore)
3. **Connect** → salin **Database URL** dan **Auth Token**
4. Masukkan ke `.env` lokal sebagai `TURSO_DATABASE_URL` dan `TURSO_AUTH_TOKEN`
5. Restart bot, lalu kirim **`/sync`** di Telegram

Setelah itu tiap entri baru otomatis ikut tersalin — `/sync` hanya perlu sekali di awal
atau kalau ingin memaksa kirim ulang semuanya.

### 2. Deploy

```powershell
npm i -g vercel
cd "C:\Users\juan.davis\Downloads\Reimbursement\web"
vercel
```

Saat ditanya, jawab: link ke project baru, **Root Directory = folder ini** (`web`).

### 3. Isi Environment Variables di Vercel

Dashboard → project → **Settings → Environment Variables**:

| Variable | Isi |
|---|---|
| `TURSO_DATABASE_URL` | sama dengan di `.env` |
| `TURSO_AUTH_TOKEN` | sama dengan di `.env` |
| `DASH_USER` | username untuk membuka dashboard |
| `DASH_PASS` | password — **panjang dan acak**, bukan password yang kamu pakai di tempat lain |
| `KARYAWAN_NAMA`, `KARYAWAN_NPK`, `KARYAWAN_DEPARTEMEN`, `PERUSAHAAN` | untuk kepala halaman |

Lalu **Redeploy** supaya variabelnya terbaca.

```powershell
vercel --prod
```

### Kenapa `web/` folder terpisah

Vercel meng-install `requirements.txt` dan membatasi bundle serverless 250 MB.
Kalau di-deploy dari root, `opencv`, `Pillow`, `openpyxl`, `openai`, dan
`python-telegram-bot` ikut terpasang dan deploy gagal karena ukuran.

`web/` sengaja **nol dependency** — hanya pustaka standar Python: `urllib` untuk
menghubungi Turso, `html`/`datetime` untuk merender. `web/requirements.txt`
memang kosong.

### Apa yang TIDAK ikut ke cloud

`cloud.py` hanya menyalin 20 kolom yang dibutuhkan dashboard. **Foto struk tidak
pernah meninggalkan laptopmu** — `photo_path` dan `raw_json` sengaja dikecualikan.
Yang tersalin hanya angka dan teks rekap.

SQLite lokal tetap sumber kebenaran. Sinkronisasi satu arah dan idempoten: baris
yang dihapus di lokal ikut terhapus di cloud pada sync berikutnya. Kalau internet
mati, bot tetap jalan penuh — kegagalan sync dicatat di log, tidak menghentikan apa pun.

---

## Catatan Keamanan

Sistem ini menyimpan data finansial asli, jadi:

- **`.env` dan `data/` tidak pernah masuk git** — sudah diatur di `.gitignore`.
- Bot memakai **allowlist**: hanya user ID di `ALLOWED_TELEGRAM_USER_IDS` yang dilayani.
  Default kosong, artinya menolak semua orang sampai sengaja diisi — bot Telegram itu
  publik, siapa pun yang tahu namanya bisa mengirim chat.
- Struk duplikat ditangkap dua lapis: **hash SHA-256** menolak file foto yang
  identik, dan kecocokan **merchant + tanggal + nominal** memberi peringatan saat
  struk yang sama difoto ulang (byte berbeda, hash lolos). Lapis kedua hanya
  memperingatkan, tidak menolak — dua struk identik yang sah memang mungkin.
- Hapus bersifat **soft delete** — data tetap ada di database untuk jejak audit.
- Untuk Gmail, gunakan **App Password**, bukan password akun utama.

---

## Pengembangan Berikutnya

| Ide | Kenapa berguna |
|---|---|
| Ingat profil tamu | Ketik "Chen Yifei" saja, posisi/perusahaan/industri terisi otomatis |
| Import Excel/CSV | Input massal untuk struk yang sudah terlanjur dicatat manual |
| Baca dari email | Invoice yang masuk lewat email ikut terproses otomatis |
| Edit lewat chat | `/edit 12 jumlah 500000` tanpa perlu buka Excel |
| Jadwal otomatis | Kirim form tiap tanggal 25 tanpa `/kirim` manual |
