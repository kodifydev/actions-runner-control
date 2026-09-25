"""Small GitHub API client with bounded timeouts and redacted failures."""
from __future__ import annotations
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


class APIError(RuntimeError):
    def __init__(self, status: int):
        self.status = status
        # Paths and server bodies may contain private repository information.
        super().__init__(f"GitHub API returned HTTP {status}")


class GitHub:
    def __init__(self, token: str, base: str = 'https://api.github.com'):
        if not token:
            raise ValueError('Missing GitHub token')
        if base != 'https://api.github.com':
            raise ValueError('Untrusted API origin')
        self.token = token
        self.base = base

    def request(self, method: str, path: str, data=None) -> Any:
        if not path.startswith('/') or path.startswith('//') or '..' in path:
            raise ValueError('Invalid API path')
        payload = None if data is None else json.dumps(data).encode()
        req = urllib.request.Request(self.base + path, data=payload, method=method,
            headers={'Authorization': f'Bearer {self.token}',
                     'Accept': 'application/vnd.github+json',
                     'X-GitHub-Api-Version': '2026-03-10',
                     'Content-Type': 'application/json',
                     'User-Agent': 'quota-aware-runner-control'})
        # No automatic retries for mutations: an ambiguous POST must be read back.
        attempts = 3 if method == 'GET' else 1
        for attempt in range(attempts):
            try:
                with urllib.request.urlopen(req, timeout=30) as response:
                    body = response.read()
                    return json.loads(body) if body else None
            except urllib.error.HTTPError as exc:
                if method == 'GET' and exc.code in {429, 502, 503, 504} and attempt + 1 < attempts:
                    raw_delay = exc.headers.get('Retry-After') or str(2 ** attempt)
                    delay = int(raw_delay) if raw_delay.isdigit() else 2 ** attempt
                    time.sleep(min(15, max(1, delay)))
                    continue
                raise APIError(exc.code) from None
            except urllib.error.URLError:
                if attempt + 1 < attempts:
                    time.sleep(2 ** attempt)
                    continue
                raise APIError(0) from None

    def pages(self, path: str, key: str | None = None):
        all_items = []
        sep = '&' if '?' in path else '?'
        page = 1
        while True:
            data = self.request('GET', f'{path}{sep}per_page=100&page={page}')
            items = data[key] if key else data
            if not isinstance(items, list):
                raise ValueError('Malformed paginated response')
            all_items.extend(items)
            if len(items) < 100:
                return all_items
            page += 1
            if page > 100:
                raise ValueError('Pagination safety limit reached')

    def variables(self, repo: str) -> dict[str, str]:
        return {item['name']: item['value'] for item in self.pages(
            f'/repos/{repo}/actions/variables', 'variables')}

    def set_variable(self, repo: str, name: str, value: str) -> None:
        path = f'/repos/{repo}/actions/variables/{name}'
        try:
            old = self.request('GET', path)
            if old['value'] == value:
                return
            self.request('PATCH', path, {'name': name, 'value': value})
        except APIError as exc:
            if exc.status != 404:
                raise
            self.request('POST', f'/repos/{repo}/actions/variables', {'name': name, 'value': value})
        if self.request('GET', path)['value'] != value:
            raise RuntimeError('Variable write verification failed')
