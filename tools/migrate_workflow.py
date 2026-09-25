"""Conservative workflow migration; preserves steps, dependencies and triggers.

Run locally against a clean worktree. This utility never writes to GitHub.
"""
from __future__ import annotations
import argparse
import copy
import io
import re
from pathlib import Path
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap
from control.policy import runner_expression

SUPPORTED = {'ubuntu-latest', 'ubuntu-24.04', 'ubuntu-22.04'}


def loader():
    parser = YAML()
    parser.preserve_quotes = True
    parser.width = 4096
    parser.indent(mapping=2, sequence=4, offset=2)
    return parser


def migrate(text: str, *, private=True) -> tuple[str, dict]:
    parser = loader()
    doc = parser.load(text)
    if not isinstance(doc, dict) or not isinstance(doc.get('jobs'), dict):
        raise ValueError('Not a workflow')
    before = copy.deepcopy(doc)
    events = doc.get('on')
    if isinstance(events, str):
        events = CommentedMap({events: None})
    elif isinstance(events, list):
        events = CommentedMap({event: None for event in events})
    elif not isinstance(events, dict):
        raise ValueError('Unsupported trigger structure')
    doc['on'] = events
    existing_manual = 'workflow_dispatch' in events
    compatible = [key for key, job in doc['jobs'].items()
                  if isinstance(job.get('runs-on'), str)
                  and (job['runs-on'] in SUPPORTED or 'vars.KODIFY_RUNNER_MODE' in job['runs-on'])]
    # A reusable caller without its own runs-on still forwards manual choice.
    local_calls = [key for key, job in doc['jobs'].items()
                   if str(job.get('uses', '')).startswith('./.github/workflows/')]
    choices = ['auto', 'github', 'vps'] if private and (compatible or local_calls) else ['auto', 'github']
    manual = events.get('workflow_dispatch') or CommentedMap()
    events['workflow_dispatch'] = manual
    inputs = manual.setdefault('inputs', CommentedMap())
    definitions = {
        'runner_target': {'description': 'Execution location (auto uses organization quota and runner health)',
                          'type': 'choice', 'required': True, 'default': 'auto', 'options': choices},
        'allow_fallback': {'description': 'Allow GitHub-hosted recovery if VPS infrastructure fails',
                           'type': 'boolean', 'required': True, 'default': True},
    }
    for name, definition in definitions.items():
        if name in inputs and dict(inputs[name]) != definition:
            raise ValueError(f'Existing input conflicts with {name}')
        if name not in inputs:
            inputs[name] = CommentedMap(definition)
    if len(inputs) > 25:
        raise ValueError('workflow_dispatch input limit exceeded')
    if 'workflow_call' in events:
        call = events['workflow_call'] or CommentedMap()
        events['workflow_call'] = call
        call_inputs = call.setdefault('inputs', CommentedMap())
        for name, type_, default in [('runner_target', 'string', 'auto'), ('allow_fallback', 'boolean', True)]:
            definition = {'type': type_, 'required': False, 'default': default}
            if name in call_inputs and dict(call_inputs[name]) != definition:
                raise ValueError('Conflicting reusable input')
            if name not in call_inputs:
                call_inputs[name] = CommentedMap(definition)
    push = events.get('push')
    branch_guard = None
    if not existing_manual and isinstance(push, dict):
        if push.get('branches-ignore') or push.get('tags') or push.get('tags-ignore'):
            raise ValueError('Manual branch semantics need explicit review')
        branches = push.get('branches', [])
        if any(re.search(r'[*?!\[\]]', branch) for branch in branches):
            raise ValueError('Manual glob branch semantics need explicit review')
        if branches:
            refs = ' || '.join("github.ref == 'refs/heads/" + b.replace("'", "''") + "'" for b in branches)
            branch_guard = "github.event_name != 'workflow_dispatch' || (" + refs + ')'
    changed = []
    for key, job in doc['jobs'].items():
        if private and key in compatible:
            if job['runs-on'] in SUPPORTED:
                job['runs-on'] = runner_expression(str(job['runs-on']))
            changed.append(key)
        if key in local_calls:
            params = job.setdefault('with', CommentedMap())
            params['runner_target'] = "${{ inputs.runner_target || 'auto' }}"
            params['allow_fallback'] = "${{ github.event_name != 'workflow_dispatch' || inputs.allow_fallback }}"
        if not existing_manual:
            # Make a newly-added manual trigger run the same push path. Existing
            # manual workflows keep their original if semantics unchanged.
            for target in [job] + job.get('steps', []):
                condition = target.get('if')
                if isinstance(condition, str):
                    target['if'] = condition.replace("github.event_name == 'push'",
                        "(github.event_name == 'push' || github.event_name == 'workflow_dispatch')")
            if branch_guard:
                prior = str(job.get('if', 'true')).strip()
                if prior.startswith('${{') and prior.endswith('}}'):
                    prior = prior[3:-2].strip()
                job['if'] = '${{ (' + prior + ') && (' + branch_guard + ') }}'
    # A deliberately narrow invariant: no executable command, action, token,
    # job dependency, environment, matrix or workflow permission can drift.
    for key, original in before['jobs'].items():
        after = doc['jobs'][key]
        for field in set(original) | set(after):
            if field in {'runs-on', 'if', 'with'}:
                continue
            if field == 'steps':
                left = [{k:v for k,v in step.items() if k != 'if'} for step in original.get('steps', [])]
                right = [{k:v for k,v in step.items() if k != 'if'} for step in after.get('steps', [])]
                if left != right:
                    raise ValueError('Step content invariant violated')
            elif original.get(field) != after.get(field):
                raise ValueError('Job invariant violated')
    for field in set(before) | set(doc):
        if field not in {'on', 'jobs'} and before.get(field) != doc.get(field):
            raise ValueError('Workflow invariant violated')
    buffer = io.StringIO()
    parser.dump(doc, buffer)
    return buffer.getvalue(), {'routed_jobs': changed, 'hosted_only': not changed,
                               'manual_trigger_added': not existing_manual,
                               'reusable_calls': local_calls}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('source', type=Path)
    parser.add_argument('destination', type=Path)
    parser.add_argument('--public', action='store_true')
    args = parser.parse_args()
    output, result = migrate(args.source.read_text(), private=not args.public)
    if args.destination.exists():
        raise SystemExit('Refusing to overwrite an existing destination')
    args.destination.parent.mkdir(parents=True, exist_ok=True)
    args.destination.write_text(output)
    print(result)


if __name__ == '__main__':
    main()
