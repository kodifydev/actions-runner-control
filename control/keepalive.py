"""Bounded public activity heartbeat to keep legitimate supervision scheduled."""
import base64
from datetime import datetime, timedelta, timezone
import os
import re
from control.api import APIError, GitHub


def refresh(api, repository, now):
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository):
        raise ValueError('Invalid repository')
    path = f'/repos/{repository}/contents/.github/runner-control-heartbeat'
    prior = None
    try:
        prior = api.request('GET', path + '?ref=main')
        stamp = datetime.fromisoformat(base64.b64decode(prior['content']).decode().strip())
        if now - stamp < timedelta(days=28):
            return False
    except APIError as exc:
        if exc.status != 404:
            raise
    value = now.isoformat() + '\n'
    payload = {'message': 'chore: keep independent runner supervision active [skip ci]',
               'content': base64.b64encode(value.encode()).decode(), 'branch': 'main'}
    if prior:
        payload['sha'] = prior['sha']
    api.request('PUT', path, payload)
    result = api.request('GET', path + '?ref=main')
    if base64.b64decode(result['content']).decode() != value:
        raise RuntimeError('Heartbeat verification failed')
    return True


if __name__ == '__main__':
    changed = refresh(GitHub(os.environ['GH_TOKEN']), os.environ['GITHUB_REPOSITORY'],
                      datetime.now(timezone.utc))
    print('Scheduler heartbeat refreshed' if changed else 'Scheduler heartbeat current')
