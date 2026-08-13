import importlib
import sys
import os

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A Flask test client backed by an isolated, empty temp database --
    never touches the real library.db. Reloads app/database/covers each
    time so init_db() runs fresh against the new DB path."""
    monkeypatch.setenv('DATABASE_PATH', str(tmp_path / 'test.db'))
    monkeypatch.setenv('SECRET_KEY', 'test-secret-key')

    import database
    importlib.reload(database)
    import covers
    importlib.reload(covers)
    import app as app_module
    importlib.reload(app_module)

    app_module.app.config['TESTING'] = True
    app_module.app.config['WTF_CSRF_ENABLED'] = False
    app_module.limiter.enabled = False

    with app_module.app.test_client() as c:
        yield c


def register_and_login(client, username='alice', password='password123', promote_admin=False):
    import database
    # The first-ever account in a database becomes owner automatically
    # (see test_first_registered_user_becomes_owner for a direct test of
    # that). Most other tests don't care about that and just want a plain
    # member/admin, so burn the "first user" slot on a throwaway account
    # first to keep register_and_login's normal callers unaffected.
    if not database.get_all_users():
        client.post('/register', data={
            'username': '_seed_owner', 'password': 'SeedOwner123', 'confirm_password': 'SeedOwner123',
        })
        client.post('/logout')

    client.post('/logout')  # in case a different user is already logged in, so register isn't skipped
    client.post('/register', data={
        'username': username, 'password': password, 'confirm_password': password,
    })
    if promote_admin:
        conn = database.get_connection()
        conn.execute("UPDATE users SET role = 'admin' WHERE username = ?", (username,))
        conn.commit()
        conn.close()
    return client.post('/login', data={'username': username, 'password': password}, follow_redirects=True)
