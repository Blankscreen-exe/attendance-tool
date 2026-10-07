<h1 align="center">Attendance</h1>

<p align="center">
  A self-hosted attendance tool for small teams.<br>
  One button to clock in and out, and a clear record for the person who runs the place.
</p>

<p align="center">
  <img alt="Python 3.10+" src="https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white">
  <img alt="Django 5.2" src="https://img.shields.io/badge/Django-5.2-092E20?logo=django&logoColor=white">
  <img alt="Tailwind CSS 4" src="https://img.shields.io/badge/Tailwind_CSS-4-06B6D4?logo=tailwindcss&logoColor=white">
  <img alt="SQLite or PostgreSQL" src="https://img.shields.io/badge/SQLite_%7C_PostgreSQL-supported-336791?logo=postgresql&logoColor=white">
  <img alt="Docker ready" src="https://img.shields.io/badge/Docker-ready-2496ED?logo=docker&logoColor=white">
  <img alt="MIT licence" src="https://img.shields.io/badge/License-MIT-green">
</p>

<p align="center">
  <img src="docs/screenshots/clock.png" alt="The employee's clock page, with one large Clock out button" width="49%">
  <img src="docs/screenshots/today.png" alt="The admin's Today page, with a timeline bar for each employee" width="49%">
</p>

## Why it exists

Full HR suites do attendance, payroll, recruitment and a dozen other things,
and they are heavy to set up and to use. This project does one job: it records
when people work, and shows whether the agreed hours were done.

- **Employees** sign in and press one button. They cannot type in a time.
- **The admin** sees who is in, reviews any week or month, decides requests,
  and sets schedules, holidays and leave.
- **It runs on one small server**, as a single container with a SQLite file,
  or against PostgreSQL if you prefer.

## Features

**For employees**

- One clock in / clock out button. The server records the time.
- Today's hours and the week's progress against the target.
- A month calendar: present, absent, leave, holiday, day off or missing clock-out.
- A timeline of each day, with breaks shown between sessions.
- Requests for missing time (with a reason) and for leave, with the leave
  balance left for the year.
- Installable on a phone's home screen.

**For the admin**

- **Today:** who is clocked in right now, with a timeline bar per employee.
- **Attendance by week and by month:** hours against target for any range,
  with CSV export and a print-friendly monthly summary.
- **Requests inbox:** approve or reject with a note. Leave that would exceed
  the yearly allowance is flagged, not blocked.
- **Corrections:** add, correct or remove an entry with a reason. The old
  values are kept in a history the employee can see.
- **Schedules:** weekly hours and working days, with a week-by-week view of
  who is expected when. Changes apply from a chosen week onward.
- **Holidays:** an editable list that starts with Pakistan's public holidays.
  Eid, Ashura and Eid Milad un-Nabi are estimated and marked tentative until
  confirmed.
- **Leave:** yearly allowance per leave type and everyone's balance.
- **Email:** notices over SMTP, with a test button that explains failures.

**Built in**

- Sign-in lockout after repeated wrong passwords.
- Admins are owners: they keep no attendance of their own and never clock in.
- SQLite by default, PostgreSQL through one setting.

## Screenshots

Every person and number in these is fictional demo data.

| | |
|---|---|
| **Month calendar and timeline**<br>Each week shows hours against its target.<br><br><img src="docs/screenshots/calendar.png" alt="An employee's month calendar with a timeline of each day"> | **Attendance by week**<br>Green met the target, red fell short, blue is still running.<br><br><img src="docs/screenshots/attendance-weekly.png" alt="A grid of employees and weeks with hours against target"> |
| **Requests**<br>Missing time and leave, with the leave balance beside each request.<br><br><img src="docs/screenshots/requests.png" alt="The admin's request inbox with approve and reject buttons"> | **Schedules**<br>Who is expected when, and how each schedule has changed over time.<br><br><img src="docs/screenshots/schedules.png" alt="A table of employees and the hours expected on each day of the week"> |
| **Monthly summary**<br>Days and hours per employee, ready to print or export.<br><br><img src="docs/screenshots/attendance-monthly.png" alt="A monthly summary table of days present, absent and hours worked"> | **Leave**<br>Yearly allowances and what each person has used.<br><br><img src="docs/screenshots/leave.png" alt="Leave allowances and each employee's balance"> |

<p align="center">
  <img src="docs/screenshots/clock-phone.png" alt="The clock page on a phone" width="300">
</p>

## How it works

A few decisions shape the whole project.

**The server owns the clock.** The button sends only "in" or "out". The time
is taken on the server, stored in UTC and shown in the company timezone, so a
wrong or altered device clock changes nothing. The button also says what it
meant, so a double tap can never clock someone in and straight back out.

**One calculation, many views.** Every calendar cell, weekly total, monthly
summary and CSV row comes from the same function, which works out each day
from four sources: time entries, approved leave, holidays and the schedule in
force that week. Nothing derived is stored, so the views cannot disagree.

**The past is not rewritten.** A schedule's hours and days are kept as dated
versions, and so is each person's assignment to a schedule. Raising a target
from next week leaves earlier weeks judged by what applied at the time.
Corrections work the same way: the entry changes, and the old values stay in
a history.

**The database refuses what must never happen.** A partial unique index allows
one open entry per employee, and check constraints keep a clock-out after its
clock-in, on SQLite and PostgreSQL alike.

**Nothing to babysit.** A forgotten clock-out is flagged the next time anyone
opens the app, so no scheduler has to be running for the record to be right.
That entry counts zero hours until a request or a correction supplies the time.

**Requests instead of edits.** Employees never enter time directly. A missed
punch becomes a request with a reason; once approved it stays marked as
manual, with the reason attached.

**Tested on both databases.** The test suite covers the attendance rules,
schedule history, requests, corrections, leave, email, the lockout,
permissions and every page, and passes on SQLite and PostgreSQL. One test
migrates old data forward to prove the schedule history migration keeps it.

## Tech stack

| Layer | Choice | Why |
|---|---|---|
| Backend | Django 5.2 (Python 3.10+) | Auth, sessions, migrations and an ORM that runs unchanged on both databases |
| Database | SQLite, or PostgreSQL | One file for a small office; a real server when wanted |
| Frontend | Server-rendered templates, compiled Tailwind CSS 4 | No build step on the server, no single-page app for what is mostly one button |
| JavaScript | Under a hundred lines, no framework | The live clock and the chart tooltips |
| Deployment | Docker, gunicorn, WhiteNoise | One container that serves the app and its static files |

## Getting started

### Run it locally

```
python -m venv .venv
.venv\Scripts\activate            # Windows; on Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
echo DEBUG=true > .env
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Open http://localhost:8000, sign in, then add a schedule under Schedules and
people under Employees.

### Deploy with Docker

```
cp .env.example .env              # then fill in SECRET_KEY, ALLOWED_HOSTS, ADMIN_PASSWORD
docker compose up -d --build
```

The app listens on port 8000. On first start it creates the database and the
admin account named in `.env`. The SQLite file lives in the `attendance_data`
volume.

Put a reverse proxy that terminates HTTPS in front of it (Caddy, nginx or
Traefik), and set `HTTPS=true` and `CSRF_TRUSTED_ORIGINS` in `.env`.

To upgrade: pull the new code and run `docker compose up -d --build` again.
Migrations are applied on start.

### With PostgreSQL

Set `POSTGRES_PASSWORD` in `.env`, then:

```
docker compose -f docker-compose.yml -f docker-compose.postgres.yml up -d --build
```

To use a PostgreSQL server you already have, set `DATABASE_URL` in `.env` and
use the plain `docker compose up`.

## Configuration

All set through environment variables or `.env`.

| Variable | Default | Meaning |
|---|---|---|
| `SECRET_KEY` | none | Required unless `DEBUG=true`. |
| `DEBUG` | `false` | Development mode. Never enable on a server. |
| `ALLOWED_HOSTS` | `localhost` | Hostnames the app answers to, comma separated. |
| `CSRF_TRUSTED_ORIGINS` | empty | e.g. `https://attendance.example.com`, when behind an HTTPS proxy. |
| `HTTPS` | `false` | Marks cookies secure and trusts the proxy's `X-Forwarded-Proto`. |
| `TIME_ZONE` | `Asia/Karachi` | Company timezone. |
| `DAY_ROLLOVER_HOUR` | `0` | Hour at which the attendance day ends. Raise it (e.g. `5`) if people work past midnight, so late work stays on the day it started. |
| `DATABASE_URL` | empty | Empty means SQLite. `postgres://user:pass@host:5432/name` for PostgreSQL. |
| `EMAIL_HOST` | empty | SMTP server. Empty means no email is sent. |
| `EMAIL_PORT` | `587` | 587 uses STARTTLS. For 465 also set `EMAIL_USE_SSL=true`. |
| `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD` | empty | SMTP login. An SMTP API key goes in the password. |
| `EMAIL_FROM` | the SMTP user | Address notices are sent from. |
| `ADMIN_USERNAME`, `ADMIN_PASSWORD` | empty | Docker only: creates the first admin if none exists. |

### Email

Email is off until `EMAIL_HOST` is set. Fill in the `EMAIL_*` lines in `.env`
with the SMTP details from your mail provider, then restart. If your provider
gives an API key for SMTP, the key is the password.

To check it works, sign in as an admin and open the **Email** page. It shows
the mail server in use (never the password) and has a "Send test email"
button. If sending fails, the page says why: wrong key, unreachable server,
refused sender, or a port and encryption mismatch.

Notices go to the email address on each account, so add addresses on the
Employees page. People without one simply get no email. If the mail server
fails, the error is logged and the app carries on.

### Installing on phones

Phones only offer to install a site served over HTTPS. Once it is:

- Android (Chrome): open the site and use "Add to home screen" on the clock
  page, or the browser menu's "Install app".
- iPhone (Safari): Share, then "Add to Home Screen".

It still needs a connection. Nothing works offline, because the server is
what records the time.

### Backups

- SQLite: stop the app, copy the file out, start it again:
  `docker compose stop && docker compose cp web:/app/data/db.sqlite3 ./backup.sqlite3 && docker compose start`
- PostgreSQL: `pg_dump`.

## Rules worth knowing

- A forgotten clock-out is flagged when the day ends. That entry counts zero
  hours until it is fixed, and the button offers "Clock in" again the next
  morning.
- Several clock-ins a day are fine. Clocking out and back in shows as a break,
  and breaks are not counted as work.
- Weeks run Monday to Sunday. A week still in progress is never shown as short.
- A holiday or approved leave on a working day lowers that week's target by
  one day's share. With 35 hours over 5 days, a week with one holiday has a
  28-hour target.
- Days before an employee's start date do not count against them.
- An employee with no schedule has their hours recorded but is never marked
  absent or short.
- Schedule changes start on the Monday of the week you pick, so a week is
  always judged by one schedule.
- Only working days that are not holidays use up leave. Leave has no accrual,
  carry-over or pro-rating for people who join mid-year.
- Five wrong passwords within 15 minutes lock that username for 15 minutes.
  An admin setting a new password unlocks it.
- Employees are deactivated, not deleted, so the record is kept.

## Project layout

```
attendance/
  services.py        the attendance rules: the place to start reading
  models.py          data model, including schedule history
  views.py           employee pages
  manage_views.py    admin pages
  forms.py           forms and their validation
  lockout.py         sign-in lockout
  notifications.py   email notices
  pk_holidays.py     Pakistan's holidays, with estimated Islamic dates
  pwa.py             what a phone needs to install the site
  templates/         server-rendered pages
  static/            compiled stylesheet, icons, chart tooltips
  tests.py           the test suite
config/              settings and URLs
assets/              Tailwind source and the icon generator
docs/screenshots/    the images in this file
```

## Development

Run the tests:

```
python manage.py test
```

To run them against PostgreSQL, set `DATABASE_URL` first.

The stylesheet at `attendance/static/attendance/app.css` is compiled from
`assets/tailwind.css` and the class names used in the templates. It is
committed, so the server needs no Node. After editing templates or styles:

```
npm install        # once
npm run css:build  # or css:watch while working
```

The app icons are drawn by `assets/make_icons.py` (needs Pillow, which the app
itself does not use). Run it only if you want to change the icon.

`/admin/` is Django's raw data editor, open to the first admin account. Edits
made there skip the request-and-approval trail, so keep it for fixing mistakes.

## Licence

Released under the [MIT Licence](LICENSE).
