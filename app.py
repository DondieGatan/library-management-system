from datetime import date
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, flash, session
from werkzeug.security import generate_password_hash, check_password_hash
import database as db

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


@app.context_processor
def inject_today():
    return {'today': date.today().isoformat(), 'current_username': session.get('username')}


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
    recent_loans = db.get_loans(status='borrowed')[:5]
    return render_template('dashboard.html', stats=stats, recent_loans=recent_loans)


# --------------------------------------------------------------------------
# Books
# --------------------------------------------------------------------------

@app.route('/books')
@login_required
def books():
    search = request.args.get('q', '')
    return render_template('books.html', books=db.get_books(search), search=search)


@app.route('/books/add', methods=['GET', 'POST'])
@login_required
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
def delete_book(book_id):
    db.delete_book(book_id)
    flash('Book deleted.', 'success')
    return redirect(url_for('books'))


# --------------------------------------------------------------------------
# Members
# --------------------------------------------------------------------------

@app.route('/members')
@login_required
def members():
    search = request.args.get('q', '')
    return render_template('members.html', members=db.get_members(search), search=search)


@app.route('/members/add', methods=['GET', 'POST'])
@login_required
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
def delete_member(member_id):
    db.delete_member(member_id)
    flash('Member deleted.', 'success')
    return redirect(url_for('members'))


# --------------------------------------------------------------------------
# Loans
# --------------------------------------------------------------------------

@app.route('/loans')
@login_required
def loans():
    status = request.args.get('status')
    return render_template(
        'loans.html',
        loans=db.get_loans(status),
        status=status,
        books=db.get_books(),
        members=db.get_members(),
    )


@app.route('/loans/borrow', methods=['POST'])
@login_required
def borrow():
    ok, error = db.borrow_book(int(request.form['book_id']), int(request.form['member_id']))
    flash('Book borrowed.' if ok else error, 'success' if ok else 'error')
    return redirect(url_for('loans'))


@app.route('/loans/return/<int:loan_id>', methods=['POST'])
@login_required
def return_loan(loan_id):
    db.return_book(loan_id)
    flash('Book returned.', 'success')
    return redirect(url_for('loans'))


if __name__ == '__main__':
    app.run(debug=True, port=5050)
