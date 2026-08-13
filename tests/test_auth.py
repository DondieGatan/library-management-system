from conftest import register_and_login


def test_register_and_login_succeeds(client):
    resp = register_and_login(client)
    assert resp.status_code == 200
    assert b'Welcome back' in resp.data or b'Dashboard' in resp.data or b'Books' in resp.data


def test_new_signups_default_to_member_role(client):
    register_and_login(client, username='bob')
    import database
    user = database.get_user_by_username('bob')
    assert user['role'] == 'member'


def test_login_rejects_wrong_password(client):
    client.post('/register', data={
        'username': 'carol', 'password': 'password123', 'confirm_password': 'password123',
    })
    resp = client.post('/login', data={'username': 'carol', 'password': 'wrongpass'}, follow_redirects=True)
    assert b'Incorrect username or password' in resp.data


def test_register_rejects_short_password(client):
    resp = client.post('/register', data={
        'username': 'dave', 'password': 'short', 'confirm_password': 'short',
    }, follow_redirects=True)
    assert b'at least 8 characters' in resp.data
    import database
    assert database.get_user_by_username('dave') is None


def test_register_rejects_mismatched_passwords(client):
    resp = client.post('/register', data={
        'username': 'erin', 'password': 'password123', 'confirm_password': 'password456',
    }, follow_redirects=True)
    assert b'do not match' in resp.data


def test_register_rejects_duplicate_username(client):
    client.post('/register', data={
        'username': 'frank', 'password': 'password123', 'confirm_password': 'password123',
    })
    resp = client.post('/register', data={
        'username': 'frank', 'password': 'password123', 'confirm_password': 'password123',
    }, follow_redirects=True)
    assert b'already taken' in resp.data


def test_protected_route_redirects_when_logged_out(client):
    resp = client.get('/books', follow_redirects=True)
    assert b'Please log in to continue' in resp.data


def test_logout_clears_session(client):
    register_and_login(client, username='grace')
    client.post('/logout')
    resp = client.get('/books', follow_redirects=True)
    assert b'Please log in to continue' in resp.data
