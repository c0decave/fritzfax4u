# FRITZ!Box fax over HTTP — the protocol fritzfax uses

Everything below was measured on a **FRITZ!Box 7590, FRITZ!OS 8.25** (154.08.25, rev. 130856):
firmware image from `download.avm.de`, unpacked offline, plus live requests against the box.
Firmware references are paths inside the unpacked root file system.

There is no official API for sending faxes. fritzfax speaks the dialect of the box's own web
interface (`fon_devices/fax_send.lua`), which the box offers on every current FRITZ!OS. It does
**not** need CAPI over TCP (port 5031, closed by default and reset by firmware updates; that is
why Roger Router and similar tools fail) and no SIP account.

## Login

`login_sid.lua?version=2` returns a challenge `2$<iter1>$<salt1>$<iter2>$<salt2>`.
Response: `<salt2>$hex(PBKDF2-SHA256(PBKDF2-SHA256(password, salt1, iter1), salt2, iter2))`,
posted with `username`. Older boxes send an MD5 challenge: `<challenge>-md5(utf16le("<challenge>-<password>"))`.
A SID of sixteen zeros means failure; `BlockTime` > 0 means the box blocks logins for a while.

**Expired / invalid SID:** Lua pages answer `303 See Other`, `Location: /`; the upload CGI answers
`302`, `Location: /index.lua`. fritzfax logs in again once and repeats the request.

**TLS:** the box uses a self-signed certificate. fritzfax pins its SHA-256 fingerprint.
Measured 2026-10-26: the box had made a new certificate during the night (valid from 03:37, subject =
its public IP address), and the pin of the day before no longer matched. The pin is then renewed with
`fritzfax trust` or under Settings - Connection. Whether the box renews it on every change of the public
address, and whether the key pair stays the same, is not measured yet.

## Sending

1. **Read the setup** — `GET /fon_devices/fax_send.lua?sid=…` (HTML). Parsed values:
   `var g_mode="fax_write"` (configured), `<select name="src_num">` options (fax numbers of the box),
   `data.identifier`, `data.send_short`, `<textarea name="from1">`, `var tfaNeeded`, `var g_pbe`
   (phone book fax entries).
2. **Precheck** — `POST /fon_devices/fax_send.lua` with `btn_send=`, `dest_num=<number>`, `from1=<name>`
   → `{"data":{"needs2FA":bool,"btn_send":"ok"}}` or `{"data":{"btn_send":"valerror","valerror":{…}}}`.
   Measured: numbers without area code and international numbers need 2FA, `0<area>…` does not.
3. **2FA if needed** — `POST /twofactor.lua` `tfa_start=` → `{"state":"button,dtmf;<code>"}`
   (press a button on the box, or dial `*1<code>` on a box phone; optional TOTP via
   `tfa_googleauth=<code>`). Poll `tfa_active=` until `{"done":true,"active":true}`.
   `state` `starterror;91` = another session confirms, `starterror;92` = too many attempts.
   One confirmation covers one action.
4. **Upload** — `POST /cgi-bin/firmwarecfg` (multipart) with `sid`, `NumDest`, `NumSrc` and
   `FaxUploadFile` = **base64 of an SFF document, as a text field**. Success page contains
   `name="progress"`; failure page shows the status text in parentheses (for example
   "Die Ausführung muss zusätzlich bestätigt werden." when 2FA is missing).
   **The box does not validate the payload before dialing.** A non-SFF upload was accepted, the
   destination was called, and the transmission then failed with reason `0x3319`. fritzfax checks
   the SFF structure locally and never uploads broken data.
5. **Progress** — `GET /fon_devices/fax_send.lua?sid=…&no_sidrenew=1&myXhr=1&query=refresh`
   → `{"status":n,"status_text":"FAXSEND_…","progress":p,"reason":r}`.

   | status | text | meaning |
   |---|---|---|
   | -1, 0 | FAXSEND_UNAVAILABLE | error |
   | 2, 3 | FAXSEND_PREPARE, FAXSEND_CONNECTING | dialing |
   | 4 | FAXSEND_CONNECTED | transmitting |
   | 1, 5 | FAXSEND_READY, FAXSEND_REPORT | finished; `reason` 0 = success, else a CAPI cause |

   Measured: `progress` jumps to 100 at once and stays there during the whole transmission
   (about 30 s per page). A watchdog therefore needs a per-page time budget.
   Measured 2026-10-26: a failing one-page call stayed CONNECTED at 100 % for about 8 minutes, then
   the box ended it by itself (`FAXSEND_READY`, reason `0x3301`, journal error). `reason` can also
   become non-zero while the status is still 4. So when the budget runs out, the box still owns the
   result: fritzfax keeps waiting for it (`faxapi.OVERDUE_GRACE`) and only then reports the result
   as unknown, never as a failure the box did not report.
   The status shows the result of the *last* fax until the next one starts, so "finished" only
   counts after an active state was seen.

## The SFF document

Encoder: `rootfs/usr/www/avm/js/sffcoder.js`; page rendering: `js/text2canvas.js`.

* file header `Sfff`, version 1, first page header at offset 28
* page header `FE 10`, vertical resolution 1 (fine, 7.7 lines/mm), 1728 pixels per line
* rows: ITU-T T.4 Modified Huffman (1D), LSB first, byte aligned; length prefix 1..216 or `00 lo hi`
* `FE 00` ends the document; several pages are fine

The web interface renders pages of 1728 × 2444 (square pixels). The box records such pages as
595 × 897 pt, 6.5 % longer than A4. fritzfax renders 1728 × 2287 lines (297 mm × 7.7), and the
box records them as 595 × 840 pt. Verified with a received fax.

The web interface allows one picture per fax only because its `<input type=file>` lacks
`multiple`. The upload itself takes any number of pages.

## Fax settings

Read via `GET /query.lua?sid=…&<name>=telcfg:settings/<Key>` (`FaxKennung`, `FaxSenderShort`,
`FaxSenderLong`, `FaxMSN0..9`, `FaxMailActive`, `FaxMailAddress0`, `FaxSendOnly`, `FaxSwitch`,
`Location/LKZ`, `Location/OKZ`).
The box keeps its own fax numbers without area code (`FaxMSN0` = `87654321`, and the fax page lists them
the same way); upload them like that as `NumSrc`. fritzfax shows them with `LKZ`/`OKZ` as `+49 30 87654321`.

Write via `POST /fon_devices/edit_fax_intern.lua` with `apply=`. Its `do_save()` writes **every**
field (a POST without `num_1` clears the fax numbers), so fritzfax serializes the complete form of
the page like a browser and changes only the requested fields. Answers:
`{"data":{"apply":"ok"|"valerror"|"twofactor"|"saveerror", …}}`.

## Journal

`/api/v0/faxjournal` exists but is not in the access list of `rest_api/security.lua` and answers
"permission denied". The web interface uses `POST /data.lua` `page=faxOverview&xhr=1`
→ `data.faxList.faxList[]` with `date_time` (id), `date`, `number`, `msn`, `sent`, `pages`,
`error`, `unread`, `path`. `path` downloads the fax as PDF
(`/cgi-bin/luacgi_notimeout?script=/lua/photo.lua&myfaxfile=…`). Delete and mark as read:
`delete=`/`markSeen=` with `faxID=<date_time>`.
