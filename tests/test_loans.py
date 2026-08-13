from conftest import register_and_login
import database


def _add_book_and_member():
    database.add_book('Loan Test Book', 'Some Author', '', 'Fiction', 1)
    database.add_member('Test Member', 'testmember@example.com', '')
    book = database.get_books(search='Loan Test Book')[0]
    member = database.get_members(search='Test Member')[0]
    return book, member


def test_borrow_decrements_available_copies(client):
    register_and_login(client, username='loanadmin1', promote_admin=True)
    book, member = _add_book_and_member()
    assert book['available_copies'] == 1

    resp = client.post('/loans/borrow', data={'book_id': book['id'], 'member_id': member['id']}, follow_redirects=True)
    assert b'Book borrowed' in resp.data

    updated = database.get_book(book['id'])
    assert updated['available_copies'] == 0


def test_cannot_borrow_book_with_zero_copies(client):
    register_and_login(client, username='loanadmin2', promote_admin=True)
    book, member = _add_book_and_member()
    client.post('/loans/borrow', data={'book_id': book['id'], 'member_id': member['id']})

    resp = client.post('/loans/borrow', data={'book_id': book['id'], 'member_id': member['id']}, follow_redirects=True)
    assert b'No copies available' in resp.data


def test_return_increments_available_copies(client):
    register_and_login(client, username='loanadmin3', promote_admin=True)
    book, member = _add_book_and_member()
    client.post('/loans/borrow', data={'book_id': book['id'], 'member_id': member['id']})

    loan = database.get_loans(status='borrowed')[0]
    resp = client.post(f'/loans/return/{loan["id"]}', follow_redirects=True)
    assert b'Book returned' in resp.data

    updated = database.get_book(book['id'])
    assert updated['available_copies'] == 1

    returned_loan = database.get_loans(status='returned')[0]
    assert returned_loan['id'] == loan['id']


def test_book_detail_shows_loan_history_for_admin_only(client):
    register_and_login(client, username='loanadmin4', promote_admin=True)
    book, member = _add_book_and_member()
    client.post('/loans/borrow', data={'book_id': book['id'], 'member_id': member['id']})

    resp = client.get(f'/books/{book["id"]}')
    assert b'Loan History' in resp.data
    assert b'Test Member' in resp.data
