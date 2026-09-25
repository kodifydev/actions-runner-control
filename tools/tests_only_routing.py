"""Separate ARM64 test execution from hosted release work; preserve release steps."""
from __future__ import annotations
import copy
import io
from ruamel.yaml.comments import CommentedMap
from tools.migrate_workflow import loader

TEST_RUNNER = "${{ (github.event_name != 'pull_request_target' && github.actor != 'dependabot[bot]' && (github.event_name != 'pull_request' || github.event.pull_request.head.repo.full_name == github.repository) && (inputs.runner_target == 'jetson' || (inputs.runner_target != 'github' && vars.KODIFY_RUNNER_MODE == 'vps')) && (github.run_attempt == 1 || (github.event_name == 'workflow_dispatch' && inputs.allow_fallback == false))) && fromJSON((github.event_name != 'workflow_dispatch' || inputs.allow_fallback != false) && '[\"self-hosted\",\"Linux\",\"ARM64\",\"kodify-tests\",\"kodify-vps\",\"kodify-fallback-enabled\"]' || '[\"self-hosted\",\"Linux\",\"ARM64\",\"kodify-tests\",\"kodify-vps\",\"kodify-fallback-disabled\"]') || 'ubuntu-latest' }}"


def expression(value):
    text = str(value).strip()
    return text[3:-2].strip() if text.startswith('${{') else text


def migrate(text: str) -> str:
    yaml = loader()
    doc = yaml.load(text)
    before = copy.deepcopy(doc)
    jobs = doc['jobs']
    has_tests = 'test' in jobs
    manual = doc.get('on', {}).get('workflow_dispatch')
    if isinstance(manual, dict):
        inputs = manual.setdefault('inputs', CommentedMap())
        if 'runner_target' in inputs:
            inputs['runner_target']['description'] = 'Test location only; builds, migrations and deployments always run on GitHub'
            inputs['runner_target']['options'] = ['auto', 'github', 'jetson'] if has_tests else ['auto', 'github']
        if 'allow_fallback' in inputs:
            inputs['allow_fallback']['description'] = 'Allow GitHub-hosted recovery for unavailable test infrastructure'
        if has_tests:
            inputs['tests_only'] = CommentedMap(description='Run tests only; never migrate or deploy', type='boolean', default=False)
    for key, job in jobs.items():
        if 'runs-on' not in job:
            continue
        if key != 'test':
            # Existing specialized hosted workflows remain exactly where they were.
            if 'vars.KODIFY_RUNNER_MODE' in str(job['runs-on']):
                job['runs-on'] = 'ubuntu-latest'
            if has_tests and isinstance(manual, dict):
                condition = expression(job.get('if', 'true'))
                job['if'] = '${{ inputs.tests_only != true && (' + condition + ') }}'
            continue
        job['runs-on'] = TEST_RUNNER
        job['timeout-minutes'] = 20
        job.setdefault('env', CommentedMap())['PYTEST_XDIST_AUTO_NUM_WORKERS'] = '2'
        if 'permissions' not in job:
            job['permissions'] = CommentedMap(contents='read')
        if 'if' in job and isinstance(manual, dict):
            job['if'] = '${{ (github.event_name == \'workflow_dispatch\' && inputs.tests_only == true) || (' + expression(job['if']) + ') }}'
        if job.get('container') or job.get('services'):
            if job.get('container') != 'ghcr.io/astral-sh/uv:python3.12-alpine' or set(job.get('services', {})) != {'postgres'}:
                raise ValueError('Unsupported test container/services; requires an explicit compatibility review')
        if job.get('container') == 'ghcr.io/astral-sh/uv:python3.12-alpine':
            del job['container']
            service = job.pop('services')['postgres']
            assert service['image'] == 'pgvector/pgvector:pg18-trixie'
            job['env']['PG_HOST'] = '127.0.0.1'
            steps = job['steps']
            setup = [
                CommentedMap(name='Set up Python', uses='actions/setup-python@v5', **{'with': {'python-version': '3.12'}}),
                CommentedMap(name='Install uv', uses='astral-sh/setup-uv@v6', **{'with': {'version': '0.8.22'}}),
                CommentedMap(name='Start isolated PostgreSQL on GitHub', **{'if': "runner.environment == 'github-hosted'", 'run': '''docker run -d --rm --name kodify-ci-postgres -p 127.0.0.1:5432:5432 \
  -e POSTGRES_PASSWORD=postgres pgvector/pgvector:pg18-trixie \
  -c statement_timeout=300000
for attempt in $(seq 1 60); do
  if docker exec kodify-ci-postgres pg_isready -U postgres; then exit 0; fi
  sleep 1
done
exit 1
'''})]
            for step in steps:
                if step.get('name') == 'Verify messaging transactions on PostgreSQL':
                    step['env']['TEST_DATABASE_URL'] = 'postgresql+psycopg://postgres:postgres@127.0.0.1:5432/postgres'
            for position, step in reversed(list(enumerate(setup, start=1))):
                steps.insert(1, step)
            steps.append(CommentedMap(name='Remove GitHub test database', **{'if': "always() && runner.environment == 'github-hosted'", 'run': 'docker rm -f kodify-ci-postgres || true'}))
        # Production credentials/OIDC must never enter the test runner.
        if job.get('permissions', {}).get('id-token') == 'write':
            raise ValueError('Test job must not receive deployment OIDC')
    for key, job in before['jobs'].items():
        if key != 'test':
            for field in ['steps', 'env', 'needs', 'strategy', 'outputs', 'permissions']:
                assert job.get(field) == jobs[key].get(field), (key, field)
    output = io.StringIO()
    yaml.dump(doc, output)
    return output.getvalue()
