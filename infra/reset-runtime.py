#!/usr/bin/env python3
"""Root-only cleanup of the dedicated mounted CI filesystem, never host data."""
import os
import pathlib
import pwd
import shutil
import subprocess

ROOT = pathlib.Path('/var/lib/kodifyci/data')
SERVICE = 'kodify-rootless-docker.service'

if os.geteuid() != 0:
    raise SystemExit('Root required for subordinate-UID cleanup')
if subprocess.run(['systemctl', 'is-active', '--quiet', SERVICE]).returncode == 0:
    raise SystemExit('Refusing cleanup while the dedicated runtime is active')
if ROOT.is_symlink() or not os.path.ismount(ROOT) or ROOT.stat().st_dev == pathlib.Path('/').stat().st_dev:
    raise SystemExit('Refusing cleanup outside the dedicated mounted filesystem')
mounts = subprocess.check_output(['findmnt', '-R', '-n', '-o', 'TARGET', str(ROOT)], text=True).splitlines()
if mounts != [str(ROOT)]:
    raise SystemExit('Refusing cleanup with unexpected nested mounts')
owner = pwd.getpwnam('kodify-ci')
for entry in ROOT.iterdir():
    if entry.name == 'lost+found':
        continue
    if entry.is_symlink() or not entry.is_dir():
        entry.unlink()
    else:
        shutil.rmtree(entry)
os.chown(ROOT, owner.pw_uid, owner.pw_gid)
for name, mode in [('home', 0o700), ('work', 0o777), ('runner', 0o777)]:
    path = ROOT / name
    path.mkdir(mode=mode)
    os.chown(path, owner.pw_uid, owner.pw_gid)
    path.chmod(mode)
print('Dedicated CI filesystem reset; no previous job state retained')
