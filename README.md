# Library Management System

A CRUD web app for managing a library's books, members, and borrow/return
workflow — Flask, SQLite, and server-rendered HTML/CSS.

## Features

- **Books** — add, edit, delete, and search titles; each book tracks total
  vs. available copies
- **Members** — add, edit, delete, and search members
- **Loans** — borrow a book for a member (14-day loan period), mark a loan
  returned; available copies update automatically on both actions
- **Dashboard** — live counts (titles, total copies, members, currently
  borrowed, overdue) and a list of active loans, with overdue loans
  highlighted

## Data model

- `books (id, title, author, isbn, category, total_copies, available_copies)`
- `members (id, name, email, phone, joined_date)`
- `loans (id, book_id, member_id, borrow_date, due_date, return_date, status)`

Borrowing decrements `available_copies` and inserts a `loans` row; returning
does the reverse and stamps `return_date`. A book can't be borrowed once its
`available_copies` reaches zero.

## Run it

```bash
pip install -r requirements.txt
python app.py
```

Then open `http://localhost:5050`. The SQLite database (`library.db`) is
created automatically on first run.

## Stack

Python · Flask · SQLite (stdlib `sqlite3`) · Jinja2 · HTML/CSS
