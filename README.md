# The Jelly Productions: Django project

One Django project, split into small apps:

| App | What it owns | URL prefix |
|---|---|---|
| `website` | Public site (no login): home, courses, course detail, about, contact, blog, enroll, newsletter | `/` |
| `accounts` | Register, login, logout, password reset, profile, password change, notifications (shared by all roles) | `/account/` |
| `student` | Student dashboard | `/student/` |
| `teacher` | Teacher dashboard | `/teacher/` |
| `adminpanel` | Admin dashboard | `/admin-panel/` |
| `core` | Shared models, permissions, sidebar menus, template tags, protected file downloads, and the views that teacher and admin share (courses, builder, live classes, recordings, certificates) | `/files/` |

Anything two dashboards have in common lives in `core` or `templates/dashboard/` (one base layout, one sidebar, one set of table and pagination partials).
Teacher and admin share `core/portal.py`: a teacher is scoped to their own courses, an admin sees everything.

## Run it

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
export DJANGO_DEBUG=1                              # development only; Windows PowerShell: $env:DJANGO_DEBUG="1". Without it Django asks for DJANGO_SECRET_KEY
python manage.py migrate           # includes the announcement, site-content and blog tables
python manage.py seed_data          # demo courses, classes, payments (refuses to run unless DJANGO_DEBUG=1)
python manage.py runserver
python manage.py test               # 350 tests
```

**Windows (`cmd`)**, one command per line, without the `#` comments:

```
cd /d C:\path\to\jelly_django
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
set DJANGO_DEBUG=1
python manage.py migrate
python manage.py seed_data
python manage.py runserver
```

In PowerShell write `$env:DJANGO_DEBUG="1"` instead of `set DJANGO_DEBUG=1`. `set` only lasts for the window you typed it in: type it again in
every new window.

**Start with your own data.** `seed_data` and the first `migrate` fill the site with demo content. To remove it and add your real data, create your
own administrator and clean up (see `GO_LIVE_CHECKLIST.md` for the order in which to add things):

```
python manage.py createsuperuser
python manage.py clean_demo_data                       # shows what would be removed
python manage.py clean_demo_data --yes --reset-site
```

**Three sign-ins.** The header's **Sign in** menu (and the sign-in page itself) has three choices: **Student**, **Teacher** and **Admin**. Each person signs in at
their own one (`/account/login/?as=student`, `?as=teacher`, `?as=admin`) and lands on their own dashboard. An account used at the wrong sign-in is refused
with the same message as a wrong password. A protected page (for example `/teacher/`) opens the matching sign-in by itself.

**Cannot sign in?** Both the user name and the e-mail work, but the person must use the sign-in of their role (the site remembers the last one that worked).
If the page says "These details belong to an Admin account", click the button it shows. Run `python manage.py check_login <user name or e-mail>` to see the
account, its role, which sign-in to use and whether it is active; add `--password "..."` to test a password or `--set-password "..."` to set a new one.
Create your first administrator with `python manage.py createsuperuser` (the user name must not contain spaces).

Demo logins (change before going live; use the sign-in that matches the role): `admin@thejellyproductions.com / admin12345`,
`arshad@thejellyproductions.com / teacher12345`, `ayesha@example.com / student12345`.
Everyone signs in at `/account/login/` (email or username) and lands on their own dashboard.
`/django-admin/` is the built-in Django admin for raw data.

## Blog (database, written in the admin dashboard)

Articles are written in **Admin dashboard > Blog** (Markdown body, cover image, category, featured flag, publish date). Drafts and posts with a
future publish date stay hidden until their time. The Markdown is sanitised before it reaches visitors (no scripts, no `javascript:` links).

The files in `content/sample_blog/` are only the *sample* articles of the original design: the first `migrate` (and **Load sample content** in
Admin > Site content) turn them into ordinary blog rows flagged as sample; **Remove sample content** deletes just those rows.

## Key behaviour

* **Live classes**: teacher or admin schedule a class with a Meet, Zoom or Teams link. Enrolled students see the time (Pakistan time) on their dashboard, a countdown, and a Join button that opens 10 minutes before the start.
* **Recordings**: after a class ends, teacher or admin upload a link (YouTube, Drive, Vimeo) or a video file. Students of that course watch it on `/student/live/?status=recordings`.
* **Certificates**: teacher or admin upload a PDF or image for an enrolled student; issuing marks the enrollment completed. Students view or download it.
* **Course images**: add a course in Teacher or Admin > Courses with an image; it shows on the public course cards immediately (unless saved as a draft).
* **Payments**: students reserve a seat on `/enroll/` (EasyPaisa, JazzCash or bank transfer, optional promo code); an admin approves or rejects under Payments, which unlocks the course.
* **Notifications**: created automatically (payment decisions, new live class, recording ready, certificate, new contact message).

## Admin dashboard (fully dynamic)

Every number, chart and list in `/admin-panel/` is computed from the database on each request (`adminpanel/stats.py`).

* **Overview**: 8 KPI cards (revenue, students, enrollments, pending payments, courses, teachers, messages, subscribers) with month-over-month trend, a 6-month revenue/enrollment/student chart, a recent-activity feed, upcoming classes, payments to review, popular courses.
* **Live refresh**: the KPI numbers and the red sidebar badges (pending payments, new messages) update every 30 seconds from `/admin-panel/api/summary/` with no page reload.
* **Reports**: date-range filter (30 days, 90 days, 12 months, all time), revenue by payment method, best-selling courses, enrollment funnel, searchable student progress, and CSV exports for payments, enrollments and students.
* **People**: click any student or teacher to open a detail page (enrollments, payments, certificates or courses taught) and edit their details.
* **Reviews**: moderate course reviews; deleting one recalculates the course rating.
* **Site settings**: promo codes and testimonials can now be edited and deleted, not just added.

## Teacher dashboard (fully dynamic)

Everything in `/teacher/` is computed from the database and scoped to the signed-in teacher's own courses (`teacher/stats.py`). A new teacher with no courses sees zeros and empty states, never sample data.

* **Dashboard**: earnings and active students with month-over-month trend, rating, completion rate, courses, upcoming classes, students awaiting payment, recordings still to upload; a 6-month earnings/students chart; a "needs your attention" list built from live data (missing recordings, classes without a meeting link, live courses with no curriculum or no open batch, finished students waiting for a certificate); recent activity, course table, latest reviews, and a countdown to the next class.
* **Live refresh**: the numbers and the red badge on "Live classes" update every 30 seconds from `/teacher/api/summary/`.
* **Students**: filter by course, status and search; CSV export; click a student for module-by-module progress, payment status, certificate and review.
* **Announcements**: send a message to every student of a course or one batch (each gets a notification); history is kept (new `Announcement` model, migration `0003`).
* **Reviews**: filter by course and star rating, per-course averages.
* **Performance**: earnings per course, completion, average progress, 12-month chart, list of approved payments, CSV export.
* Avatars are generated from the person's initials and courses without an image use a local placeholder: no random stock photos are loaded from the internet.

## Student dashboard (fully dynamic)

Everything in `/student/` is computed from the student's own records (`student/stats.py`). A new student sees zeros and empty states.

* **Dashboard**: next live class with countdown and join button (or the start date of the next batch), 8 live numbers (courses, lectures done, average progress, certificates, classes this week, study streak, payments to action, unread notifications), "Resume" button for the next unfinished lecture, a 14-day learning chart, a "what to do next" list (payment due or rejected, payment under review, certificate pending, rate your course, class live now), teacher announcements, class timings, recordings, certificates and course cards.
* **Live refresh**: numbers plus the red badges on Billing (payments to action) and Live classes (class live now) update every 30 seconds from `/student/api/summary/`.
* **Billing**: cancel an unpaid seat (frees the batch seat), submit or fix payment proof.
* **Reviews**: edit or delete your reviews; the course rating on the public site is recalculated at once.

## How the dashboards and the public website are linked

| Someone does this | ...and it shows up here |
|---|---|
| Teacher or admin publishes a course, adds a batch | Public Courses page, home page, ticker ("Next batch: ... N seats left", "Save up to X%") |
| Visitor reserves a seat on `/enroll/` | Admin: Payments + notification + live badge; Student: Billing + "under review" |
| Admin approves the payment | Student: course unlocked, dashboard numbers, class links; Teacher: student list; Public: live student count |
| Student reviews a course (dashboard or website) | Public course page + rating, Teacher reviews, Admin reviews |
| Visitor sends the contact form / subscribes | Admin: Contact inbox / Newsletter + badge |
| Admin edits **Site content** | Public site name, contact details, social links, hero, About page, partners strip, timeline, gallery, FAQs, ticker lines, batch size and refund text |
| Admin writes an article in **Blog** | Public blog and home page (drafts and scheduled posts stay hidden) |
| Teacher sends an **Announcement** | Notification and dashboard card for the students of that course or batch |
| Teacher or admin issues a certificate | Student certificates page, "certificate" stat on the public site |

The public site shows no invented text: stats (students, courses, rating, completion, certificates) are counted from the database, instructors are the real teacher accounts, and every block that needs your content (partners, timeline, gallery, FAQs, testimonials, blog) stays hidden until you add something. `seed_data` is for demos only; for a real launch skip it, run `createsuperuser`, then fill in Admin > Site content.

## The original design is back (v9)

v5-v8 had replaced the original public design with a plain one (flat colour header, no photos, shorter pages). That was wrong: the original pages, copy and photos are the owner's design. They are restored, and nothing about the dynamic dashboards was lost.

* **Same design, checked by running both projects:** the original upload and this version were run side by side and every public page was compared. Home, About, Courses, Contact, Blog, a course page, Login and Register have the identical section sequence, headings, number of images and number of photos. 88% of all visible text is word for word the same; the rest is deliberately live data (stat band numbers, next batch, seats left, discount, popular searches). `static/css/` and the logo were never touched.
* **Everything is editable, nothing is hard-wired:** the original copy (hero text, About story, founder note, footer, contact details, batch size 18, refund window 14 days) are the *defaults* of Admin > Site content. The three-photo hero carousel, partners, timeline, team, gallery, FAQs (14), quote cards (3), testimonials (3) and 7 articles are ordinary rows you can edit or delete in Admin (new tabs: Hero slides, Team, Quotes). Rows that came with the design carry a "sample" flag: **Remove sample content** deletes only those, **Load sample content** puts them back into empty lists, and rows you add yourself are never touched.
* **Stock photos:** like the original, empty picture slots (hero, course cards, avatars, article images) show stock photos from picsum.photos. One checkbox, "Use stock photos", turns them all off (plain colour backgrounds and initials). Uploading your own picture always wins.
* **Claims follow your data:** "One instructor" becomes "2 Instructors" when a second teacher publishes a course; batch size and refund days come from Site content; the instructor on the home page is the founder you describe there, or your busiest real teacher if you clear it; real teacher accounts appear on the About page next to the team cards.
* **Dashboards:** the three dashboards still use the original layout and classes (checked the same way: nothing the original used is gone). The "Students by level" card that v4 dropped is back.
* A fresh `migrate` loads the sample content (skipped when `JELLY_NO_SAMPLE=1` or under the test-suite). If you do not want the sample articles, pictures or team on a live site, click **Remove sample content** once.

## Review fixes (v6)

Every item of the v4 review was re-checked against the code first; the ones that reproduced got a failing test (`core/tests_regression.py`), then a fix.

| # | Fixed |
|---|---|
| 1 | Teacher earnings no longer multiplied by review count (separate grouped queries) |
| 2 | `DJANGO_DEBUG` is off by default, `DJANGO_SECRET_KEY` is required (the server refuses to start without it), HTTPS/HSTS/secure cookies in production |
| 3, 8, 26 | Enrollment KPIs use one definition; Reports date range applies to every block and the search term survives a range change |
| 4, 5 | Approved payments cannot be rejected, rejected ones cannot be approved without a re-submission, cancelled enrollments cannot be re-paid or re-approved |
| 6, 10 | Bulk progress / certificate / class queries; tests prove dashboard query counts do not grow with the number of students |
| 7 | CSV exports neutralise `= + - @` cells (also the newsletter export) |
| 9 | Student dashboard refreshes every 30 s |
| 11 | Notification links use `reverse()` |
| 12 | `require_https` on every redirect check |
| 13 | Real refund flow (payment and enrollment become *refunded*, seat freed); cancelling an unpaid enrollment closes its pending payment |
| 14 | Passwords that an administrator types for a new teacher or student are validated (strength, length) and used as they are; the welcome e-mail never contains the password. Tick "Ask them to choose their own password" only if you want it changed at the first sign-in |
| 15 | One certificate per student and course (database constraint, old duplicates removed by the migration); students are told when a class time changes |
| 16 | Edit screens for batches, modules and lectures (status, seats, order); a batch becomes *full* and re-opens automatically |
| 17 | "Approve as teacher" in the contact inbox, "Make teacher / Make student" and "Send password-reset link" on a user's page |
| 18 | `interested_course` and enrollment `notes` are saved and shown to admins |
| 19 | Terms, Privacy and Refund pages (written in Admin > Site content > Legal pages; empty = hidden), linked from the footer, register and enroll |
| 20 | Files are deleted from disk when their record is deleted or the file is replaced |
| 21, 22 | Rate limiting (login, register, contact, newsletter, promo check) and a honeypot on the public forms; promo codes have a usage limit |
| 23 | Live classes: no past start, 10 min to 8 h, no overlap for the same teacher |
| 24 | A course with any enrollment, payment or certificate record cannot be deleted |
| 25 | Pill tabs remember the open tab after saving |
| 27 | "N enrolled" counts paid learners only and seat counters are recounted from the real enrollments (signals + a data migration) |
| 28 | Hardcoded claims replaced by live data and Site content (v5) |
| 29, 30 | Meta description, canonical, Open Graph, favicon, Course JSON-LD, `sitemap.xml`, `robots.txt`, custom 404 and 500 pages |
| 31 | Blog and legal Markdown is sanitised (no scripts, no `javascript:` links); future-dated posts stay hidden until their time |
| 32 | Course preview video accepts a YouTube link or 11-character id only |
| 33 | Enroll page works with no published course |
| E-mail | Payment approved / rejected / refunded / cancelled, teacher approved, teacher account created, class time changed, password reset. SMTP is configured with `EMAIL_HOST` |
| Certificates | Each certificate has a public verification code and `/verify/<code>/` page |
| Newsletter | Unsubscribe link page and re-subscribe |

**Not built (these are new features, not bugs):** assignments and project grading, live-class attendance, quizzes and lecture attachments, private lecture video upload, 1:1 messaging and review replies, manual student creation and manual enrollment by the admin, audit log, 2FA, newsletter sending, EasyPaisa/JazzCash API payments, e-mail verification at sign-up. Payments stay manual: the student sends proof and the admin approves it.

## Audit (v7)

The delivered zip was audited again from a fresh extract, by running things rather than reading them:

* `pyflakes` (no undefined names, unused imports removed), `check --deploy`, `makemigrations --check`, migrations 0006/0007 run on a database that held duplicate certificates and subscribers.
* Real gunicorn in production mode: HTTPS redirect, HSTS, static files via WhiteNoise, custom 404, wrong `Host` refused, path traversal on `/media/` blocked, private folders not reachable.
* Every one of the 148 routes requested as anonymous, admin, teacher and student (no 5xx, no wrong access); every POST endpoint sent empty and hostile data (294 requests, no 5xx); 3,476 forbidden-zone POSTs (student, teacher and anonymous against zones that are not theirs) and 460 cross-account (IDOR) requests, database unchanged afterwards.
* Private files: who can open a payment proof, certificate and recording (owner, other student, course teacher, other teacher, admin, anonymous) plus `Range` requests.
* All 252 pages of a brand-new empty install, and all pages of the demo install, scanned for template leaks and unclosed tags; a stored-XSS payload planted in every text field and searched for in 592 page renders.
* Dashboard numbers compared with independent raw SQL (17 of 17 equal); query counts compared before and after growing the data five-fold.

What the audit found and fixed:

| Found | Fix |
|---|---|
| **Stored XSS** in the Course JSON-LD block (`'\u003c'` is the character `<` in Python source, so the escape did nothing; the earlier test only checked the tag name) | Escaped with `chr(92)`; regression test proves a `</script>` title cannot break out |
| Student review form returned 500 for a malformed course id | Parsed safely |
| A student could hold two seats in one course (two batches, two payments); a paid student could enrol again in another batch | One seat per student and course: choosing another batch moves the reservation and releases the old seat |
| Enrol view let a student with a *cancelled* record take a batch that had filled up (only the form's status filter stopped it) | The view checks the seat count itself |
| Promo discount rounded half-even in the database but half-up in the page's JavaScript (could differ by one rupee) | Server rounds half-up to whole rupees |
| Login throttle also counted successful sign-ins, so a classroom behind one address could be locked out | Only failed attempts count (per account and per address) |
| Uploads were checked by file name only | Images and PDFs are also checked by their first bytes |
| N+1 queries: blog list (one query per post), admin overview, payments, live classes, review feed; the admin overview scanned every live class ever created | Fixed, tests keep them flat |
| Admin charts counted unpaid reservations as enrolments; the 12-month chart ran its queries twice | Same definition everywhere, computed once |
| One notification link was still a hard-coded path; refund/cancel e-mails read awkwardly; bulk e-mails opened one SMTP connection per student; the "PKT" label was hard-coded | `reverse()`, clearer wording, one connection, label follows `TIME_ZONE` |
| `/media/` served from a folder captured at import time | Read per request |

## Django-expert audit (v8)

Checked with: `pip-audit`, `bandit`, `ruff` (Django `DJ`, bugbear `B`, security `S` rules), `pyflakes`, `check --deploy`, `makemigrations --check`, the test-suite run with warnings turned into errors, `coverage` (96% of 3,492 statements; every file in `core/services.py`, `teacher/`, `student/stats` and the e-mail/throttle helpers is fully covered), reversibility of every migration, and a CSP compliance check over all rendered pages.

| Area | Found | Fixed |
|---|---|---|
| Dependencies | Pillow 12.1.1 had 35 published advisories and it parses every uploaded image | Pinned to a patched release (see `requirements.txt`), `pip-audit` reports none |
| Data integrity | `Payment`, `Enrollment` and `Certificate` used `CASCADE`: deleting a user or course silently erased money records | `PROTECT` (deactivate users instead); tests prove it |
| Data integrity | No database-level rules: a rating of 7, a 0% or 250% promo, a negative price or payment, 0 seats could be stored by any code path | `CHECK` constraints for all of them; the migration first clamps legacy rows so `migrate` cannot fail on an old database |
| Performance | No indexes on the columns every dashboard filters by (`status`, `starts_at`, `is_read`, `created_at` ...) | 11 composite indexes; a test asserts they exist |
| Transactions | E-mails were sent over SMTP *inside* database transactions (row locks held while waiting for the mail server, mail sent even when the change rolled back) | `transaction.on_commit`; tests for commit and rollback |
| Transactions | A view that failed half way left half-written data (proved: a certificate stayed behind) | `ATOMIC_REQUESTS=True` |
| Settings | `EMAIL_TIMEOUT` was unlimited, password-reset links lived three days, private files were world-readable on disk (`0644`) | 10 s, 2 hours, `0600`/`0700` |
| Security | `EmailOrUsernameBackend` returned instantly for unknown accounts: login timing revealed which e-mail addresses are registered | Runs the hasher like Django's own backend |
| Security | Admin "mark paid" redirected to any `next=` value (open redirect) | Host-checked |
| Security | `fields = '__all__'` on two ModelForms (a field added later would become editable by accident) | Explicit field lists |
| Security | No Content-Security-Policy, and Bootstrap/Chart.js came from a CDN without integrity checks | Both vendored under `static/vendor/` (MIT, licences included); nonce-based CSP (`script-src 'self' 'nonce-…'`, no `unsafe-inline`/`unsafe-eval` for scripts, `object-src 'none'`, `frame-ancestors 'none'`, `form-action 'self'`); all 101 rendered pages verified compliant. `DJANGO_CSP=report` or `off` if you ever need to |
| Django admin | Superusers could change a payment's status or delete records in `/django-admin/`, bypassing every business rule; list pages queried per row | Money, enrollments and certificates are read-only there (only notes editable); `DJANGO_ADMIN_URL` moves it to a private address |
| Deprecation | `URLField` assumed `http://` (changes in Django 6) | `HttpsURLField`: a link typed as `meet.google.com/x` becomes `https://…` |
| Operations | No health probe, Docker image ran as root, expired sessions never purged | `/healthz/` (database check, exempt from the HTTPS redirect), non-root image with `HEALTHCHECK`, `clearsessions` documented, optional e-mail of 500 tracebacks (`DJANGO_ADMINS`) |
| Hygiene | Dead function, silent `except: pass` on file deletion, missing `__str__`, `raise` without `from` | Fixed |

Not done on purpose: `DJ012` (ordering of methods inside models) is cosmetic; Material Symbols and Google Fonts are still loaded from Google (they are CSS and font files, not code, and are allowed by the CSP); the Docker image and `docker-compose.yml` were written but could not be built in this environment.

## Review fixes (v10)

Every item was reproduced first, then fixed; each has a test in `core/tests_fixes.py`.

| Fixed |
|---|
| `docker compose up` returned 500 on every page: the Redis cache backend needs the `redis` package, which the image did not install. Compose now binds gunicorn to `127.0.0.1` only (put nginx/Caddy with TLS in front) |
| Promo `max_uses` could be exceeded: codes already promised to pending reservations now count, and approval re-checks the limit |
| Editing a course whose instructor is inactive silently removed the instructor |
| "Remove sample content" deleted rows the admin had edited; an edited sample row now becomes the admin's own |
| Password reset had no rate limit (e-mail bombing); now 10 per hour per address |
| Unpaid reservations held seats forever: `python manage.py expire_reservations` (cron, hourly) releases reservations with no payment proof after `RESERVATION_HOLD_HOURS` (default 72; 0 = off) |
| Revenue was booked in the month the seat was reserved, not the month it was approved (admin and teacher dashboards, reports, charts) |
| Duplicate promo code in another letter case, and a profile e-mail equal to another account's username, returned 500; both are now form errors |
| Registration accepted future birth dates and ignored "similar to your name/e-mail" password checks |
| `Range: bytes=-N` on a small file and multi-range requests returned 416 |
| Deleting a certificate left the enrollment "completed"; a double-clicked certificate form raised 500 |
| Moving to another batch e-mailed "Enrollment cancelled" |
| Blog list loaded every article body; sitemap used a non-existent `updated_at`; dead `details_for` removed |

| Newsletter form no longer reveals whether an address is already subscribed; student "Live classes" no longer loads every class ever created; icon-font fallback is clipped so a blocked Google Fonts cannot stretch dashboards on phones |

Also checked, no change needed: full suite on PostgreSQL 16 plus real race tests (`core/tests_concurrency.py`: six students racing for the last seat, four admins approving one payment, a promo limit under parallel approvals; skipped on SQLite), and a real Chromium run over 35 pages as admin, teacher, student and visitor (no JS errors, no CSP violations, no 4xx/5xx, no horizontal scroll at 390 px).

Cron example: `0 * * * * cd /app && python manage.py expire_reservations && python manage.py clearsessions`

## Deploying

1. `cp .env.example .env` and fill it in (secret key, hosts, `SITE_URL`, SMTP). `DJANGO_DEBUG` stays unset.
2. `docker compose up --build` (Postgres + Redis + gunicorn), or run `gunicorn config.wsgi` behind nginx. Static files are served by WhiteNoise after `python manage.py collectstatic`.
3. `python manage.py createsuperuser`, then fill in Admin > Site content, Blog, Payments & promos. Do **not** run `seed_data` on a live site.
4. With `docker compose up` the **`cron` service already runs `expire_reservations` and `clearsessions` every hour**; without Docker, run them from cron (example below). Django keeps sessions in the database and never purges expired ones by itself: Django keeps sessions in the database and never purges expired ones by itself. `/healthz/` answers 200 when the database is reachable (used by the Docker health check).
5. Public uploads (`media/`) are served by Django by default; set `DJANGO_SERVE_MEDIA=0` and let nginx or object storage serve them for heavy traffic. `private_media/` (payment proofs, certificates, recordings) is never served directly. Back up the database and both folders.
6. Local development: `DJANGO_DEBUG=1 python manage.py runserver`.

## Security notes

* Certificates, payment proofs and recording files are stored in `private_media/`, which has no URL. They are served only by `core/files.py` after a permission check (owner, teacher of the course, admin, or enrolled student).
* Nobody can make themselves a teacher by registering: "I want to teach" creates a student account and an application in the admin's Contact inbox.
* Uploads are validated by type and size (see `MAX_*` in `config/settings.py`). Logout is POST only. Redirect targets are checked.
* Before deploying: set `DJANGO_SECRET_KEY`, `DJANGO_DEBUG=0`, `DJANGO_ALLOWED_HOSTS`; configure SMTP (password reset emails print to the console by default); serve `media/` from your web server (for large recordings use your server's `X-Accel-Redirect` or `X-Sendfile` instead of Django); use PostgreSQL for real traffic.

## Not included

Online payment gateways (payments are manual approval), SMS or WhatsApp reminders, and migration of data from the old `jelly_site` project.
"# THE-JELLY-PRODUCTIONS-" 
