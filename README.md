# Library Management System

A full CRUD web app for managing a library's books, members, and
borrow/return workflow, with real authentication, role-based access
control, and book cover art and synopses pulled live from Open Library —
Flask, SQLite, and server-rendered HTML/CSS with no frontend framework.

## Features

- **Authentication** — register/login/logout with hashed passwords
  (Werkzeug), session-based auth, and CSRF protection on every form
- **Roles** — `admin` (full access: manage books, members, and loans) vs.
  `member` (browse and search the catalog only); enforced server-side on
  every route, not just hidden in the UI
- **Books** — a cover-art gallery grid with search-as-you-type suggestions
  (title/author, shown with cover thumbnails), pagination, CSV export, and
  a detail page per book showing its full info, loan history (admins), and
  an auto-fetched synopsis
- **Book covers & descriptions** — resolved lazily from the [Open
  Library](https://openlibrary.org/dev/docs/api/books) API the first time
  a book is viewed, then cached in the database so each title is only
  looked up once; lookups run concurrently so a page full of uncached
  books doesn't hang waiting on them one at a time
- **Members** (admin only) — add, edit, delete, and search members
- **Loans** (admin only) — borrow a book for a member via search-picker
  dropdowns (scales fine to a large catalog — no giant `<select>`), mark a
  loan returned, filter by borrowed/overdue/returned, search, CSV export;
  available copies update automatically on both actions
- **Dashboard** (admin only) — live counts (titles, total copies, members,
  currently borrowed, overdue), an instant category picker, and a list of
  active loans with overdue ones highlighted
- **Rate limiting** — login/register capped per IP to slow brute-force
  attempts

## Data model

- `users (id, username, password_hash, role, created_at)`
- `books (id, title, author, isbn, category, total_copies, available_copies, cover_url, description)`
- `members (id, name, email, phone, joined_date)`
- `loans (id, book_id, member_id, borrow_date, due_date, return_date, status)`

Borrowing decrements `available_copies` and inserts a `loans` row;
returning does the reverse and stamps `return_date`. A book can't be
borrowed once its `available_copies` reaches zero.

## Run it

```bash
pip install -r requirements.txt
cp .env.example .env
python -c "import secrets; print(secrets.token_hex(32))"   # paste the output into .env as SECRET_KEY
python app.py
```

Then open `http://localhost:5050`. The SQLite database (`library.db`) is
created automatically on first run. The first person to register on a
fresh database... doesn't get admin automatically — promote an account to
admin directly in the database if you need one:

```bash
python -c "import database as db; db.get_connection().execute(\"UPDATE users SET role='admin' WHERE username='YOUR_USERNAME'\").connection.commit()"
```

## Tests

```bash
pip install -r requirements-dev.txt
pytest -v
```

Tests run against an isolated temp SQLite database per test (never the
real `library.db`), covering auth, role-based access control, and the
borrow/return flow. A GitHub Actions workflow (`.github/workflows/ci.yml`)
runs the suite on every push and pull request.

## Stack

Python · Flask · SQLite (stdlib `sqlite3`) · Jinja2 · HTML/CSS ·
Flask-WTF (CSRF) · Flask-Limiter (rate limiting) · python-dotenv ·
pytest · Open Library API (covers & descriptions)

No JavaScript framework or build step — the handful of interactive bits
(search suggestions, the category picker, delete confirmations) are small
scripts embedded directly in their templates.
