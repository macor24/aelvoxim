/**
 * Message timestamp helpers.
 *
 * Two different shapes reach the UI and they MUST end up on the same timeline:
 *
 *  1. Locally created messages: `new Date().toISOString()` → real UTC, e.g.
 *     "2026-09-15T13:12:50.123Z" (has the Z offset → unambiguous).
 *  2. Messages loaded from the backend: the API serialises a naive Postgres
 *     timestamp with `str(row)`, e.g. "2026-09-12 11:23:23.210514" — server
 *     local wall clock (Asia/Shanghai, UTC+8), no offset, 6 fractional digits.
 *
 * JavaScript parses an offset-less string as the VIEWER's local time, so a
 * viewer outside +08:00 would see a time shifted by the difference, and
 * reloaded history would contradict messages just sent in the same thread.
 * Offset-less values are therefore pinned to +08:00 before parsing, and the
 * sub-millisecond digits (outside the spec's 3-digit allowance) are truncated.
 *
 * Displayed times are always the viewer's local time.
 */

const SERVER_TZ_OFFSET = '+08:00';
const HAS_OFFSET = /(?:Z|[+-]\d{2}:?\d{2})$/i;

/** Parse a backend/local timestamp into an instant, or null if unusable. */
export function parseMessageTime(raw?: string | null): Date | null {
  if (raw === undefined || raw === null) return null;
  let s = String(raw).trim();
  if (!s) return null;
  s = s.replace(' ', 'T');
  if (!HAS_OFFSET.test(s)) {
    s = s.replace(/(\.\d{3})\d+/, '$1') + SERVER_TZ_OFFSET;
  }
  const d = new Date(s);
  return isNaN(d.getTime()) ? null : d;
}

function pad(n: number): string {
  return String(n).padStart(2, '0');
}

/**
 * Short label next to the copy button: the reply time of this message.
 * Today → "21:12"; another day this year → "9/12 21:12"; older → "2025/9/12 21:12".
 */
export function formatMessageTime(d: Date | null, now: Date = new Date()): string {
  if (!d) return '';
  const clock = pad(d.getHours()) + ':' + pad(d.getMinutes());
  const sameDay =
    d.getFullYear() === now.getFullYear() &&
    d.getMonth() === now.getMonth() &&
    d.getDate() === now.getDate();
  if (sameDay) return clock;
  const md = (d.getMonth() + 1) + '/' + d.getDate() + ' ' + clock;
  return d.getFullYear() === now.getFullYear() ? md : d.getFullYear() + '/' + md;
}

/** Full timestamp for the hover tooltip: "2026-09-12 11:23:23". */
export function formatFullTime(d: Date | null): string {
  if (!d) return '';
  return (
    d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()) +
    ' ' + pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds())
  );
}
