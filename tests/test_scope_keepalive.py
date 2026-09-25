import base64
from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import Mock
from control.api import APIError
from control.supervisor import Supervisor
from control.keepalive import refresh


class ScopeTests(unittest.TestCase):
    def test_mutation_requires_scope_before_any_api(self):
        api = Mock()
        with self.assertRaises(ValueError):
            Supervisor(api, 'example', apply=True).tick()
        api.request.assert_not_called()

    def test_unselected_repository_is_not_even_inspected(self):
        api = Mock()
        api.request.return_value = {'usageItems': []}
        api.pages.side_effect = [[], [
            {'name': n, 'owner': {'login': 'example'}} for n in ['selected', 'other']]]
        supervisor = Supervisor(api, 'example', allowed_repositories={'selected'})
        supervisor.inspect_repository = Mock()
        supervisor.tick()
        self.assertEqual(supervisor.inspect_repository.call_count, 1)
        self.assertEqual(supervisor.inspect_repository.call_args.args[0]['name'], 'selected')

    def test_empty_readonly_scope_touches_no_repositories(self):
        api = Mock()
        api.request.return_value = {'usageItems': []}
        api.pages.side_effect = [[], [{'name': 'other', 'owner': {'login': 'example'}}]]
        supervisor = Supervisor(api, 'example', allowed_repositories=set())
        supervisor.inspect_repository = Mock()
        supervisor.tick()
        supervisor.inspect_repository.assert_not_called()


class KeepaliveTests(unittest.TestCase):
    now = datetime(2026, 9, 25, tzinfo=timezone.utc)

    def content(self, date):
        return {'sha': 'known-sha', 'content': base64.b64encode((date.isoformat()+'\n').encode()).decode()}

    def test_recent_heartbeat_performs_no_write(self):
        api = Mock()
        api.request.return_value = self.content(self.now - timedelta(days=10))
        self.assertFalse(refresh(api, 'example/control', self.now))
        self.assertEqual(api.request.call_count, 1)

    def test_old_heartbeat_updates_exact_file_and_verifies(self):
        api = Mock()
        api.request.side_effect = [self.content(self.now-timedelta(days=29)), {}, self.content(self.now)]
        self.assertTrue(refresh(api, 'example/control', self.now))
        method,path,payload = api.request.call_args_list[1].args
        self.assertEqual(method, 'PUT')
        self.assertEqual(path, '/repos/example/control/contents/.github/runner-control-heartbeat')
        self.assertEqual(payload['sha'], 'known-sha')
        self.assertEqual(payload['branch'], 'main')

    def test_missing_heartbeat_creates_without_sha(self):
        api = Mock()
        api.request.side_effect = [APIError(404), {}, self.content(self.now)]
        self.assertTrue(refresh(api, 'example/control', self.now))
        self.assertNotIn('sha', api.request.call_args_list[1].args[2])

    def test_permission_failure_never_becomes_create(self):
        api = Mock()
        api.request.side_effect = APIError(403)
        with self.assertRaises(APIError):
            refresh(api, 'example/control', self.now)
        self.assertEqual(api.request.call_count, 1)
