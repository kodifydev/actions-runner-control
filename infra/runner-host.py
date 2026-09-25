#!/usr/bin/env python3
"""One disposable runner cycle, supervised/restarted by systemd.

Only this trusted host process uses gh's local credentials. Neither the assistant
home nor its GitHub credential is ever passed into the job runtime. The runtime
and all writable layers are rebuilt from a root-owned archive between jobs.
"""
from __future__ import annotations
import gzip
import json

import pathlib
import shutil
import subprocess
import time
import uuid

DOCKER = ['sudo', '-n', '-u', 'kodify-ci', '/opt/kodify-runner/docker/docker',
          '--host', 'unix:///run/kodify-ci/docker.sock']
SERVICE = 'kodify-rootless-docker.service'
STATE = pathlib.Path('/var/lib/kodify-runner-host/registration.json')


def checked(args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def api(method, path, payload=None):
    command = ['gh', 'api', '-X', method, path]
    if payload is not None:
        command += ['--input', '-']
    result = subprocess.run(command, input=json.dumps(payload) if payload is not None else None,
                            text=True, capture_output=True, timeout=45)
    if result.returncode:
        # Never include response bodies or credential-bearing registration data.
        raise RuntimeError('GitHub runner API request failed')
    return json.loads(result.stdout) if result.stdout.strip() else {}


def unregister(organization, runner_id):
    cleanup = subprocess.run(['gh', 'api', '-X', 'DELETE',
        f'orgs/{organization}/actions/runners/{int(runner_id)}'],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=45)
    # A completed ephemeral job removes itself; 404 is an expected absence.
    return cleanup.returncode == 0 or b'HTTP 404' in cleanup.stderr


def main():
    config = json.loads(pathlib.Path('/etc/kodify-runner.json').read_text())
    if not shutil.which('gh'):
        raise RuntimeError('GitHub CLI missing from lifecycle service PATH')
    archive = pathlib.Path('/opt/kodify-runner/runner-image.tar.gz')
    if archive.stat().st_uid != 0 or archive.stat().st_mode & 0o022:
        raise RuntimeError('Runner archive must be root-owned and not writable by jobs')
    checked(['sudo', '-n', 'systemctl', 'stop', SERVICE])
    if STATE.exists():
        previous = json.loads(STATE.read_text())
        if previous['organization'] != config['organization']:
            raise RuntimeError('Previous runner registration belongs to another organization')
        if not unregister(config['organization'], previous['runner_id']):
            raise RuntimeError('Could not remove previous isolated runner registration')
        STATE.unlink()
    checked(['sudo', '-n', 'python3', '/opt/kodify-runner/reset-runtime.py'])
    checked(['sudo', '-n', 'systemctl', 'start', SERVICE])
    # Real health probes, bounded startup wait; no assumption from systemctl success.
    for attempt in range(30):
        result = subprocess.run(DOCKER + ['info', '--format', '{{.ServerVersion}}'],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
        if result.returncode == 0:
            break
        time.sleep(1)
    else:
        raise RuntimeError('Isolated Docker runtime did not become ready')
    with gzip.open(archive, 'rb') as source:
        process = subprocess.Popen(DOCKER + ['image', 'load', '--quiet'], stdin=subprocess.PIPE,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        assert process.stdin is not None
        try:
            while chunk := source.read(1024 * 1024):
                process.stdin.write(chunk)
            process.stdin.close()
        except BaseException:
            process.kill()
            process.wait()
            raise
        if process.wait(timeout=300):
            raise RuntimeError('Trusted runner image could not be restored')
    image = config['image_id']
    checked(DOCKER + ['image', 'inspect', image], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # Container jobs bind runner externals from the Docker daemon's filesystem.
    # Use the same isolated absolute path in both namespaces, never host /home.
    runner_path = '/var/lib/kodifyci/data/runner'
    checked(DOCKER + ['run', '--rm', '--user', '0', '--entrypoint', 'bash',
        '--mount', f'type=bind,src={runner_path},dst=/export', image,
        '-c', 'cp -a /home/runner/. /export/'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    name = 'kodify-vps-' + uuid.uuid4().hex[:12]
    registration = api('POST', f"orgs/{config['organization']}/actions/runners/generate-jitconfig", {
        'name': name, 'runner_group_id': config['runner_group_id'],
        'labels': ['self-hosted', 'Linux', 'X64', 'kodify-vps',
                   'kodify-fallback-enabled', 'kodify-fallback-disabled'],
        'work_folder': '/var/lib/kodifyci/data/work/current',
    })
    runner_id = registration['runner']['id']
    STATE.write_text(json.dumps({'organization': config['organization'], 'runner_id': runner_id}))
    print(f'Ephemeral runner registered: {runner_id}', flush=True)
    try:
        command = DOCKER + ['run', '--rm', '-i', '--name', 'kodify-job-runner', '--group-add', '0',
            '--env', 'RUNNER_TOOL_CACHE=/var/lib/kodifyci/data/work/toolcache',
            '--env', 'AGENT_TOOLSDIRECTORY=/var/lib/kodifyci/data/work/toolcache',
            '--env', 'PATH=/opt/runner-bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin',
            '--mount', 'type=bind,src=/opt/kodify-runner/docker-wrapper.py,dst=/opt/runner-bin/docker,readonly',
            '--workdir', runner_path,
            '--mount', f'type=bind,src={runner_path},dst={runner_path}',
            '--mount', 'type=bind,src=/run/kodify-ci/docker.sock,dst=/var/run/docker.sock',
            '--mount', 'type=bind,src=/var/lib/kodifyci/data/work,dst=/var/lib/kodifyci/data/work',
            image]
        result = subprocess.run(command, input=registration['encoded_jit_config'] + '\n',
                                text=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print(f'Ephemeral runner cycle ended: exit={result.returncode}', flush=True)
        return result.returncode
    finally:
        # The service is a dedicated runtime: stop all job descendants before
        # another cycle can use any cache or writable filesystem state.
        checked(['sudo', '-n', 'systemctl', 'stop', SERVICE])
        if unregister(config['organization'], runner_id):
            STATE.unlink(missing_ok=True)


if __name__ == '__main__':
    raise SystemExit(main())
