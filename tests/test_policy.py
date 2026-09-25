import copy
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from control.policy import (
    quota_from_usage, preferred_runner, runner_expression, recovery_candidate,
    infra_failure, has_executed_steps,
)

NOW = datetime(2026, 1, 2, tzinfo=timezone.utc)
LABELS = ['self-hosted', 'Linux', 'X64', 'kodify-vps', 'kodify-fallback-enabled']


def usage(gross='0', net='0'):
    return {'usageItems': [{'product': 'Actions', 'unitType': 'minutes',
                            'grossAmount': gross, 'netAmount': net}]}


def runner(status='online', busy=False):
    return {'status': status, 'busy': busy, 'labels': [{'name': x} for x in LABELS]}


def run(status='in_progress', conclusion=None, attempt=1):
    return {'status': status, 'conclusion': conclusion, 'run_attempt': attempt,
            'created_at': (NOW - timedelta(minutes=10)).isoformat()}


def job(status='queued', conclusion=None, name='test'):
    return {'id': 1, 'name': name, 'status': status, 'conclusion': conclusion,
            'labels': LABELS, 'runner_id': None, 'steps': []}


class QuotaTests(unittest.TestCase):
    def test_remaining_quota_stays_hosted(self):
        q = quota_from_usage(usage('6'), 2000)
        self.assertEqual(q.used_equivalent_minutes, Decimal(1000))
        self.assertEqual(preferred_runner(q, [runner()], private=True), 'github')

    def test_reserve_switches_before_limit(self):
        q = quota_from_usage(usage('11.4'), 2000)
        self.assertEqual(preferred_runner(q, [runner()], private=True), 'vps')

    def test_paid_compute_switches_even_with_delayed_feed(self):
        q = quota_from_usage(usage('1', '.01'), 2000)
        self.assertEqual(preferred_runner(q, [runner()], private=True), 'vps')

    def test_busy_is_not_offline(self):
        q = quota_from_usage(usage('12'), 2000)
        self.assertEqual(preferred_runner(q, [runner(busy=True)], private=True), 'vps')

    def test_offline_falls_back_hosted(self):
        q = quota_from_usage(usage('12'), 2000)
        self.assertEqual(preferred_runner(q, [runner('offline')], private=True), 'github')

    def test_public_repositories_remain_hosted(self):
        q = quota_from_usage(usage('12'), 2000)
        self.assertEqual(preferred_runner(q, [runner()], private=False), 'github')

    def test_unknown_quota_fails_to_hosted(self):
        self.assertEqual(preferred_runner(None, [runner()], private=True), 'github')

    def test_storage_and_other_products_excluded(self):
        report = usage('6')
        report['usageItems'] += [
            {'product': 'Actions', 'unitType': 'gigabyte-hours', 'grossAmount': 50, 'netAmount': 50},
            {'product': 'Copilot', 'unitType': 'minutes', 'grossAmount': 50, 'netAmount': 50},
        ]
        q = quota_from_usage(report, 2000)
        self.assertEqual(q.used_equivalent_minutes, Decimal(1000))
        self.assertFalse(q.paid_compute)

    def test_reset_restores_hosted(self):
        self.assertEqual(preferred_runner(quota_from_usage(usage(), 2000), [runner()], private=True), 'github')

    def test_malformed_usage_rejected(self):
        for report in [{}, usage('-1'), usage('NaN'), usage('Infinity')]:
            with self.assertRaises(ValueError):
                quota_from_usage(report, 2000)

    def test_mixed_os_normalization(self):
        report = usage('6')
        report['usageItems'].append({'product': 'Actions', 'unitType': 'minutes', 'grossAmount': '3', 'netAmount': '0'})
        self.assertEqual(quota_from_usage(report, 2000).used_equivalent_minutes, Decimal(1500))


class RecoveryTests(unittest.TestCase):
    def decide(self, r=None, jobs=None, annotations=None, online=False, safe=frozenset()):
        return recovery_candidate(r or run(), jobs if jobs is not None else [job()],
            annotations or {}, now=NOW, runners_online=online, safe_job_names=safe)

    def test_offline_unstarted_queue_can_be_cancelled(self):
        self.assertEqual(self.decide(), 'cancel-queued')

    def test_busy_online_queue_is_preserved(self):
        self.assertEqual(self.decide(online=True), 'none')

    def test_grace_prevents_races(self):
        r = run(); r['created_at'] = NOW.isoformat()
        self.assertEqual(self.decide(r), 'none')

    def test_strict_vps_is_never_overridden(self):
        j = job(); j['labels'] = ['kodify-vps', 'kodify-fallback-disabled']
        self.assertEqual(self.decide(jobs=[j]), 'none')

    def test_running_work_is_not_cancelled(self):
        self.assertEqual(self.decide(jobs=[job(), job('in_progress', name='deploy')]), 'none')

    def test_environment_approval_is_not_bypassed(self):
        self.assertEqual(self.decide(jobs=[job(), job('waiting')]), 'none')

    def test_manual_cancel_is_respected(self):
        self.assertEqual(self.decide(run('completed', 'cancelled')), 'none')

    def test_no_infinite_retry(self):
        self.assertEqual(self.decide(run(attempt=2)), 'none')

    def test_code_failure_does_not_retry(self):
        self.assertEqual(self.decide(run('completed', 'failure'), [job('completed', 'failure')]), 'none')

    def test_infra_failure_before_execution_retries(self):
        a = {1: [{'annotation_level': 'failure', 'message': 'The runner has lost communication with the server.'}]}
        self.assertEqual(self.decide(run('completed', 'failure'), [job('completed', 'failure')], a), 'retry-failed')

    def test_interrupted_deploy_requires_human(self):
        j = job('completed', 'failure', 'deploy'); j['runner_id'] = 42
        a = {1: [{'annotation_level': 'failure', 'message': 'The runner has lost communication with the server.'}]}
        self.assertEqual(self.decide(run('completed', 'failure'), [j], a), 'manual')

    def test_reviewed_idempotent_test_can_retry(self):
        j = job('completed', 'failure'); j['runner_id'] = 42
        a = {1: [{'annotation_level': 'failure', 'message': 'The runner has lost communication with the server.'}]}
        self.assertEqual(self.decide(run('completed', 'failure'), [j], a, safe=frozenset({'test'})), 'retry-failed')

    def test_warning_is_not_infra_failure(self):
        self.assertFalse(infra_failure([{'annotation_level': 'warning', 'message': 'The runner has lost communication with the server.'}]))

    def test_no_jobs_is_not_recoverable(self):
        self.assertEqual(self.decide(jobs=[]), 'none')

    def test_completed_success_is_untouched(self):
        self.assertEqual(self.decide(run('completed', 'success'), [job('completed', 'success')]), 'none')

    def test_skipped_steps_are_not_execution(self):
        j = job(); j['steps'] = [{'status': 'completed', 'conclusion': 'skipped'}]
        self.assertFalse(has_executed_steps(j))


class ExpressionTests(unittest.TestCase):
    def test_expression_has_explicit_choice_and_strict_labels(self):
        value = runner_expression()
        for marker in ['inputs.runner_target', 'inputs.allow_fallback', 'github.run_attempt',
                       'vars.KODIFY_RUNNER_MODE', 'kodify-fallback-enabled', 'kodify-fallback-disabled']:
            self.assertIn(marker, value)

    def test_other_os_cannot_be_silently_rewritten(self):
        for label in ['windows-latest', 'ubuntu-24.04-arm', 'macos-latest']:
            with self.assertRaises(ValueError): runner_expression(label)


if __name__ == '__main__':
    unittest.main()
