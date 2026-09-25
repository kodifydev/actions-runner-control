#!/usr/bin/env python3
"""One-job remote test runners; durable GitHub credentials never reach the edge host."""
from __future__ import annotations
import json
import os
from pathlib import Path
import subprocess
import time
import uuid


def api(method, path, data=None):
    cmd = ['gh', 'api', '-X', method, path]
    if data is not None:
        cmd += ['--input', '-']
    result = subprocess.run(cmd, input=json.dumps(data) if data is not None else None,
                            text=True, capture_output=True, timeout=60)
    if result.returncode:
        raise RuntimeError('GitHub API request failed (response suppressed)')
    return json.loads(result.stdout) if result.stdout.strip() else {}


def ssh_command(config):
    return ['ssh', '-i', config['ssh_key'], '-o', 'IdentitiesOnly=yes', '-o', 'BatchMode=yes',
            '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=15',
            '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=3',
            config['ssh_destination']]


def cycle(config, state):
    ssh = ssh_command(config)
    # Do not register capacity unless the endpoint and its guard are healthy.
    subprocess.run(ssh + ['sudo -n systemctl is-active --quiet kodify-tests-network.service'], check=True, timeout=25)
    if state.exists():
        saved = json.loads(state.read_text())
        # Refuse to replace an in-flight remote execution after a controller crash.
        active = subprocess.run(ssh + ["docker ps -q --filter name=^/kodify-tests-ephemeral$"],
                                text=True, capture_output=True, timeout=25)
        if active.returncode or active.stdout.strip():
            raise RuntimeError('Prior remote execution still active or unverifiable')
        delete = subprocess.run(['gh', 'api', '-X', 'DELETE',
            'orgs/' + config['organization'] + '/actions/runners/' + str(saved['runner_id'])],
            capture_output=True, text=True, timeout=60)
        if delete.returncode and 'HTTP 404' not in delete.stderr:
            raise RuntimeError('Stale runner cleanup failed')
        state.unlink()
    registration = api('POST', 'orgs/' + config['organization'] + '/actions/runners/generate-jitconfig', {
        'name': 'kodify-tests-arm64-' + uuid.uuid4().hex[:10],
        'runner_group_id': config['runner_group_id'],
        'labels': ['self-hosted', 'Linux', 'ARM64', 'kodify-tests', 'kodify-vps',
                   'kodify-fallback-enabled', 'kodify-fallback-disabled'],
        'work_folder': '/home/runner/_work',
    })
    runner_id = registration['runner']['id']
    state.write_text(json.dumps({'runner_id': runner_id}))
    print('Registered isolated ARM64 test runner', flush=True)
    try:
        result = subprocess.run(ssh + ['sudo -n python3 /opt/kodify-tests/launch.py'],
            input=registration['encoded_jit_config'] + '\n', text=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=1900)
        print('Remote test cycle completed: exit=' + str(result.returncode), flush=True)
    finally:
        # Preserve state on uncertain transport/timeout; next cycle checks remote idle.
        active = subprocess.run(ssh + ["docker ps -q --filter name=^/kodify-tests-ephemeral$"],
                                text=True, capture_output=True, timeout=25)
        if active.returncode == 0 and not active.stdout.strip():
            delete = subprocess.run(['gh', 'api', '-X', 'DELETE',
                'orgs/' + config['organization'] + '/actions/runners/' + str(runner_id)],
                capture_output=True, text=True, timeout=60)
            if delete.returncode == 0 or 'HTTP 404' in delete.stderr:
                state.unlink(missing_ok=True)


def main():
    config_path = Path(os.environ.get('KODIFY_TESTS_CONFIG', '/etc/kodify-tests-controller.json'))
    config = json.loads(config_path.read_text())
    state = Path(config['state_path'])
    state.parent.mkdir(parents=True, exist_ok=True)
    while True:
        try:
            cycle(config, state)
        except (RuntimeError, subprocess.SubprocessError, OSError, ValueError, KeyError):
            print('Test runner cycle failed safely; retrying after backoff', flush=True)
            time.sleep(30)
        time.sleep(3)


if __name__ == '__main__':
    main()
