import importlib.util
from pathlib import Path
import unittest
from tools.migrate_workflow import loader
from tools.tests_only_routing import migrate

BASE = '''name: CI
on:
  push:
    branches: [main]
  workflow_dispatch:
    inputs:
      runner_target:
        type: choice
        options: [auto, github, vps]
      allow_fallback:
        type: boolean
        default: true
jobs:
  test:
    runs-on: "${{ vars.KODIFY_RUNNER_MODE }}"
    steps:
      - run: uv run pytest -n auto
  release:
    runs-on: "${{ vars.KODIFY_RUNNER_MODE }}"
    needs: test
    if: github.event_name == 'push'
    env:
      SECRET: "${{ secrets.PRODUCTION_SECRET }}"
    steps:
      - run: deploy-do-not-change
'''

class TestsOnlyRoutingTests(unittest.TestCase):
    def test_only_test_job_is_arm64(self):
        data = loader().load(migrate(BASE))
        self.assertIn('ARM64', data['jobs']['test']['runs-on'])
        self.assertIn('kodify-tests', data['jobs']['test']['runs-on'])
        self.assertEqual(data['jobs']['release']['runs-on'], 'ubuntu-latest')
        self.assertEqual(data['jobs']['test']['permissions'], {'contents': 'read'})
        self.assertEqual(data['jobs']['test']['env']['PYTEST_XDIST_AUTO_NUM_WORKERS'], '2')
        self.assertEqual(data['jobs']['test']['timeout-minutes'], 20)

    def test_release_steps_secrets_dependencies_and_push_unchanged(self):
        old = loader().load(BASE)
        new = loader().load(migrate(BASE))
        for field in ['steps', 'env', 'needs']:
            self.assertEqual(old['jobs']['release'][field], new['jobs']['release'][field])
        self.assertEqual(old['on']['push'], new['on']['push'])
        self.assertIn('inputs.tests_only != true', new['jobs']['release']['if'])
        self.assertFalse(new['on']['workflow_dispatch']['inputs']['tests_only']['default'])

    def test_release_only_workflow_has_no_jetson_choice(self):
        data = loader().load(migrate(BASE.replace('  test:\n    runs-on: "${{ vars.KODIFY_RUNNER_MODE }}"\n    steps:\n      - run: uv run pytest -n auto\n', '').replace('    needs: test\n', '')))
        self.assertEqual(data['on']['workflow_dispatch']['inputs']['runner_target']['options'], ['auto', 'github'])
        self.assertNotIn('tests_only', data['on']['workflow_dispatch']['inputs'])

    def test_unknown_services_fail_closed(self):
        with self.assertRaises(ValueError):
            migrate(BASE.replace('  test:\n', '  test:\n    services:\n      db:\n        image: unknown\n'))

    def test_forks_dependabot_and_retries_stay_hosted(self):
        expression = loader().load(migrate(BASE))['jobs']['test']['runs-on']
        for guard in ['pull_request_target', 'dependabot[bot]', 'head.repo.full_name == github.repository', 'github.run_attempt == 1', 'inputs.allow_fallback == false']:
            self.assertIn(guard, expression)

    def test_postgres_service_becomes_disposable_local_db(self):
        original = BASE.replace('  test:\n', '''  test:
    container: ghcr.io/astral-sh/uv:python3.12-alpine
    env:
      PG_HOST: postgres
    services:
      postgres:
        image: pgvector/pgvector:pg18-trixie
''')
        data = loader().load(migrate(original))['jobs']['test']
        self.assertNotIn('container', data)
        self.assertNotIn('services', data)
        self.assertEqual(data['env']['PG_HOST'], '127.0.0.1')
        starts = [s for s in data['steps'] if s.get('name') == 'Start isolated PostgreSQL on GitHub']
        self.assertEqual(len(starts), 1)
        self.assertEqual(starts[0]['if'], "runner.environment == 'github-hosted'")
        self.assertIn('statement_timeout=300000', starts[0]['run'])

class LauncherTests(unittest.TestCase):
    def test_container_has_no_host_mounts_socket_devices_or_privilege(self):
        path = Path(__file__).parents[1] / 'infra/tests-arm64/launch.py'
        spec = importlib.util.spec_from_file_location('test_launcher', path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        cmd = module.command('sha256:' + 'a'*64)
        for forbidden in ['--privileged', '--mount', '--volume', '-v', '--device', '--pid', '--uts']:
            self.assertNotIn(forbidden, cmd)
        self.assertEqual(cmd[cmd.index('--cap-drop')+1], 'ALL')
        self.assertEqual(cmd[cmd.index('--security-opt')+1], 'no-new-privileges')
        self.assertEqual(cmd[cmd.index('--memory')+1], '6g')
        self.assertEqual(cmd[cmd.index('--memory-swap')+1], '6g')
        self.assertEqual(cmd[cmd.index('--cpus')+1], '2')
        self.assertEqual(cmd[cmd.index('--network')+1], 'kodify-ci-tests')
        self.assertEqual(cmd[cmd.index('--pids-limit')+1], '512')
        self.assertIn('--rm', cmd)
