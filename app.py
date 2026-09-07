import csv
import io
import logging
import os
import re
import secrets
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from functools import wraps
from typing import Any, Callable
from dotenv import load_dotenv
import requests
from flask import Flask, render_template, request, redirect, url_for, flash, session, Response
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_wtf import CSRFProtect
from werkzeug.security import generate_password_hash, check_password_hash
import database as db
import covers

load_dotenv()

EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger('library')

COVER_POOL = ThreadPoolExecutor(max_workers=8)

app = Flask(__name__)
# Falls back to a random per-process key if SECRET_KEY isn't set so the app
# still runs, but sessions won't survive a restart -- set SECRET_KEY in .env
# for real use (see .env.example). Never hardcode a real key in source.
app.secret_key = os.environ.get('SECRET_KEY') or secrets.token_hex(32)
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = os.environ.get('FLASK_DEBUG') != '1'

csrf = CSRFProtect(app)
limiter = Limiter(get_remote_address, app=app, default_limits=[])

db.init_db()

SENDGRID_API_KEY = os.environ.get('SENDGRID_API_KEY')
SENDGRID_FROM = os.environ.get('SENDGRID_FROM')
RESET_CODE_TTL_MINUTES = 10


def send_email(to: str, subject: str, html: str) -> bool:
    """Send an email via SendGrid's HTTP API. Returns False (and logs a
    warning instead of raising) when SendGrid isn't configured -- e.g. in
    local dev without a key set -- so callers can fire-and-forget without
    every environment needing real email delivery."""
    if not SENDGRID_API_KEY or not SENDGRID_FROM:
        logger.warning('SendGrid is not configured; skipping email to %s', to)
        return False
    response = requests.post(
        'https://api.sendgrid.com/v3/mail/send',
        headers={'Authorization': f'Bearer {SENDGRID_API_KEY}'},
        json={
            'personalizations': [{'to': [{'email': to}]}],
            'from': {'email': SENDGRID_FROM},
            'subject': subject,
            'content': [{'type': 'text/html', 'value': html}],
        },
        timeout=10,
    )
    if not response.ok:
        logger.error('SendGrid email to %s failed: %s %s', to, response.status_code, response.text)
    return response.ok


def generate_reset_code() -> str:
    return f'{secrets.randbelow(1_000_000):06d}'


def login_required(view: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(view)
    def wrapped(*args, **kwargs):
        if 'user_id' not in session:
            flash('Please log in to continue.', 'error')
            return redirect(url_for('login', next=request.path))
        # Re-check the user against the database on every request rather than
        # trusting the cookie's cached username/role -- otherwise a deleted
        # account keeps working until the browser session expires, and a
        # demoted admin/owner keeps their old privileges until they log out.
        user = db.get_user_by_id(session['user_id'])
        if user is None:
            session.clear()
            flash('Your account is no longer available. Please log in again.', 'error')
            return redirect(url_for('login', next=request.path))
        session['username'] = user['username']
        session['role'] = user['role']
        return view(*args, **kwargs)
    return wrapped


def admin_required(view: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(view)
    def wrapped(*args, **kwargs):
        if session.get('role') not in ('admin', 'owner'):
            flash('That action requires an admin account.', 'error')
            return redirect(request.referrer or url_for('books'))
        return view(*args, **kwargs)
    return wrapped


def owner_required(view: Callable[..., Any]) -> Callable[..., Any]:
    """Stricter than admin_required -- an admin promoted by the owner could
    otherwise turn around and demote the owner (or any other admin), which
    is exactly the kind of access a regular admin shouldn't be able to
    revoke. Only the owner can change anyone's role."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        if session.get('role') != 'owner':
            flash('Only the owner account can manage user roles.', 'error')
            return redirect(request.referrer or url_for('books'))
        return view(*args, **kwargs)
    return wrapped


def home_url() -> str:
    """Members have no Dashboard, so their landing page is Books instead."""
    return url_for('dashboard') if session.get('role') != 'member' else url_for('books')


def _resolve_missing_covers(
    rows: list, id_key: str, isbn_key: str, title_key: str, author_key: str, url_key: str
) -> list[dict]:
    """Shared helper for with_book_covers/with_loan_covers. Looks up every
    row missing a cover concurrently (these are independent, slow network
    calls -- doing them one at a time made a page with many uncached books
    take minutes to load) then caches each result on the book row."""
    result = [dict(row) for row in rows]
    pending = [r for r in result if r.get(url_key) is None]
    if pending:
        urls = COVER_POOL.map(
            lambda r: covers.resolve_cover_url(r.get(isbn_key), r[title_key], r.get(author_key)),
            pending,
        )
        for r, url in zip(pending, urls):
            db.update_book_cover(r[id_key], url or '')
            r[url_key] = url or ''
    return result


def with_book_covers(books: list) -> list[dict]:
    return _resolve_missing_covers(books, 'id', 'isbn', 'title', 'author', 'cover_url')


def with_loan_covers(loans: list) -> list[dict]:
    return _resolve_missing_covers(loans, 'book_id', 'book_isbn', 'book_title', 'book_author', 'book_cover_url')


@app.context_processor
def inject_today():
    return {
        'today': date.today().isoformat(),
        'current_username': session.get('username'),
        'current_role': session.get('role'),
    }


# --------------------------------------------------------------------------
# Authentication
# --------------------------------------------------------------------------

@app.route('/register', methods=['GET', 'POST'])
@limiter.limit('5 per minute', methods=['POST'])
def register():
    if 'user_id' in session:
        return redirect(home_url())
    if request.method == 'POST':
        username = request.form['username'].strip()
        email = request.form.get('email', '').strip().lower()
        password = request.form['password']
        confirm = request.form.get('confirm_password', '')

        if not username or not password or not email:
            flash('Username, email, and password are required.', 'error')
        elif not EMAIL_RE.match(email):
            flash('Enter a valid email address.', 'error')
        elif password != confirm:
            flash('Passwords do not match.', 'error')
        elif len(password) < 8:
            flash('Password must be at least 8 characters.', 'error')
        elif db.get_user_by_username(username):
            flash('That username is already taken.', 'error')
        elif db.get_user_by_email(email):
            flash('An account with that email already exists.', 'error')
        else:
            db.create_user(username, generate_password_hash(password), email=email)
            flash('Account created — please log in.', 'success')
            return redirect(url_for('login'))
    return render_template('register.html')


@app.route('/login', methods=['GET', 'POST'])
@limiter.limit('5 per minute', methods=['POST'])
def login():
    if 'user_id' in session:
        return redirect(home_url())
    if request.method == 'POST':
        username = request.form['username'].strip()
        password = request.form['password']
        user = db.get_user_by_username(username)
        if user and check_password_hash(user['password_hash'], password):
            session['user_id'] = user['id']
            session['username'] = user['username']
            session['role'] = user['role']
            logger.info('login success user=%s ip=%s', username, request.remote_addr)
            flash('Welcome back, ' + user['username'] + '.', 'success')
            next_url = request.args.get('next')
            return redirect(next_url or home_url())
        logger.warning('login failed user=%s ip=%s', username, request.remote_addr)
        flash('Incorrect username or password.', 'error')
    return render_template('login.html')


@app.route('/logout', methods=['POST'])
def logout():
    session.clear()
    flash('Logged out.', 'success')
    return redirect(url_for('login'))


@app.route('/forgot-password', methods=['GET', 'POST'])
@limiter.limit('5 per minute', methods=['POST'])
def forgot_password():
    if 'user_id' in session:
        return redirect(home_url())
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        user = db.get_user_by_email(email)
        if user:
            code = generate_reset_code()
            db.create_reset_code(email, code, ttl_minutes=RESET_CODE_TTL_MINUTES)
            send_email(
                email,
                'Reset your Library Management System password',
                f'<p>Your password reset code is:</p><h2>{code}</h2>'
                f'<p>This code expires in {RESET_CODE_TTL_MINUTES} minutes. '
                f"If you didn't request this, you can safely ignore this email.</p>",
            )
        # Same message whether or not the account exists, so this can't be
        # used to enumerate which emails are registered.
        flash('If an account with that email exists, a reset code has been sent.', 'success')
        return redirect(url_for('verify_reset_code_route', email=email))
    return render_template('forgot_password.html')


@app.route('/verify-reset-code', methods=['GET', 'POST'])
@limiter.limit('10 per minute', methods=['POST'])
def verify_reset_code_route():
    if 'user_id' in session:
        return redirect(home_url())

    email = request.args.get('email', '') or request.form.get('email', '')
    if not email:
        flash('Please start the password reset process again.', 'error')
        return redirect(url_for('forgot_password'))

    if request.method == 'POST':
        code = request.form.get('code', '').strip()
        if not code or len(code) != 6:
            flash('Please enter the 6-digit code.', 'error')
        elif db.verify_reset_code(email, code):
            session['reset_email'] = email
            flash('Code verified! Set your new password.', 'success')
            return redirect(url_for('reset_password'))
        else:
            flash('Invalid or expired code. Please try again.', 'error')
    return render_template('verify_reset_code.html', email=email)


@app.route('/reset-password', methods=['GET', 'POST'])
def reset_password():
    if 'user_id' in session:
        return redirect(home_url())

    email = session.get('reset_email')
    if not email:
        flash('Please verify your code first.', 'error')
        return redirect(url_for('forgot_password'))

    # Re-checked here (not just at verify-code) because the session flag
    # above has no expiry of its own -- without this, a browser tab left
    # open past the code's TTL could still set a new password with no
    # live verification at all.
    if not db.reset_token_still_valid(email):
        session.pop('reset_email', None)
        flash('Your reset code has expired. Please start again.', 'error')
        return redirect(url_for('forgot_password'))

    if request.method == 'POST':
        password = request.form.get('password', '')
        confirm = request.form.get('confirm_password', '')

        if not password or len(password) < 8:
            flash('Password must be at least 8 characters.', 'error')
        elif password != confirm:
            flash('Passwords do not match.', 'error')
        else:
            db.reset_user_password(email, generate_password_hash(password))
            session.pop('reset_email', None)
            flash('Password reset successfully! Please log in with your new password.', 'success')
            return redirect(url_for('login'))
    return render_template('reset_password.html')


@app.route('/')
@login_required
def dashboard():
    if session.get('role') not in ('admin', 'owner'):
        return redirect(url_for('books'))
    stats = db.get_stats()
    recent_loans = with_loan_covers(db.get_loans(status='borrowed')[:5])
    category_counts = db.get_books_by_category()
    selected_category = request.args.get('category') or (category_counts[0]['category'] if category_counts else None)
    selected_count = next((c['n'] for c in category_counts if c['category'] == selected_category), 0)
    return render_template(
        'dashboard.html',
        stats=stats,
        recent_loans=recent_loans,
        category_counts=category_counts,
        selected_category=selected_category,
        selected_count=selected_count,
    )


# --------------------------------------------------------------------------
# Books
# --------------------------------------------------------------------------

BOOKS_PER_PAGE = 12  # multiple of 2/3/4/6 so the grid's last row fills evenly at common widths


@app.route('/books')
@login_required
def books():
    search = request.args.get('q', '')
    page = max(request.args.get('page', 1, type=int), 1)
    rows, total = db.get_books(search, page=page, per_page=BOOKS_PER_PAGE)
    total_pages = max((total + BOOKS_PER_PAGE - 1) // BOOKS_PER_PAGE, 1)
    return render_template(
        'books.html',
        books=with_book_covers(rows),
        search=search,
        page=page,
        total_pages=total_pages,
        total=total,
        all_books_lite=db.get_all_books_lite(),
    )


@app.route('/books/<int:book_id>')
@login_required
def book_detail(book_id):
    book = db.get_book(book_id)
    if not book:
        flash('Book not found.', 'error')
        return redirect(url_for('books'))
    book = with_book_covers([book])[0]
    if book.get('description') is None:
        description = covers.resolve_description(book.get('isbn'), book['title'], book['author'])
        db.update_book_description(book_id, description or '')
        book['description'] = description or ''
    loan_history = db.get_book_loans(book_id) if session.get('role') in ('admin', 'owner') else []
    return render_template('book_detail.html', book=book, loan_history=loan_history)


@app.route('/books/export.csv')
@login_required
@admin_required
def export_books_csv():
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Title', 'Author', 'ISBN', 'Category', 'Total Copies', 'Available Copies'])
    for b in db.get_books():
        writer.writerow([b['title'], b['author'], b['isbn'] or '', b['category'], b['total_copies'], b['available_copies']])
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename=books.csv'},
    )


@app.route('/books/add', methods=['GET', 'POST'])
@login_required
@admin_required
def add_book():
    if request.method == 'POST':
        db.add_book(
            request.form['title'].strip(),
            request.form['author'].strip(),
            request.form.get('isbn', '').strip(),
            request.form.get('category', 'General').strip() or 'General',
            max(int(request.form.get('total_copies', 1) or 1), 1),
        )
        flash('Book added.', 'success')
        return redirect(url_for('books'))
    return render_template('book_form.html', book=None)


@app.route('/books/edit/<int:book_id>', methods=['GET', 'POST'])
@login_required
@admin_required
def edit_book(book_id):
    book = db.get_book(book_id)
    if not book:
        flash('Book not found.', 'error')
        return redirect(url_for('books'))
    if request.method == 'POST':
        db.update_book(
            book_id,
            request.form['title'].strip(),
            request.form['author'].strip(),
            request.form.get('isbn', '').strip(),
            request.form.get('category', 'General').strip() or 'General',
            max(int(request.form.get('total_copies', 1) or 1), 1),
        )
        flash('Book updated.', 'success')
        return redirect(url_for('books'))
    return render_template('book_form.html', book=book)


@app.route('/books/delete/<int:book_id>', methods=['POST'])
@login_required
@admin_required
def delete_book(book_id):
    ok, error = db.delete_book(book_id)
    if ok:
        logger.info('book deleted id=%s by=%s', book_id, session.get('username'))
    flash('Book deleted.' if ok else error, 'success' if ok else 'error')
    return redirect(url_for('books'))


# --------------------------------------------------------------------------
# Members
# --------------------------------------------------------------------------

MEMBERS_PER_PAGE = 12


@app.route('/members')
@login_required
@admin_required
def members():
    search = request.args.get('q', '')
    page = max(request.args.get('page', 1, type=int), 1)
    rows, total = db.get_members(search, page=page, per_page=MEMBERS_PER_PAGE)
    total_pages = max((total + MEMBERS_PER_PAGE - 1) // MEMBERS_PER_PAGE, 1)
    return render_template(
        'members.html', members=rows, search=search,
        page=page, total_pages=total_pages, total=total,
    )


@app.route('/members/add', methods=['GET', 'POST'])
@login_required
@admin_required
def add_member():
    if request.method == 'POST':
        db.add_member(
            request.form['name'].strip(),
            request.form['email'].strip(),
            request.form.get('phone', '').strip(),
        )
        flash('Member added.', 'success')
        return redirect(url_for('members'))
    return render_template('member_form.html', member=None)


@app.route('/members/edit/<int:member_id>', methods=['GET', 'POST'])
@login_required
@admin_required
def edit_member(member_id):
    member = db.get_member(member_id)
    if not member:
        flash('Member not found.', 'error')
        return redirect(url_for('members'))
    if request.method == 'POST':
        db.update_member(
            member_id,
            request.form['name'].strip(),
            request.form['email'].strip(),
            request.form.get('phone', '').strip(),
        )
        flash('Member updated.', 'success')
        return redirect(url_for('members'))
    return render_template('member_form.html', member=member)


@app.route('/members/delete/<int:member_id>', methods=['POST'])
@login_required
@admin_required
def delete_member(member_id):
    ok, error = db.delete_member(member_id)
    if ok:
        logger.info('member deleted id=%s by=%s', member_id, session.get('username'))
    flash('Member deleted.' if ok else error, 'success' if ok else 'error')
    return redirect(url_for('members'))


# --------------------------------------------------------------------------
# Users (admin/member role management)
# --------------------------------------------------------------------------

@app.route('/users')
@login_required
@owner_required
def users():
    return render_template('users.html', users=db.get_all_users())


@app.route('/users/<int:user_id>/role', methods=['POST'])
@login_required
@owner_required
def update_user_role(user_id):
    if user_id == session.get('user_id'):
        flash("You can't change your own role.", 'error')
        return redirect(url_for('users'))
    target = db.get_user_by_id(user_id)
    if target and target['role'] == 'owner':
        flash("The owner's role can't be changed.", 'error')
        return redirect(url_for('users'))
    new_role = 'admin' if request.form.get('role') == 'admin' else 'member'
    db.update_user_role(user_id, new_role)
    logger.info('role changed user_id=%s new_role=%s by=%s', user_id, new_role, session.get('username'))
    flash('Role updated.', 'success')
    return redirect(url_for('users'))


# --------------------------------------------------------------------------
# Loans
# --------------------------------------------------------------------------

LOANS_PER_PAGE = 12


@app.route('/loans')
@login_required
@admin_required
def loans():
    status = request.args.get('status')
    search = request.args.get('q', '')
    page = max(request.args.get('page', 1, type=int), 1)
    rows, total = db.get_loans(status, search, page=page, per_page=LOANS_PER_PAGE)
    total_pages = max((total + LOANS_PER_PAGE - 1) // LOANS_PER_PAGE, 1)
    borrow_books = [
        {'id': b['id'], 'title': b['title'], 'author': b['author'],
         'cover_url': b['cover_url'], 'available_copies': b['available_copies']}
        for b in db.get_books()
    ]
    borrow_members = [{'id': m['id'], 'name': m['name'], 'email': m['email']} for m in db.get_members()]
    return render_template(
        'loans.html',
        loans=with_loan_covers(rows),
        status=status,
        search=search,
        page=page,
        total_pages=total_pages,
        total=total,
        borrow_books=borrow_books,
        borrow_members=borrow_members,
    )


@app.route('/loans/export.csv')
@login_required
@admin_required
def export_loans_csv():
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Book', 'Member', 'Borrowed', 'Due', 'Returned', 'Status'])
    for loan in db.get_loans():
        writer.writerow([
            loan['book_title'], loan['member_name'], loan['borrow_date'],
            loan['due_date'], loan['return_date'] or '', loan['status'],
        ])
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename=loans.csv'},
    )


@app.route('/loans/borrow', methods=['POST'])
@login_required
@admin_required
def borrow():
    ok, error = db.borrow_book(int(request.form['book_id']), int(request.form['member_id']))
    flash('Book borrowed.' if ok else error, 'success' if ok else 'error')
    return redirect(url_for('loans'))


@app.route('/loans/return/<int:loan_id>', methods=['POST'])
@login_required
@admin_required
def return_loan(loan_id):
    db.return_book(loan_id)
    flash('Book returned.', 'success')
    return redirect(url_for('loans'))


if __name__ == '__main__':
    app.run(debug=os.environ.get('FLASK_DEBUG') == '1', port=int(os.environ.get('PORT', 5050)))
