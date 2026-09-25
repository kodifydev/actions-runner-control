#!/usr/bin/env python3
"""Root-owned bounded launcher. Jobs never receive this process's Docker socket."""
import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

NAME = 'kodify-tests-ephemeral'
CONFIG = Path('/etc/kodify-tests.json')

def command(image, probe=False):
    cmd = ['docker', 'run', '--rm', '-i', '--name', NAME, '--label', 'kodify.role=tests',
           '--network', 'kodify-ci-tests', '--dns', '1.1.1.1', '--dns', '8.8.8.8',
           '--sysctl', 'net.ipv6.conf.all.disable_ipv6=1', '--sysctl', 'net.ipv6.conf.default.disable_ipv6=1',
           '--cpus', '2', '--cpu-shares', '128', '--memory', '6g', '--memory-reservation', '5g',
           '--memory-swap', '6g', '--pids-limit', '512', '--oom-score-adj', '500',
           '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
           '--log-driver', 'local', '--log-opt', 'max-size=10m', '--log-opt', 'max-file=2',
           '--env', 'PYTEST_XDIST_AUTO_NUM_WORKERS=2',
           image]
    if probe:
        cmd.append('--probe')
    return cmd

def main():
    if os.geteuid() != 0:
        raise SystemExit('Trusted launcher requires root')
    probe = len(sys.argv) == 2 and sys.argv[1] == 'probe'
    if not probe and len(sys.argv) != 1:
        raise SystemExit('Unsupported invocation')
    with open('/run/lock/kodify-tests.lock', 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if shutil.disk_usage('/var/lib/docker').free < 20 * 1024**3:
            raise SystemExit('Insufficient disk headroom for CI')
        st = CONFIG.stat()
        if st.st_uid != 0 or st.st_mode & 0o022:
            raise SystemExit('Unsafe launcher configuration')
        image = json.loads(CONFIG.read_text())['image']
        if not image.startswith('sha256:') or len(image) != 71:
            raise SystemExit('Image must be pinned by local image ID')
        subprocess.run(['iptables', '-w', '-C', 'DOCKER-USER', '-i', 'br-kodify-test', '-j', 'KODIFY_TESTS'], check=True)
        subprocess.run(['iptables', '-w', '-C', 'INPUT', '-i', 'br-kodify-test', '-j', 'REJECT'], check=True)
        payload = b'' if probe else sys.stdin.buffer.readline(65537)
        if not probe and (not payload.strip() or len(payload) > 65536):
            raise SystemExit('Missing/oversized JIT configuration')
        output = None if probe else subprocess.DEVNULL
        proc = subprocess.Popen(command(image, probe), stdin=subprocess.PIPE, stdout=output, stderr=output)
        assert proc.stdin is not None
        proc.stdin.write(payload)
        proc.stdin.close()
        deadline = time.monotonic() + (120 if probe else 1800)
        try:
            while proc.poll() is None:
                if time.monotonic() > deadline or shutil.disk_usage('/var/lib/docker').free < 10 * 1024**3:
                    raise RuntimeError('CI watchdog: time/disk budget reached')
                time.sleep(2)
            return proc.returncode
        finally:
            # Exact name owned by this launcher, protected by a single-slot lock.
            subprocess.run(['docker', 'rm', '-f', NAME], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                proc.kill()

if __name__ == '__main__':
    raise SystemExit(main())
