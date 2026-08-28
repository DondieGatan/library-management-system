"""Populates a fresh database with demo data so the live deployment has
something to look at immediately -- a handful of accounts, a real-ISBN
book catalogue (so the Open Library cover/description lookup has
something to actually resolve), a few members, and a couple of loans
(one deliberately overdue, so the dashboard's overdue highlighting has
something to show). Safe to re-run: no-ops once any user exists.
"""
import sys
from datetime import date, timedelta

from werkzeug.security import generate_password_hash

import database as db

DEMO_PASSWORD = "password123"

BOOKS = [
    ("The Hobbit", "J.R.R. Tolkien", "9780547928227", "Fantasy", 3),
    ("Dune", "Frank Herbert", "9780441013593", "Sci-Fi", 2),
    ("1984", "George Orwell", "9780451524935", "Classic", 4),
    ("To Kill a Mockingbird", "Harper Lee", "9780061120084", "Classic", 2),
    ("The Great Gatsby", "F. Scott Fitzgerald", "9780743273565", "Classic", 3),
    ("Sapiens", "Yuval Noah Harari", "9780062316097", "Non-Fiction", 2),
    ("Clean Code", "Robert C. Martin", "9780132350884", "Programming", 3),
    ("The Pragmatic Programmer", "David Thomas", "9780135957059", "Programming", 2),
]

MEMBERS = [
    ("Ava Thompson", "ava.thompson@example.com", "555-0101"),
    ("Liam Chen", "liam.chen@example.com", "555-0102"),
    ("Sofia Torres", "sofia.torres@example.com", "555-0103"),
    ("Noah Bergstrom", "noah.bergstrom@example.com", "555-0104"),
]


def main():
    db.init_db()
    conn = db.get_connection()
    already_seeded = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"] > 0
    conn.close()
    if already_seeded:
        print("Users already exist -- skipping seed.")
        return

    # First user becomes owner automatically (see database.create_user).
    db.create_user("owner", generate_password_hash(DEMO_PASSWORD))
    db.create_user("admin", generate_password_hash(DEMO_PASSWORD), role="admin")
    db.create_user("member", generate_password_hash(DEMO_PASSWORD), role="member")

    for title, author, isbn, category, copies in BOOKS:
        db.add_book(title, author, isbn, category, copies)

    for name, email, phone in MEMBERS:
        db.add_member(name, email, phone)

    books = db.get_books()
    members = db.get_members()

    # A couple of normal active loans...
    db.borrow_book(books[0]["id"], members[0]["id"])
    db.borrow_book(books[2]["id"], members[1]["id"])

    # ...and one backdated past its due date, so the dashboard's overdue
    # highlighting has something to demonstrate out of the box.
    ok, _ = db.borrow_book(books[4]["id"], members[2]["id"])
    if ok:
        conn = db.get_connection()
        borrow_date = date.today() - timedelta(days=21)
        due_date = borrow_date + timedelta(days=db.LOAN_PERIOD_DAYS)
        conn.execute(
            "UPDATE loans SET borrow_date = ?, due_date = ? "
            "WHERE book_id = ? AND member_id = ? AND status = 'borrowed'",
            (borrow_date.isoformat(), due_date.isoformat(), books[4]["id"], members[2]["id"]),
        )
        conn.commit()
        conn.close()

    print("Seeded demo users (owner/admin/member, password: %s), "
          "%d books, %d members, 3 loans." % (DEMO_PASSWORD, len(BOOKS), len(MEMBERS)))


if __name__ == "__main__":
    sys.exit(main())
