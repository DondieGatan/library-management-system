from conftest import register_and_login


def test_register_and_login_succeeds(client):
    resp = register_and_login(client)
    assert resp.status_code == 200
    assert b'Welcome back' in resp.data or b'Dashboard' in resp.data or b'Books' in resp.data


def test_new_signups_default_to_member_role(client):
    # the *first* account in a fresh db becomes owner (see the dedicated
    # bootstrap test below), so register someone else first to get a
    # normal, non-first signup here.
    register_and_login(client, username='first_account')
    register_and_login(client, username='bob')
    import database
    user = database.get_user_by_username('bob')
    assert user['role'] == 'member'


def test_first_registered_user_becomes_owner(client):
    # register directly, bypassing register_and_login's auto-seeded
    # throwaway owner -- this test is specifically about who lands in
    # that first-user slot on a truly fresh database.
    client.post('/register', data={
        'username': 'founder', 'email': 'founder@example.com',
        'password': 'password123', 'confirm_password': 'password123',
    })
    import database
    user = database.get_user_by_username('founder')
    assert user['role'] == 'owner'


def test_login_rejects_wrong_password(client):
    client.post('/register', data={
        'username': 'carol', 'email': 'carol@example.com',
        'password': 'password123', 'confirm_password': 'password123',
    })
    resp = client.post('/login', data={'username': 'carol', 'password': 'wrongpass'}, follow_redirects=True)
    assert b'Incorrect username or password' in resp.data


def test_register_rejects_short_password(client):
    resp = client.post('/register', data={
        'username': 'dave', 'email': 'dave@example.com',
        'password': 'short', 'confirm_password': 'short',
    }, follow_redirects=True)
    assert b'at least 8 characters' in resp.data
    import database
    assert database.get_user_by_username('dave') is None


def test_register_rejects_mismatched_passwords(client):
    resp = client.post('/register', data={
        'username': 'erin', 'email': 'erin@example.com',
        'password': 'password123', 'confirm_password': 'password456',
    }, follow_redirects=True)
    assert b'do not match' in resp.data


def test_register_rejects_duplicate_username(client):
    client.post('/register', data={
        'username': 'frank', 'email': 'frank@example.com',
        'password': 'password123', 'confirm_password': 'password123',
    })
    resp = client.post('/register', data={
        'username': 'frank', 'email': 'frank2@example.com',
        'password': 'password123', 'confirm_password': 'password123',
    }, follow_redirects=True)
    assert b'already taken' in resp.data


def test_register_rejects_duplicate_email(client):
    client.post('/register', data={
        'username': 'gina', 'email': 'shared@example.com',
        'password': 'password123', 'confirm_password': 'password123',
    })
    resp = client.post('/register', data={
        'username': 'gina2', 'email': 'shared@example.com',
        'password': 'password123', 'confirm_password': 'password123',
    }, follow_redirects=True)
    assert b'already exists' in resp.data


def test_register_rejects_invalid_email(client):
    resp = client.post('/register', data={
        'username': 'henry', 'email': 'not-an-email',
        'password': 'password123', 'confirm_password': 'password123',
    }, follow_redirects=True)
    assert b'valid email' in resp.data
    import database
    assert database.get_user_by_username('henry') is None


def test_protected_route_redirects_when_logged_out(client):
    resp = client.get('/books', follow_redirects=True)
    assert b'Please log in to continue' in resp.data


def test_logout_clears_session(client):
    register_and_login(client, username='grace')
    client.post('/logout')
    resp = client.get('/books', follow_redirects=True)
    assert b'Please log in to continue' in resp.data


def test_deleted_user_session_is_rejected(client):
    # A still-valid session cookie shouldn't keep working once the
    # underlying user row is gone -- the app must re-check the DB per
    # request rather than trusting cached session data.
    register_and_login(client, username='heidi')

    import database
    conn = database.get_connection()
    conn.execute("DELETE FROM users WHERE username = 'heidi'")
    conn.commit()
    conn.close()

    resp = client.get('/books', follow_redirects=True)
    assert b'Please log in to continue' in resp.data or b'no longer available' in resp.data

    # and the nav bar should no longer show the deleted user as logged in
    assert b'heidi' not in resp.data


def test_demoted_admin_loses_access_on_next_request(client):
    # Promoting/demoting a role should take effect immediately, not only
    # after the affected user logs out and back in.
    register_and_login(client, username='ivan', promote_admin=True)

    resp = client.get('/books/add')
    assert resp.status_code == 200

    import database
    conn = database.get_connection()
    conn.execute("UPDATE users SET role = 'member' WHERE username = 'ivan'")
    conn.commit()
    conn.close()

    resp = client.get('/books/add', follow_redirects=True)
    assert b'requires an admin account' in resp.data


class TestPasswordReset:
    """The /reset-password route only gates on a Flask session flag with no
    expiry of its own -- database.reset_token_still_valid() is the second,
    time-based check that closes the gap where a browser tab left open past
    the code's TTL could otherwise still set a new password with no live
    verification at all (the same bug found and fixed in the sibling Smart
    Resume Analyser app)."""

    def _register(self, client, username='resetuser', email='resetuser@example.com', password='password123'):
        client.post('/register', data={
            'username': username, 'email': email,
            'password': password, 'confirm_password': password,
        })
        client.post('/logout')

    def _get_code(self, email):
        import database
        conn = database.get_connection()
        row = conn.execute('SELECT reset_token FROM users WHERE email = ?', (email,)).fetchone()
        conn.close()
        return row['reset_token']

    def test_forgot_password_does_not_reveal_unknown_email(self, client):
        resp = client.post('/forgot-password', data={'email': 'nobody@example.com'}, follow_redirects=True)
        assert b'a reset code has been sent' in resp.data

    def test_full_reset_flow_changes_password(self, client):
        self._register(client, email='fullflow@example.com')
        client.post('/forgot-password', data={'email': 'fullflow@example.com'})
        code = self._get_code('fullflow@example.com')

        verify_resp = client.post(
            '/verify-reset-code', data={'email': 'fullflow@example.com', 'code': code}, follow_redirects=True
        )
        assert b'Set your new password' in verify_resp.data or b'New Password' in verify_resp.data

        reset_resp = client.post(
            '/reset-password',
            data={'password': 'brandnewpass456', 'confirm_password': 'brandnewpass456'},
            follow_redirects=True,
        )
        assert b'Password reset successfully' in reset_resp.data

        old_login = client.post('/login', data={'username': 'resetuser', 'password': 'password123'})
        assert b'Incorrect username or password' in old_login.data

        new_login = client.post(
            '/login', data={'username': 'resetuser', 'password': 'brandnewpass456'}, follow_redirects=True
        )
        assert b'Welcome back' in new_login.data

    def test_reset_password_rejects_wrong_code(self, client):
        self._register(client, email='wrongcode@example.com')
        client.post('/forgot-password', data={'email': 'wrongcode@example.com'})

        resp = client.post(
            '/verify-reset-code', data={'email': 'wrongcode@example.com', 'code': '000000'}, follow_redirects=True
        )
        assert b'Invalid or expired code' in resp.data

    def test_reset_password_route_rejects_expired_code_even_with_valid_session(self, client):
        # Verify successfully (this sets session['reset_email']), then
        # expire the code server-side afterward -- simulating a browser
        # tab left open past the code's TTL -- and confirm the
        # actual password-change step is still blocked.
        self._register(client, email='expiry@example.com')
        client.post('/forgot-password', data={'email': 'expiry@example.com'})
        code = self._get_code('expiry@example.com')
        client.post('/verify-reset-code', data={'email': 'expiry@example.com', 'code': code})

        import database
        conn = database.get_connection()
        from datetime import datetime, timedelta
        conn.execute(
            "UPDATE users SET reset_token_expiry = ? WHERE email = ?",
            ((datetime.now() - timedelta(minutes=1)).isoformat(), 'expiry@example.com'),
        )
        conn.commit()
        conn.close()

        resp = client.post(
            '/reset-password',
            data={'password': 'exploitedpass456', 'confirm_password': 'exploitedpass456'},
            follow_redirects=True,
        )
        assert b'expired' in resp.data

        # The original password must still work.
        login_resp = client.post(
            '/login', data={'username': 'resetuser', 'password': 'password123'}, follow_redirects=True
        )
        assert b'Welcome back' in login_resp.data
