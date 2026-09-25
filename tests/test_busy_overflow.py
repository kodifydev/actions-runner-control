"""Synthetic policy/API fixtures: never real deployment results."""
import copy
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import unittest
from unittest.mock import Mock

from control.policy import Quota, pool_state, preferred_runner, recovery_candidate
from control.supervisor import Supervisor

NOW = datetime(2026, 1, 2, tzinfo=timezone.utc)
BUSY = {'status': 'online', 'busy': True, 'labels': [{'name': 'kodify-vps'}]}
IDLE = {**BUSY, 'busy': False}
RUN = {'id': 7, 'status': 'queued', 'conclusion': None, 'run_attempt': 1,
       'created_at': (NOW - timedelta(seconds=90)).isoformat()}
JOB = {'id': 8, 'name': 'test', 'status': 'queued', 'conclusion': None,
       'runner_id': None, 'steps': [], 'labels': ['kodify-vps', 'kodify-fallback-enabled']}
QUOTA = Quota(Decimal(2000), Decimal(2000), True)


class BusyOverflowTests(unittest.TestCase):
    def decide(self, run=None, jobs=None, **kwargs):
        return recovery_candidate(run or RUN, jobs or [JOB], {}, now=NOW,
                                  runners_online=True, pool_saturated=True, **kwargs)

    def test_idle_capacity_prevents_overflow(self):
        self.assertEqual(preferred_runner(QUOTA, [BUSY, IDLE], private=True), 'vps')
        self.assertEqual(pool_state([BUSY, IDLE]), (True, False))

    def test_all_busy_uses_hosted_without_calling_it_offline(self):
        self.assertEqual(preferred_runner(QUOTA, [BUSY], private=True), 'github')
        self.assertEqual(pool_state([BUSY]), (True, True))

    def test_unknown_capacity_cannot_cancel_queue(self):
        self.assertEqual(pool_state([{**BUSY, 'busy': None}]), (True, False))
        self.assertEqual(pool_state([{**BUSY, 'labels': []}]), (False, False))

    def test_queue_overflows_after_short_capacity_grace(self):
        self.assertEqual(self.decide(), 'cancel-queued')
        self.assertEqual(self.decide({**RUN, 'created_at': NOW.isoformat()}), 'none')

    def test_busy_pool_does_not_override_existing_safety_gates(self):
        cases = [
            (RUN, [JOB, {**JOB, 'status': 'in_progress'}]),
            (RUN, [JOB, {**JOB, 'status': 'waiting'}]),
            (RUN, [{**JOB, 'runner_id': 123}]),
            (RUN, [{**JOB, 'labels': ['kodify-vps', 'kodify-fallback-disabled']}]),
            (RUN, [JOB, {**JOB, 'status': 'completed', 'conclusion': 'failure'}]),
            ({**RUN, 'status': 'completed', 'conclusion': 'cancelled'}, [JOB]),
            ({**RUN, 'run_attempt': 2}, [JOB]),
        ]
        for run, jobs in cases:
            with self.subTest(run=run, jobs=jobs):
                self.assertEqual(self.decide(run, jobs), 'none')

    def test_supervisor_rechecks_capacity_before_cancelling(self):
        for live_pool, should_cancel in [([BUSY], True), ([IDLE], False)]:
            api = Mock()
            api.variables.return_value = {'KODIFY_RUNNER_MANAGED': '1', 'KODIFY_RUNNER_MODE': 'vps'}
            def pages(path, key=None):
                if path == '/orgs/example/actions/runners': return copy.deepcopy(live_pool)
                if '/jobs?' in path: return [copy.deepcopy(JOB)]
                if '?status=queued' in path: return [copy.deepcopy(RUN)]
                return []
            api.pages.side_effect = pages
            api.request.return_value = copy.deepcopy(RUN)
            supervisor = Supervisor(api, 'example', apply=True, now=NOW)
            supervisor.inspect_repository({'full_name': 'example/private', 'name': 'private', 'private': True}, QUOTA, [BUSY])
            cancels = [call for call in api.request.call_args_list if call.args[0] == 'POST']
            self.assertEqual(bool(cancels), should_cancel)
            api.set_variable.assert_any_call('example/private', 'KODIFY_RUNNER_MODE', 'github')

    def test_runtime_supplies_python_alias_and_daemon_visible_home(self):
        root = Path(__file__).parents[1]
        self.assertIn('python-is-python3', (root / 'infra/Dockerfile').read_text())
        self.assertIn("'--env', f'HOME={runner_path}'", (root / 'infra/runner-host.py').read_text())

    def test_submitted_recovery_stage_survives_eventual_consistency(self):
        api = Mock()
        variables = {'KODIFY_RUNNER_MANAGED': '1', 'KODIFY_RUNNER_MODE': 'github',
                     'KODIFY_RUNNER_RECOVERY': json.dumps([
                         {'run_id': 7, 'attempt': 1, 'stage': 'cancel-requested'}])}
        api.variables.side_effect = lambda repo: dict(variables)
        api.set_variable.side_effect = lambda repo, key, value: variables.update({key: value})
        # The API deliberately continues returning attempt 1 after a successful POST.
        api.request.return_value = {**RUN, 'status': 'completed', 'conclusion': 'cancelled'}
        api.pages.side_effect = lambda path, key=None: [
            {**JOB, 'status': 'completed', 'conclusion': 'cancelled'}] if '/jobs?' in path else []
        repo = {'full_name': 'example/private', 'name': 'private', 'private': True}
        supervisor = Supervisor(api, 'example', apply=True, now=NOW)
        supervisor.inspect_repository(repo, QUOTA, [BUSY])
        self.assertEqual(json.loads(variables['KODIFY_RUNNER_RECOVERY'])[0]['stage'], 'rerun-submitted')
        supervisor.inspect_repository(repo, QUOTA, [BUSY])
        posts = [call for call in api.request.call_args_list if call.args[0] == 'POST']
        self.assertEqual(len(posts), 1, 'Stale GitHub reads must not repeat the rerun POST')
