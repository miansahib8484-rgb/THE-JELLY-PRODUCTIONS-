# Start with your own data

The demo data (fake courses, students, payments, team, articles, phone numbers) is only there so you can see how the site works.
This page is the order in which to replace it with your real data. Every step happens in the browser, in the **Admin dashboard**
(`/admin-panel/`). Menu names below are the names you see in the left sidebar.

## 1. Clean the demo data (once)

Create your own administrator first, then clean:

```
python manage.py createsuperuser
python manage.py clean_demo_data                  # only shows what it would remove
python manage.py clean_demo_data --yes --reset-site
```

* The command removes only records it can recognise as demo. Anything you added yourself stays.
* The demo administrator (`admin@thejellyproductions.com`) stays until you have created your own, so you can never lock yourself out.
* `--reset-site` empties the placeholder texts (phone, e-mail, founder biography ...), hides "batch size" and "refund days", switches stock
  photos off, and puts short neutral text in the page headers so they are not empty.
* Starting a brand-new database instead? Set `JELLY_NO_SAMPLE=1` before the first `migrate`, then run
  `python manage.py clean_demo_data --yes --reset-site` once.
* Windows `cmd`: set variables with `set NAME=value`; PowerShell: `$env:NAME="value"`.

## 2. Add your real data, in this order

| # | Where in the sidebar | What to add |
|---|---|---|
| 1 | **Account & password** | Your own name and a strong password. |
| 2 | **Site content** | Site name, tagline, e-mail, phone/WhatsApp, address, reply time, social links, the home and About texts, your photo, logo pictures, FAQs, team, partners, gallery. Anything you leave empty is hidden on the public site. |
| 3 | **Payments & promos** | The EasyPaisa / JazzCash / bank account numbers students must pay to, and promo codes. Students see these when they enrol: **fill them before you open enrolment.** |
| 4 | **Teachers** and **Students** | Add each person with their e-mail and a password **you type and tell them** (the page has an "Add a teacher" / "Add a student" box). They can sign in at once with it and change it any time in Settings; tick "Ask them to choose their own password" only if you want that at the first sign-in. If you teach yourself, also create a teacher account for yourself. Students can also register on the website themselves. |
| 5 | **Courses** | Each course: title, price, description, picture, YouTube preview. Then open it (**Builder**) to add modules and lessons and the **batches** (start date, seats). |
| 6 | **Live classes** | Class date/time and the Zoom / Meet link; after the class, upload the recording or paste its link. |
| 7 | **Blog** | Your own articles (optional). |

A new student registers on the website, books a seat, pays and uploads the payment proof. You approve it in **Payments**; from that moment
the course is open for them.

Sign-in: everybody uses the **Sign in** menu in the website header and picks **Student**, **Teacher** or **Admin**. Tell your teachers and students
which one is theirs; if they pick the wrong one they see "Incorrect email/username or password for the ... sign-in".

## 3. Texts in the design that you should read once

Some sentences are part of the original design and are not stored in Site content. They are general marketing lines, so check each one is
true for your school, or ask to have it changed:

* Home page: "One Instructor", "Easy Refunds / Honest, no-fuss refunds", "Four steps, six to ten weeks, one finished thing".
* About page: the three "What we actually optimize for" cards (small batches, refunds are easy).
* Course page: "Lifetime access to materials", "Weekly review with your instructor", "Finished portfolio project".
* Footer: "Made by a small team who still answers your emails personally."

## 4. Before the public sees it

* `DJANGO_DEBUG` must **not** be set on the live server; set `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS`,
  `SITE_URL` and the e-mail (SMTP) settings (see `.env.example`).
* Never run `seed_data` on the live site (it creates accounts with public passwords).
* Make a backup of the database and of the `media/` and `private_media/` folders regularly.
