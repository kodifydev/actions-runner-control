import unittest
from control.api import GitHub, APIError
from control.supervisor import Supervisor


class FixtureAPI(GitHub):
    """Deterministic fixture for API transition tests; not a live API result."""
    def __init__(self, run, jobs=None):
        self.run = dict(run)
        self.jobs = jobs or []
        self.writes = []
        self.variables_written = []

    def request(self, method, path, data=None):
        if method == 'GET':
            return dict(self.run)
        self.writes.append((method, path))
        if path.endswith('/rerun-failed-jobs'):
            self.run['run_attempt'] += 1
            self.run['status'] = 'queued'

    def pages(self, path, key=None):
        return self.jobs

    def set_variable(self, repo, name, value):
        self.variables_written.append((repo, name, value))


class RecoveryTransitionTests(unittest.TestCase):
    def make(self, *, conclusion: str | None = 'cancelled', status='completed', attempt=1, assigned=False, apply=True):
        api = FixtureAPI({'status': status, 'conclusion': conclusion, 'run_attempt': attempt},
            [{'conclusion': 'cancelled', 'runner_id': 42 if assigned else None, 'steps': []}])
        return Supervisor(api, 'example', apply=apply), api

    def test_only_cancelled_unstarted_jobs_are_retried(self):
        s, api = self.make()
        result = s.attempt_recovery('example/private', {'run_id': 1, 'attempt': 1})
        self.assertIsNone(result)
        self.assertEqual(len(api.writes), 1)
        self.assertTrue(api.writes[0][1].endswith('/rerun-failed-jobs'))
        self.assertEqual(s.counts['recoveries_verified'], 1)

    def test_started_in_cancel_race_is_not_replayed(self):
        s, api = self.make(assigned=True)
        self.assertIsNone(s.attempt_recovery('example/private', {'run_id': 1, 'attempt': 1}))
        self.assertFalse(api.writes)
        self.assertEqual(api.variables_written[0][1], 'KODIFY_RUNNER_ATTENTION')

    def test_success_race_is_abandoned(self):
        s, api = self.make(conclusion='success')
        self.assertIsNone(s.attempt_recovery('example/private', {'run_id': 1, 'attempt': 1}))
        self.assertFalse(api.writes)

    def test_failure_race_is_not_hidden(self):
        s, api = self.make(conclusion='failure')
        self.assertIsNone(s.attempt_recovery('example/private', {'run_id': 1, 'attempt': 1}))
        self.assertFalse(api.writes)

    def test_pending_cancel_is_not_force_cancelled(self):
        s, api = self.make(status='in_progress', conclusion=None)
        entry = {'run_id': 1, 'attempt': 1}
        self.assertEqual(s.attempt_recovery('example/private', entry), entry)
        self.assertFalse(api.writes)

    def test_existing_rerun_is_not_submitted_twice(self):
        s, api = self.make(attempt=2)
        self.assertIsNone(s.attempt_recovery('example/private', {'run_id': 1, 'attempt': 1}))
        self.assertFalse(api.writes)

    def test_dry_run_performs_no_remote_writes(self):
        s, api = self.make(apply=False)
        entry = {'run_id': 1, 'attempt': 1}
        self.assertEqual(s.attempt_recovery('example/private', entry), entry)
        self.assertFalse(api.writes)
        self.assertFalse(api.variables_written)


class APISafetyTests(unittest.TestCase):
    def test_no_token_leak_in_errors(self):
        error = APIError(403)
        self.assertEqual(str(error), 'GitHub API returned HTTP 403')

    def test_untrusted_origin_rejected(self):
        with self.assertRaises(ValueError): GitHub('fixture-only', 'https://example.invalid')

    def test_empty_credential_rejected(self):
        with self.assertRaises(ValueError): GitHub('')

    def test_path_injection_rejected_before_network(self):
        api = GitHub('fixture-only')
        for path in ['https://example.invalid', '//example.invalid', '/../user']:
            with self.assertRaises(ValueError): api.request('GET', path)


if __name__ == '__main__':
    unittest.main()
