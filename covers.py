"""Resolves a book cover image URL from the Open Library covers API.

No API key needed. Results are cached on the book row (see
database.update_book_cover) so each book is only looked up once --
lookups happen lazily the first time a book is displayed.
"""
import json
import urllib.parse
import urllib.request

TIMEOUT = 6  # Open Library's search endpoint routinely takes 3-4s to respond
MIN_COVER_BYTES = 3000  # Open Library returns a tiny blank placeholder for "no cover"


def _looks_like_real_cover(url):
    try:
        req = urllib.request.Request(url, method='HEAD')
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            length = int(resp.headers.get('Content-Length', 0))
            return resp.status == 200 and length > MIN_COVER_BYTES
    except Exception:
        return False


def _first_cover_id(params):
    try:
        query = urllib.parse.urlencode({**params, 'limit': 5, 'fields': 'cover_i'})
        req = urllib.request.Request('https://openlibrary.org/search.json?' + query)
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            data = json.loads(resp.read())
        for doc in data.get('docs') or []:
            if doc.get('cover_i'):
                return doc['cover_i']
    except Exception:
        pass
    return None


def _search_cover_id(title, author):
    # The top title+author match is often a specific edition with no
    # cover on file, even when other editions of the same book have one --
    # so fall back to a title-only search before giving up.
    if author:
        cover_id = _first_cover_id({'title': title, 'author': author})
        if cover_id:
            return cover_id
    return _first_cover_id({'title': title})


def resolve_cover_url(isbn, title, author):
    """Returns a cover image URL, or None if nothing usable was found."""
    if isbn:
        clean_isbn = isbn.replace('-', '').replace(' ', '')
        if clean_isbn:
            url = f'https://covers.openlibrary.org/b/isbn/{clean_isbn}-M.jpg'
            if _looks_like_real_cover(url):
                return url

    cover_id = _search_cover_id(title, author)
    if cover_id:
        return f'https://covers.openlibrary.org/b/id/{cover_id}-M.jpg'

    return None
