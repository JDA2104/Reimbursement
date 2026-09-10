// Basic auth di tepi jaringan - dijalankan sebelum serverless function apa pun,
// jadi tidak ada satu byte data pun terkirim ke pengunjung yang belum lolos.
//
// Kredensial diambil dari environment variable DASH_USER dan DASH_PASS.
// Kalau salah satu belum diisi, seluruh situs ditutup - lebih aman daripada
// terbuka tanpa sadar.

export const config = { matcher: '/:path*' };

// Perbandingan yang waktunya tidak bergantung isi, supaya tidak bisa ditebak
// karakter demi karakter lewat pengukuran waktu respons.
function samaAman(a, b) {
  if (a.length !== b.length) return false;
  let beda = 0;
  for (let i = 0; i < a.length; i++) beda |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return beda === 0;
}

function mintaLogin() {
  return new Response('Perlu autentikasi.', {
    status: 401,
    headers: {
      'WWW-Authenticate': 'Basic realm="Dashboard Reimbursement", charset="UTF-8"',
      'Cache-Control': 'no-store',
    },
  });
}

export default function middleware(request) {
  const user = process.env.DASH_USER;
  const pass = process.env.DASH_PASS;

  if (!user || !pass) {
    return new Response(
      'DASH_USER dan DASH_PASS belum diisi di Environment Variables Vercel. ' +
      'Situs ditutup sampai keduanya diatur.',
      { status: 503, headers: { 'Cache-Control': 'no-store' } },
    );
  }

  const header = request.headers.get('authorization') || '';
  if (!header.startsWith('Basic ')) return mintaLogin();

  let terkirim;
  try {
    terkirim = atob(header.slice(6));
  } catch {
    return mintaLogin();
  }

  const pemisah = terkirim.indexOf(':');
  if (pemisah < 0) return mintaLogin();

  const namaOk = samaAman(terkirim.slice(0, pemisah), user);
  const sandiOk = samaAman(terkirim.slice(pemisah + 1), pass);

  // Sengaja mengevaluasi keduanya lebih dulu, bukan hubung-singkat, supaya
  // lama respons tidak membocorkan mana yang salah.
  return namaOk && sandiOk ? undefined : mintaLogin();
}
