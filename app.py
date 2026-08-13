import csv
import io
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, flash, session, Response
from werkzeug.security import generate_password_hash, check_password_hash
import database as db
import covers

COVER_POOL = ThreadPoolExecutor(max_workers=8)

app = Flask(__name__)
app.secret_key = 'library-management-system-dev-key'

db.init_db()


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if 'user_id' not in session:
            flash('Please log in to continue.', 'error')
            return redirect(url_for('login', next=request.path))
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if session.get('role') != 'admin':
            flash('That action requires an admin account.', 'error')
            return redirect(request.referrer or url_for('dashboard'))
        return view(*args, **kwargs)
    return wrapped


def _resolve_missing_covers(rows, id_key, isbn_key, title_key, author_key, url_key):
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


def with_book_covers(books):
    return _resolve_missing_covers(books, 'id', 'isbn', 'title', 'author', 'cover_url')


def with_loan_covers(loans):
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
def register():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        username = request.form['username'].strip()
        password = request.form['password']
        confirm = request.form.get('confirm_password', '')

        if not username or not password:
            flash('Username and password are required.', 'error')
        elif password != confirm:
            flash('Passwords do not match.', 'error')
        elif len(password) < 8:
            flash('Password must be at least 8 characters.', 'error')
        elif db.get_user_by_username(username):
            flash('That username is already taken.', 'error')
        else:
            db.create_user(username, generate_password_hash(password))
            flash('Account created — please log in.', 'success')
            return redirect(url_for('login'))
    return render_template('register.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        username = request.form['username'].strip()
        password = request.form['password']
        user = db.get_user_by_username(username)
        if user and check_password_hash(user['password_hash'], password):
            session['user_id'] = user['id']
            session['username'] = user['username']
            session['role'] = user['role']
            flash('Welcome back, ' + user['username'] + '.', 'success')
            next_url = request.args.get('next')
            return redirect(next_url or url_for('dashboard'))
        flash('Incorrect username or password.', 'error')
    return render_template('login.html')


@app.route('/logout', methods=['POST'])
def logout():
    session.clear()
    flash('Logged out.', 'success')
    return redirect(url_for('login'))


@app.route('/')
@login_required
def dashboard():
    stats = db.get_stats()
    recent_loans = with_loan_covers(db.get_loans(status='borrowed')[:5]) if session.get('role') == 'admin' else []
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

BOOKS_PER_PAGE = 10


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
    )


@app.route('/books/<int:book_id>')
@login_required
def book_detail(book_id):
    book = db.get_book(book_id)
    if not book:
        flash('Book not found.', 'error')
        return redirect(url_for('books'))
    book = with_book_covers([book])[0]
    loan_history = db.get_book_loans(book_id)
    return render_template('book_detail.html', book=book, loan_history=loan_history)


@app.route('/books/export.csv')
@login_required
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
    db.delete_book(book_id)
    flash('Book deleted.', 'success')
    return redirect(url_for('books'))


# --------------------------------------------------------------------------
# Members
# --------------------------------------------------------------------------

@app.route('/members')
@login_required
@admin_required
def members():
    search = request.args.get('q', '')
    return render_template('members.html', members=db.get_members(search), search=search)


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
    db.delete_member(member_id)
    flash('Member deleted.', 'success')
    return redirect(url_for('members'))


# --------------------------------------------------------------------------
# Loans
# --------------------------------------------------------------------------

@app.route('/loans')
@login_required
@admin_required
def loans():
    status = request.args.get('status')
    search = request.args.get('q', '')
    return render_template(
        'loans.html',
        loans=with_loan_covers(db.get_loans(status, search)),
        status=status,
        search=search,
        books=db.get_books(),
        members=db.get_members(),
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
    app.run(debug=True, port=5050)
