import unittest
from tools.migrate_workflow import migrate, loader

BASE = '''name: Example
on:
  push:
    branches: [main]
  pull_request:
permissions:
  contents: read
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v6
      - run: python -m unittest
  deploy:
    needs: test
    if: github.event_name == 'push'
    environment: production
    runs-on: ubuntu-latest
    steps:
      - name: Existing command must not change
        run: ./deploy.sh
'''


class MigrationTests(unittest.TestCase):
    def test_preserves_commands_and_dependencies(self):
        migrated, report = migrate(BASE)
        d = loader().load(migrated)
        original = loader().load(BASE)
        self.assertEqual(d['jobs']['deploy']['steps'], original['jobs']['deploy']['steps'])
        self.assertEqual(d['jobs']['deploy']['needs'], 'test')
        self.assertEqual(d['jobs']['deploy']['environment'], 'production')
        self.assertEqual(d['permissions'], original['permissions'])
        self.assertEqual(report['routed_jobs'], ['test', 'deploy'])

    def test_manual_push_path_is_branch_guarded(self):
        d = loader().load(migrate(BASE)[0])
        condition = d['jobs']['deploy']['if']
        self.assertIn("github.event_name == 'workflow_dispatch'", condition)
        self.assertIn("github.ref == 'refs/heads/main'", condition)

    def test_does_not_restrict_existing_manual_behavior(self):
        source = BASE.replace('  push:', '  workflow_dispatch:\n  push:')
        d = loader().load(migrate(source)[0])
        self.assertEqual(d['jobs']['deploy']['if'], "github.event_name == 'push'")

    def test_repeated_migration_is_idempotent(self):
        first, _ = migrate(BASE)
        second, _ = migrate(first)
        self.assertEqual(first, second)

    def test_public_stays_github_hosted(self):
        d = loader().load(migrate(BASE, private=False)[0])
        self.assertEqual(d['jobs']['test']['runs-on'], 'ubuntu-latest')
        self.assertEqual(d['on']['workflow_dispatch']['inputs']['runner_target']['options'], ['auto', 'github'])

    def test_windows_and_arm_are_not_rewritten(self):
        for label in ['windows-latest', 'ubuntu-24.04-arm', 'macos-latest']:
            d = loader().load(migrate(BASE.replace('ubuntu-latest', label))[0])
            self.assertEqual(d['jobs']['test']['runs-on'], label)
            self.assertNotIn('vps', d['on']['workflow_dispatch']['inputs']['runner_target']['options'])

    def test_dynamic_matrix_stays_unchanged_for_review(self):
        source = BASE.replace('ubuntu-latest', '${{ matrix.os }}')
        d = loader().load(migrate(source)[0])
        self.assertEqual(d['jobs']['test']['runs-on'], '${{ matrix.os }}')

    def test_label_arrays_do_not_crash(self):
        source = BASE.replace('ubuntu-latest', '[self-hosted, special]')
        d = loader().load(migrate(source)[0])
        self.assertEqual(d['jobs']['test']['runs-on'], ['self-hosted', 'special'])

    def test_existing_custom_inputs_are_retained(self):
        source = BASE.replace('  push:', '''  workflow_dispatch:
    inputs:
      notes:
        type: string
        required: true
  push:''')
        d = loader().load(migrate(source)[0])
        self.assertTrue(d['on']['workflow_dispatch']['inputs']['notes']['required'])

    def test_conflicting_input_fails_closed(self):
        source = BASE.replace('  push:', '''  workflow_dispatch:
    inputs:
      runner_target:
        type: string
  push:''')
        with self.assertRaises(ValueError): migrate(source)

    def test_glob_branch_needs_explicit_review(self):
        with self.assertRaises(ValueError): migrate(BASE.replace('[main]', '["release/**"]'))

    def test_short_trigger_syntax(self):
        source = 'on: [push, pull_request]\njobs:\n  test:\n    runs-on: ubuntu-latest\n    steps: [{run: "true"}]\n'
        d = loader().load(migrate(source)[0])
        self.assertEqual(set(d['on']), {'push', 'pull_request', 'workflow_dispatch'})

    def test_reusable_definition_receives_inputs(self):
        source = BASE.replace('  push:', '  workflow_call:\n  push:')
        d = loader().load(migrate(source)[0])
        self.assertEqual(d['on']['workflow_call']['inputs']['runner_target']['type'], 'string')
        self.assertTrue(d['on']['workflow_call']['inputs']['allow_fallback']['default'])

    def test_local_reusable_call_forwards_choice(self):
        source = '''on: push
jobs:
  checks:
    uses: ./.github/workflows/ci.yml
    with:
      existing: keep
'''
        d = loader().load(migrate(source)[0])
        self.assertEqual(d['jobs']['checks']['with']['existing'], 'keep')
        self.assertIn('inputs.runner_target', d['jobs']['checks']['with']['runner_target'])

    def test_forks_and_dependabot_guarded(self):
        d = loader().load(migrate(BASE)[0])
        expression = d['jobs']['test']['runs-on']
        self.assertIn("github.event_name != 'pull_request_target'", expression)
        self.assertIn('github.event.pull_request.head.repo.full_name == github.repository', expression)
        self.assertIn("github.actor != 'dependabot[bot]'", expression)


if __name__ == '__main__':
    unittest.main()
