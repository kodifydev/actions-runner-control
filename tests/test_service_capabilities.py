import unittest
from tools.migrate_workflow import migrate, loader


class ServiceCapabilityTests(unittest.TestCase):
    source = '''name: test
on: push
jobs:
  postgres:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:16
        ports: [5432:5432]
    steps:
      - run: psql -h 127.0.0.1 -c 'SELECT 1'
'''

    def test_host_published_services_remain_hosted_and_idempotent(self):
        text, report = migrate(self.source)
        data = loader().load(text)
        self.assertEqual(data['jobs']['postgres']['runs-on'], 'ubuntu-latest')
        self.assertEqual(data['on']['workflow_dispatch']['inputs']['runner_target']['options'], ['auto','github'])
        self.assertEqual(report['routed_jobs'], [])
        self.assertEqual(migrate(text)[0],text)

    def test_container_services_can_use_isolated_network(self):
        source = self.source.replace('    services:', '    container: ubuntu:24.04\n    services:').replace('        ports: [5432:5432]\n', '')
        text, report = migrate(source)
        self.assertEqual(report['routed_jobs'], ['postgres'])
        self.assertIn('vars.KODIFY_RUNNER_MODE',text)
