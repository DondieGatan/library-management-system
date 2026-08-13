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


def test_member_cannot_view_users_page(client):
    register_and_login(client, username='member6')
    resp = client.get('/users', follow_redirects=True)
    assert b'requires an admin account' in resp.data


def test_admin_can_promote_member_to_admin(client):
    register_and_login(client, username='admin3', promote_admin=True)
    register_and_login(client, username='plainmember')
    # log back in as the admin
    register_and_login(client, username='admin3', promote_admin=True)

    import database
    target = database.get_user_by_username('plainmember')
    resp = client.post(f'/users/{target["id"]}/role', data={'role': 'admin'}, follow_redirects=True)
    assert b'Role updated' in resp.data
    assert database.get_user_by_username('plainmember')['role'] == 'admin'


def test_admin_cannot_change_own_role(client):
    register_and_login(client, username='admin4', promote_admin=True)
    import database
    self_user = database.get_user_by_username('admin4')
    resp = client.post(f'/users/{self_user["id"]}/role', data={'role': 'member'}, follow_redirects=True)
    assert b'change your own role' in resp.data
    assert database.get_user_by_username('admin4')['role'] == 'admin'


def test_members_page_paginates(client):
    register_and_login(client, username='admin5', promote_admin=True)
    import database
    for i in range(15):
        database.add_member(f'Member {i}', f'member{i}@example.com', '')

    resp = client.get('/members')
    assert b'Page 1 of' in resp.data


def test_loans_page_shows_pagination_status_with_many_loans(client):
    register_and_login(client, username='admin6', promote_admin=True)
    import database
    database.add_member('Repeat Member', 'repeat@example.com', '')
    member = database.get_members(search='Repeat Member')[0]
    for i in range(13):
        database.add_book(f'Loan Book {i}', 'Author', '', 'Fiction', 1)
        book = database.get_books(search=f'Loan Book {i}')[0]
        client.post('/loans/borrow', data={'book_id': book['id'], 'member_id': member['id']})

    resp = client.get('/loans')
    assert b'Page 1 of' in resp.data
