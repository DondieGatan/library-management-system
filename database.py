import sqlite3
import os
from datetime import date, timedelta

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'library.db')
LOAN_PERIOD_DAYS = 14


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    return conn


def init_db():
    conn = get_connection()
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS books (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            title           TEXT NOT NULL,
            author          TEXT NOT NULL,
            isbn            TEXT,
            category        TEXT DEFAULT 'General',
            total_copies    INTEGER NOT NULL DEFAULT 1,
            available_copies INTEGER NOT NULL DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS members (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL,
            email       TEXT NOT NULL UNIQUE,
            phone       TEXT,
            joined_date TEXT NOT NULL DEFAULT (date('now'))
        );

        CREATE TABLE IF NOT EXISTS loans (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            book_id     INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
            member_id   INTEGER NOT NULL REFERENCES members(id) ON DELETE CASCADE,
            borrow_date TEXT NOT NULL,
            due_date    TEXT NOT NULL,
            return_date TEXT,
            status      TEXT NOT NULL DEFAULT 'borrowed' CHECK (status IN ('borrowed', 'returned'))
        );

        CREATE TABLE IF NOT EXISTS users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            username      TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at    TEXT NOT NULL DEFAULT (date('now'))
        );
    ''')
    # Migration for tables created before cover_url existed. NULL means
    # "never looked up yet"; '' means "looked up, no cover found" -- both
    # distinct from an actual URL, so we only ever resolve each book once.
    # try/except rather than "ADD COLUMN IF NOT EXISTS" for portability
    # across older SQLite builds.
    try:
        conn.execute('ALTER TABLE books ADD COLUMN cover_url TEXT')
    except sqlite3.OperationalError:
        pass
    # Migration for tables created before roles existed. Defaults to
    # 'admin' so accounts registered before this feature keep the full
    # access they already had; create_user() passes 'member' explicitly
    # for every signup from here on.
    try:
        conn.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'admin'")
    except sqlite3.OperationalError:
        pass
    conn.commit()
    conn.close()


# --------------------------------------------------------------------------
# Books
# --------------------------------------------------------------------------

def get_books(search='', page=None, per_page=10):
    conn = get_connection()
    like = f'%{search}%'
    where = 'WHERE title LIKE ? OR author LIKE ? OR category LIKE ?' if search else ''
    params = (like, like, like) if search else ()

    total = conn.execute(f'SELECT COUNT(*) AS n FROM books {where}', params).fetchone()['n']

    query = f'SELECT * FROM books {where} ORDER BY title'
    if page is not None:
        query += ' LIMIT ? OFFSET ?'
        params = params + (per_page, (page - 1) * per_page)
    rows = conn.execute(query, params).fetchall()
    conn.close()

    if page is not None:
        return rows, total
    return rows


def get_book(book_id):
    conn = get_connection()
    row = conn.execute('SELECT * FROM books WHERE id = ?', (book_id,)).fetchone()
    conn.close()
    return row


def add_book(title, author, isbn, category, total_copies):
    conn = get_connection()
    conn.execute(
        'INSERT INTO books (title, author, isbn, category, total_copies, available_copies) VALUES (?, ?, ?, ?, ?, ?)',
        (title, author, isbn, category, total_copies, total_copies)
    )
    conn.commit()
    conn.close()


def update_book(book_id, title, author, isbn, category, total_copies):
    conn = get_connection()
    current = conn.execute('SELECT total_copies, available_copies FROM books WHERE id = ?', (book_id,)).fetchone()
    borrowed_out = current['total_copies'] - current['available_copies']
    new_available = max(total_copies - borrowed_out, 0)
    conn.execute(
        # cover_url reset to NULL so a changed title/author/isbn gets its
        # cover re-resolved instead of keeping a stale one.
        'UPDATE books SET title=?, author=?, isbn=?, category=?, total_copies=?, available_copies=?, cover_url=NULL WHERE id=?',
        (title, author, isbn, category, total_copies, new_available, book_id)
    )
    conn.commit()
    conn.close()


def update_book_cover(book_id, cover_url):
    conn = get_connection()
    conn.execute('UPDATE books SET cover_url = ? WHERE id = ?', (cover_url, book_id))
    conn.commit()
    conn.close()


def delete_book(book_id):
    conn = get_connection()
    conn.execute('DELETE FROM books WHERE id = ?', (book_id,))
    conn.commit()
    conn.close()


# --------------------------------------------------------------------------
# Members
# --------------------------------------------------------------------------

def get_members(search=''):
    conn = get_connection()
    if search:
        like = f'%{search}%'
        rows = conn.execute(
            'SELECT * FROM members WHERE name LIKE ? OR email LIKE ? ORDER BY name', (like, like)
        ).fetchall()
    else:
        rows = conn.execute('SELECT * FROM members ORDER BY name').fetchall()
    conn.close()
    return rows


def get_member(member_id):
    conn = get_connection()
    row = conn.execute('SELECT * FROM members WHERE id = ?', (member_id,)).fetchone()
    conn.close()
    return row


def add_member(name, email, phone):
    conn = get_connection()
    conn.execute('INSERT INTO members (name, email, phone) VALUES (?, ?, ?)', (name, email, phone))
    conn.commit()
    conn.close()


def update_member(member_id, name, email, phone):
    conn = get_connection()
    conn.execute('UPDATE members SET name=?, email=?, phone=? WHERE id=?', (name, email, phone, member_id))
    conn.commit()
    conn.close()


def delete_member(member_id):
    conn = get_connection()
    conn.execute('DELETE FROM members WHERE id = ?', (member_id,))
    conn.commit()
    conn.close()


# --------------------------------------------------------------------------
# Loans (borrow / return)
# --------------------------------------------------------------------------

def get_loans(status=None, search=''):
    conn = get_connection()
    query = '''
        SELECT loans.*, books.title AS book_title, books.isbn AS book_isbn,
               books.author AS book_author, books.cover_url AS book_cover_url,
               members.name AS member_name
        FROM loans
        JOIN books ON books.id = loans.book_id
        JOIN members ON members.id = loans.member_id
    '''
    conditions = []
    params = []
    if status == 'overdue':
        conditions.append("loans.status = 'borrowed' AND loans.due_date < ?")
        params.append(date.today().isoformat())
    elif status:
        conditions.append('loans.status = ?')
        params.append(status)
    if search:
        conditions.append('(books.title LIKE ? OR members.name LIKE ?)')
        like = f'%{search}%'
        params.extend([like, like])
    if conditions:
        query += ' WHERE ' + ' AND '.join(conditions)
    query += ' ORDER BY loans.borrow_date DESC'
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return rows


def get_book_loans(book_id):
    conn = get_connection()
    rows = conn.execute('''
        SELECT loans.*, members.name AS member_name
        FROM loans
        JOIN members ON members.id = loans.member_id
        WHERE loans.book_id = ?
        ORDER BY loans.borrow_date DESC
    ''', (book_id,)).fetchall()
    conn.close()
    return rows


def get_books_by_category():
    conn = get_connection()
    rows = conn.execute(
        'SELECT category, COUNT(*) AS n FROM books GROUP BY category ORDER BY n DESC'
    ).fetchall()
    conn.close()
    return rows


def borrow_book(book_id, member_id):
    conn = get_connection()
    book = conn.execute('SELECT available_copies FROM books WHERE id = ?', (book_id,)).fetchone()
    if not book or book['available_copies'] < 1:
        conn.close()
        return False, 'No copies available for this book.'

    today = date.today()
    due = today + timedelta(days=LOAN_PERIOD_DAYS)
    conn.execute(
        'INSERT INTO loans (book_id, member_id, borrow_date, due_date, status) VALUES (?, ?, ?, ?, ?)',
        (book_id, member_id, today.isoformat(), due.isoformat(), 'borrowed')
    )
    conn.execute('UPDATE books SET available_copies = available_copies - 1 WHERE id = ?', (book_id,))
    conn.commit()
    conn.close()
    return True, None


def return_book(loan_id):
    conn = get_connection()
    loan = conn.execute('SELECT * FROM loans WHERE id = ?', (loan_id,)).fetchone()
    if not loan or loan['status'] == 'returned':
        conn.close()
        return False

    conn.execute(
        "UPDATE loans SET status = 'returned', return_date = ? WHERE id = ?",
        (date.today().isoformat(), loan_id)
    )
    conn.execute('UPDATE books SET available_copies = available_copies + 1 WHERE id = ?', (loan['book_id'],))
    conn.commit()
    conn.close()
    return True


# --------------------------------------------------------------------------
# Dashboard stats
# --------------------------------------------------------------------------

def get_stats():
    conn = get_connection()
    total_books = conn.execute('SELECT COALESCE(SUM(total_copies), 0) AS n FROM books').fetchone()['n']
    total_titles = conn.execute('SELECT COUNT(*) AS n FROM books').fetchone()['n']
    total_members = conn.execute('SELECT COUNT(*) AS n FROM members').fetchone()['n']
    borrowed_now = conn.execute("SELECT COUNT(*) AS n FROM loans WHERE status = 'borrowed'").fetchone()['n']
    overdue_now = conn.execute(
        "SELECT COUNT(*) AS n FROM loans WHERE status = 'borrowed' AND due_date < ?",
        (date.today().isoformat(),)
    ).fetchone()['n']
    conn.close()
    return {
        'total_books': total_books,
        'total_titles': total_titles,
        'total_members': total_members,
        'borrowed_now': borrowed_now,
        'overdue_now': overdue_now,
    }


# --------------------------------------------------------------------------
# Users (authentication)
# --------------------------------------------------------------------------

def get_user_by_username(username):
    conn = get_connection()
    row = conn.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
    conn.close()
    return row


def get_user_by_id(user_id):
    conn = get_connection()
    row = conn.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
    conn.close()
    return row


def create_user(username, password_hash, role='member'):
    conn = get_connection()
    conn.execute(
        'INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)',
        (username, password_hash, role)
    )
    conn.commit()
    conn.close()
