# Attendance

A small attendance tool for one company. Employees sign in and press one
button to clock in or out; the server records the time. Admins review the
record, decide requests, and set schedules and holidays. Admins are the
owners: they keep no attendance of their own and never clock in.

Built with Django. SQLite by default, Postgres optionally. The UI is
server-rendered HTML styled with a compiled Tailwind stylesheet.

## What it does

- **Clock in / clock out** with one button. The time always comes from the
  server and is stored in UTC, then shown in the company timezone.
- **Weekly hours target** per schedule (for example "BD: 35h, Mon-Fri").
  Each week shows hours worked against the target and whether it was completed.
- **Holidays and leave lower the target** pro rata. With 35h over 5 days, a
  week with one holiday has a 28h target.
- **Missing-time requests.** Someone who forgot to punch submits the times and
  a reason. Once an admin approves, the time is added and stays marked as
  manual, with the reason attached.
- **Leave requests** (annual, casual, sick, unpaid, other).
- **Leave allowance.** A number of days per leave type per calendar year, the
  same for everyone, set on the Leave page. Employees see what they have left.
  A request that goes over is not blocked; it is flagged for the admin to decide.
  There is no accrual, carry-over or pro-rating for people who join mid-year.
- **Calendar** per employee: each day is present, absent, leave, holiday,
  day off or missing a clock-out.
- **Breaks.** Clocking out and back in on the same day shows as a break line
  between the two entries, with its length. Breaks are not counted as work.
- **Timeline chart.** Each day drawn as a bar along the hours of the day: work,
  breaks, a marker for a missing clock-out, and a line for the present moment.
  The admin's Today page has one bar per employee; each employee's month has
  one bar per day. Hover or tap a stretch for its times.
- **Admin pages** under `/manage/`: who is in right now, attendance by week or
  by month, CSV exports, the request inbox, employees, schedules, holidays
  and leave.
- **Monthly summary** per employee: expected days, present, absent, leave,
  holidays, hours worked, target and difference. It prints cleanly and exports
  to CSV. A month still running is counted up to yesterday.
- **Admin corrections.** From an employee's page an admin can add time, correct
  an entry or remove one, always with a reason. The entry is marked as an admin
  correction and the old values are kept in a history the employee can see.
- **Schedule overview** for admins: every employee's expected hours on each
  day of a chosen week, with days off, holidays and approved leave, and the
  resulting weekly target.
- **Schedule history.** Changing a schedule's hours or days, or moving someone
  to another schedule, takes effect from a chosen week. Earlier weeks keep
  being judged by what applied at the time.
- **Email notices** over SMTP: admins hear about new requests; employees hear
  when a request is decided or their record is corrected.
- **Sign-in lockout.** Five wrong passwords within 15 minutes lock that
  username for 15 minutes. An admin setting a new password unlocks it.
- **Install on a phone.** The site can be added to the home screen and then
  opens like an app. It still needs a connection: nothing works offline.
- **Pakistan's public holidays** are loaded on first start. Fixed-date ones
  repeat every year. Eid, Ashura and Eid Milad un-Nabi are estimated and marked
  tentative, because the real dates depend on moon sighting; correct them on
  the Holidays page when they are announced.

## Rules worth knowing

- A forgotten clock-out is flagged when the day ends. That entry counts zero
  hours until an approved request supplies the time, and the button offers
  "Clock in" again the next morning.
- Several clock-ins a day are fine, so a lunch break is clocking out and back in.
- Weeks run Monday to Sunday. A week still in progress is never shown as short.
- Days before an employee's start date do not count against them.
- An employee with no schedule has their hours recorded but is never marked
  absent or short.
- Schedule changes start on the Monday of the week you pick, so a week is
  always judged by one schedule. To correct a mistake in a schedule, date the
  change from when the mistake began; that replaces anything set after it.
- Only working days that are not holidays use up leave.
- Employees are deactivated, not deleted, so the record is kept.
- An admin account sees only the admin pages and is left out of every
  attendance list. Ticking "Admin" on an employee removes their schedule.

## Run it locally

Needs Python 3.10 or newer (developed and tested on 3.13).

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

## Deploy with Docker

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

### With Postgres

Set `POSTGRES_PASSWORD` in `.env`, then:

```
docker compose -f docker-compose.yml -f docker-compose.postgres.yml up -d --build
```

To use a Postgres server you already have, set `DATABASE_URL` in `.env` and
use the plain `docker compose up`.

### Email

Email is off until `EMAIL_HOST` is set. Fill in the `EMAIL_*` lines in `.env`
with the SMTP details from your mail provider, then restart. If your provider
gives an API key for SMTP, the key is the password, and the username is the
fixed one the provider documents (often `apikey`).

Check it works with:

```
docker compose exec web python manage.py sendtestemail you@example.com
```

Notices go to the email address on each account, so add addresses on the
Employees page. People without one simply get no email. If the mail server
fails, the error is logged and the app carries on.

### Installing on phones

Phones only offer to install a site served over HTTPS. Once it is:

- Android (Chrome): open the site and use "Add to home screen" on the clock
  page, or the browser menu's "Install app".
- iPhone (Safari): Share, then "Add to Home Screen".

### Backups

- SQLite: stop the app, copy the file out, start it again:
  `docker compose stop && docker compose cp web:/app/data/db.sqlite3 ./backup.sqlite3 && docker compose start`
- Postgres: `pg_dump`.

## Settings

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
| `DATABASE_URL` | empty | Empty means SQLite. `postgres://user:pass@host:5432/name` for Postgres. |
| `EMAIL_HOST` | empty | SMTP server. Empty means no email is sent. |
| `EMAIL_PORT` | `587` | 587 uses STARTTLS. For 465 also set `EMAIL_USE_SSL=true`. |
| `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD` | empty | SMTP login. An SMTP API key goes in the password. |
| `EMAIL_FROM` | the SMTP user | Address notices are sent from. |
| `ADMIN_USERNAME`, `ADMIN_PASSWORD` | empty | Docker only: creates the first admin if none exists. |

## Changing the look

The stylesheet at `attendance/static/attendance/app.css` is compiled from
`assets/tailwind.css` and the class names used in the templates. It is
committed, so the server needs no Node. After editing templates or styles:

```
npm install        # once
npm run css:build  # or css:watch while working
```

## Tests

```
python manage.py test
```

The suite covers punching, weekly targets, schedule history, requests,
corrections, leave allowances, email notices, the sign-in lockout, permissions
and the pages. To run it against Postgres, set `DATABASE_URL` first.

The app icons are drawn by `assets/make_icons.py` (needs Pillow, which the app
itself does not use). Run it only if you want to change the icon.

## Repairing data

`/admin/` is Django's raw data editor, open to the first admin account. Edits
made there skip the request-and-approval trail, so keep it for fixing mistakes.
