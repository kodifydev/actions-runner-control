"""Trust-boundary regression checks; no root, network or real runtime mutations."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('runner_host', Path(__file__).parents[1] / 'infra/runner-host.py')
assert SPEC is not None and SPEC.loader is not None
host = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(host)


class RunnerHostTests(unittest.TestCase):
    def test_registration_failure_is_redacted(self):
        result = SimpleNamespace(returncode=1, stdout='sensitive-response', stderr='sensitive-response')
        with patch.object(host.subprocess, 'run', return_value=result):
            with self.assertRaisesRegex(RuntimeError, '^GitHub runner API request failed$'):
                host.api('POST', 'orgs/example/actions/runners/generate-jitconfig', {})

    def test_missing_cli_aborts_before_runtime_stop(self):
        with patch.object(host.pathlib.Path, 'read_text', return_value='{}'), \
             patch.object(host.shutil, 'which', return_value=None), \
             patch.object(host, 'checked') as checked:
            with self.assertRaisesRegex(RuntimeError, 'GitHub CLI missing'):
                host.main()
            checked.assert_not_called()

    def test_job_writable_archive_is_rejected(self):
        with patch.object(host.pathlib.Path, 'read_text', return_value='{}'), \
             patch.object(host.shutil, 'which', return_value='/usr/bin/gh'), \
             patch.object(host.pathlib.Path, 'stat', return_value=SimpleNamespace(st_uid=1001, st_mode=0o644)), \
             patch.object(host, 'checked') as checked:
            with self.assertRaisesRegex(RuntimeError, 'root-owned'):
                host.main()
            checked.assert_not_called()

    def test_cleanup_404_means_ephemeral_already_removed(self):
        result = SimpleNamespace(returncode=1, stderr=b'gh: Not Found (HTTP 404)')
        with patch.object(host.subprocess, 'run', return_value=result):
            self.assertTrue(host.unregister('example', 123))

    def test_cleanup_auth_failure_is_not_ignored(self):
        result = SimpleNamespace(returncode=1, stderr=b'gh: Forbidden (HTTP 403)')
        with patch.object(host.subprocess, 'run', return_value=result):
            self.assertFalse(host.unregister('example', 123))

    def test_unregistration_requires_numeric_id(self):
        with patch.object(host.subprocess, 'run') as run:
            with self.assertRaises(ValueError):
                host.unregister('example', '../other')
            run.assert_not_called()
