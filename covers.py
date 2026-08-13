"""Resolves a book cover image URL from the Open Library covers API.

No API key needed. Results are cached on the book row (see
database.update_book_cover) so each book is only looked up once --
lookups happen lazily the first time a book is displayed.
"""
import json
import urllib.parse
import urllib.request
from typing import Any

TIMEOUT = 6  # Open Library's search endpoint routinely takes 3-4s to respond
MIN_COVER_BYTES = 3000  # Open Library returns a tiny blank placeholder for "no cover"


def _looks_like_real_cover(url: str) -> bool:
    try:
        req = urllib.request.Request(url, method='HEAD')
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            length = int(resp.headers.get('Content-Length', 0))
            return resp.status == 200 and length > MIN_COVER_BYTES
    except Exception:
        return False


def _first_doc_field(params: dict, field: str) -> Any | None:
    try:
        query = urllib.parse.urlencode({**params, 'limit': 5, 'fields': field})
        req = urllib.request.Request('https://openlibrary.org/search.json?' + query)
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            data = json.loads(resp.read())
        for doc in data.get('docs') or []:
            if doc.get(field):
                return doc[field]
    except Exception:
        pass
    return None


def _search_field(title: str, author: str | None, field: str) -> Any | None:
    # The top title+author match is often a specific edition missing the
    # field we want, even when other editions of the same book have it --
    # so fall back to a title-only search before giving up.
    if author:
        value = _first_doc_field({'title': title, 'author': author}, field)
        if value:
            return value
    return _first_doc_field({'title': title}, field)


def resolve_cover_url(isbn: str | None, title: str, author: str | None) -> str | None:
    """Returns a cover image URL, or None if nothing usable was found."""
    if isbn:
        clean_isbn = isbn.replace('-', '').replace(' ', '')
        if clean_isbn:
            url = f'https://covers.openlibrary.org/b/isbn/{clean_isbn}-M.jpg'
            if _looks_like_real_cover(url):
                return url

    cover_id = _search_field(title, author, 'cover_i')
    if cover_id:
        return f'https://covers.openlibrary.org/b/id/{cover_id}-M.jpg'

    return None


def resolve_description(isbn: str | None, title: str, author: str | None) -> str | None:
    """Returns a plain-text synopsis from the book's Open Library work
    entry, or None if nothing usable was found."""
    work_key = _search_field(title, author, 'key')
    if not work_key:
        return None
    try:
        req = urllib.request.Request(f'https://openlibrary.org{work_key}.json')
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            work = json.loads(resp.read())
    except Exception:
        return None
    description = work.get('description')
    if isinstance(description, dict):
        description = description.get('value')
    return description.strip() if description else None
