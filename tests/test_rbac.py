from conftest import register_and_login


def test_member_cannot_add_book(client):
    register_and_login(client, username='member1')
    resp = client.get('/books/add', follow_redirects=True)
    assert b'requires an admin account' in resp.data


def test_member_cannot_view_members_page(client):
    register_and_login(client, username='member2')
    resp = client.get('/members', follow_redirects=True)
    assert b'requires an admin account' in resp.data


def test_member_cannot_view_loans_page(client):
    register_and_login(client, username='member3')
    resp = client.get('/loans', follow_redirects=True)
    assert b'requires an admin account' in resp.data


def test_member_dashboard_redirects_to_books(client):
    register_and_login(client, username='member4')
    resp = client.get('/', follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers['Location'].endswith('/books')


def test_member_books_page_hides_admin_controls(client):
    register_and_login(client, username='member5')
    resp = client.get('/books')
    assert b'+ Add Book' not in resp.data
    assert b'Export CSV' not in resp.data


def test_admin_can_add_book(client):
    register_and_login(client, username='admin1', promote_admin=True)
    resp = client.post('/books/add', data={
        'title': 'Test Book', 'author': 'Test Author', 'isbn': '', 'category': 'Fiction', 'total_copies': '2',
    }, follow_redirects=True)
    assert b'Book added' in resp.data
    import database
    titles = [b['title'] for b in database.get_books()]
    assert 'Test Book' in titles


def test_admin_dashboard_is_accessible(client):
    register_and_login(client, username='admin2', promote_admin=True)
    resp = client.get('/')
    assert b'Dashboard' in resp.data
    assert resp.status_code == 200
