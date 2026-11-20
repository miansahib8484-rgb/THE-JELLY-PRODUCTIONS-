# Security notes

## What this system never holds

There is **no card data** anywhere: students pay by EasyPaisa, JazzCash or bank transfer outside the site and only send a transaction ID
and a screenshot. The site stores the transaction ID, the wallet or account number the student paid from, and the screenshot (in private
storage, visible only to that student and the administrators). Tell students to hide card numbers in screenshots they upload.

## What is protected, and how

| Risk | Protection |
|---|---|
| Wrong person opens a page | Every dashboard view checks the role (`role_required`). A teacher sees only their own courses, a student only their own enrolments. Tested with a brand-new teacher and student against every object URL: nothing opens. |
| Payment proofs, certificates, recordings | Stored in `private_media/` (no public URL) and sent only after a permission check. Sent with `nosniff`, never as a web page. |
| Three separate sign-ins | Students, teachers and administrators sign in at their own page (`?as=student|teacher|admin`). The server checks the account's real role, never what the browser claims, and access to every dashboard is still checked on every page. A **wrong password** reveals nothing about the account. Only a person who typed the **right** password is told why they were refused ("these details belong to a Teacher account, use the Teacher sign-in", or "deactivated"): this is deliberate, because an unexplained "incorrect password" at the wrong tab confused real users, and it gives an attacker nothing they could not get by trying the three tabs. Every refusal still counts for the lock-out. |
| Password guessing | 8 failed sign-ins per address and account, 30 per address, **and 80 per account from all addresses together** lock sign-in for 10 minutes. Passwords are hashed (PBKDF2), must be strong, and are limited to 128 characters. Changing a password is rate limited per address, so a stolen session cannot guess the old password. |
| Forms abused by bots | Hidden honeypot field, per-address rate limits on register, contact, newsletter, enrolment, payment proof, reviews, promo codes and certificate-code checks. |
| Hostile text in fields | Every text box has a maximum length (also set in the HTML). One-line fields refuse line breaks, null bytes and "right-to-left override" characters (used to forge e-mail headers, log lines and look-alike names). Text typed by visitors and students may not contain HTML tags. Everything is escaped again when shown. Phone, transaction ID, account number and promo code have a fixed format. |
| Hostile uploads | Allowed extensions and maximum sizes, the real file type is checked (a `.png` that is really HTML is refused), SVG is refused, images are re-read by Pillow (huge "image bombs" are refused), file names are cleaned. |
| Cross-site scripting | Django escapes all output. The only `|safe` places are blog and legal pages (cleaned by `nh3`) and the JSON-LD block (escaped). A strict Content-Security-Policy with a per-request nonce is sent on every page. |
| Cross-site request forgery | CSRF token on every form; a stale token shows a friendly page (never a bare 403). |
| SQL injection | Only the Django ORM is used (the one raw query is a fixed `SELECT 1` health check). |
| Clickjacking, sniffing, downgrade | `X-Frame-Options: DENY`, `X-Content-Type-Options`, HTTPS redirect, HSTS, `Referrer-Policy`, `Permissions-Policy` (camera, microphone, location off), secure and HttpOnly cookies. |
| Cached private pages | Pages shown to a signed-in user are sent with `Cache-Control: no-store`, so the Back button after signing out (or a shared computer or proxy) never shows somebody's dashboard. |
| Spreadsheet formula injection | CSV exports prefix dangerous cells. |
| Open redirects, host-header attacks | Redirect targets are checked; only the hosts in `DJANGO_ALLOWED_HOSTS` are served. |
| Errors that leak information | `DEBUG` is off in production; the 400, 403, 404, 429 and 500 pages never show code or settings; a failure inside an error page falls back to plain text. |
| Nobody noticing an attack | Failed sign-ins, lock-outs, rate limits, denied access and CSRF failures are written to the `jelly.security` log (one line each, without passwords and without the typed text). |
| Known-vulnerable packages | Versions are pinned. CI runs `pip-audit` and `bandit` on every push. |

## What YOU must do on the live server

1. Set `DJANGO_SECRET_KEY` (long and random), `DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS`, `SITE_URL`; leave `DJANGO_DEBUG` unset.
2. Serve the site over **HTTPS** (Caddy or nginx in front, `DJANGO_BEHIND_PROXY=1`). Never expose gunicorn directly to the internet.
3. **Limit the request size at the proxy.** Django limits form text to 10 MB, but only the proxy can stop a huge upload early. Recordings may be up
   to 500 MB, so allow that only where needed:
   ```nginx
   client_max_body_size 12m;                       # normal pages
   location /teacher/ { client_max_body_size 520m; }   # recording uploads
   location /admin-panel/ { client_max_body_size 520m; }
   ```
4. Students who share one internet address (a classroom, a lab, an office, mobile data) count as ONE visitor for the per-address limits. On a
   workshop day set `THROTTLE_MULTIPLIER=3` (all limits times 3) in `.env`.
5. Use Redis for the rate limits (`REDIS_URL`; the supplied `docker-compose.yml` does): without it each worker counts on its own.
6. Change the Django admin address (`DJANGO_ADMIN_URL`) and, if you can, allow `/admin-panel/` and the admin address only from your own IP at the proxy.
7. A password you type for a new teacher or student is used as it is (nobody is forced to change it). Pick a different, strong password for each person, give it to them privately (not by public chat), and tell them to change it in Settings. Tick "Ask them to choose their own password" when you want it changed at the first sign-in.
8. Give every administrator a long, unique password. The site has **no two-factor sign-in**; if you want one, put the admin pages behind a VPN or
   an access proxy (Cloudflare Access, Tailscale) or add a TOTP package.
9. Keep backups (database, `media/`, `private_media/`) and test a restore. Keep Python, Django and the packages updated (`pip-audit` tells you).
10. Watch the log: `docker compose logs web | grep jelly.security`. Many `login_failed` or `rate_limited` lines from one address means an attack
   (block it at the proxy or with fail2ban).

## Known limits (honest list)

* Registration tells a visitor that an e-mail address is already registered (usual for sites without e-mail confirmation); it is rate limited.
* E-mail addresses are not confirmed at sign-up.
* The page style (`style-src`) still allows inline styles, which the design uses; scripts are strictly nonce-based.
