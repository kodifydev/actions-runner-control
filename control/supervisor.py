"""Independent fleet supervisor; public output is aggregate counts only."""
from __future__ import annotations

import json
import os
from collections import Counter
from datetime import datetime, timedelta, timezone

from control.api import APIError, GitHub
from control.policy import (
    RUNNER_LABEL, has_executed_steps, permits_fallback, preferred_runner,
    quota_from_usage, recovery_candidate,
)

RECOVERY_VAR = 'KODIFY_RUNNER_RECOVERY'


class Supervisor:
    def __init__(self, api: GitHub, owner: str, *, apply=False, included=2000,
                 reserve=100, now=None, control_repository='actions-runner-control'):
        if not owner or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-' for c in owner):
            raise ValueError('Invalid owner')
        self.api = api
        self.owner = owner
        self.apply = apply
        self.included = included
        self.reserve = reserve
        self.now = now or datetime.now(timezone.utc)
        self.control_repository = control_repository
        self.counts = Counter()

    def set_variable(self, repo, name, value):
        if self.apply:
            self.api.set_variable(repo, name, value)
        else:
            self.counts['planned_variable_writes'] += 1

    def save_state(self, repo, state):
        self.set_variable(repo, RECOVERY_VAR, json.dumps(state, separators=(',', ':')))

    def attempt_recovery(self, repo, entry):
        """Continue only a cancellation recorded by this controller.

        After cancelling a wholly queued tail, rerun-failed-jobs preserves all
        successful jobs and original commit/event context. Pending cancellation
        is revisited next tick; no force-cancel and no speculative rerun.
        """
        run_id = int(entry['run_id'])
        live = self.api.request('GET', f'/repos/{repo}/actions/runs/{run_id}')
        if int(live['run_attempt']) > int(entry['attempt']):
            self.counts['recoveries_verified'] += 1
            return None
        if live['status'] != 'completed':
            return entry
        if live['conclusion'] != 'cancelled':
            # Job won a race and completed; don't replay successful work or tests.
            self.counts['recovery_races_abandoned'] += 1
            return None
        jobs = self.api.pages(f'/repos/{repo}/actions/runs/{run_id}/jobs?filter=latest', 'jobs')
        replayed = [job for job in jobs if job.get('conclusion') in {'failure', 'timed_out', 'cancelled'}]
        if not replayed or any(has_executed_steps(job) for job in replayed):
            # A queued job may have started between preflight and cancellation.
            # Never repeat that side effect on a second machine.
            self.counts['manual_intervention'] += 1
            if self.apply:
                self.api.set_variable(repo, 'KODIFY_RUNNER_ATTENTION', str(run_id))
            return None
        self.counts['queued_retries'] += 1
        if not self.apply:
            return entry
        self.api.request('POST', f'/repos/{repo}/actions/runs/{run_id}/rerun-failed-jobs')
        check = self.api.request('GET', f'/repos/{repo}/actions/runs/{run_id}')
        if int(check['run_attempt']) > int(entry['attempt']):
            self.counts['recoveries_verified'] += 1
            return None
        # Eventual consistency: don't repeat a successful POST on the next tick.
        entry['stage'] = 'rerun-submitted'
        return entry

    def read_annotations(self, repo, jobs):
        result = {}
        for job in jobs:
            if job.get('conclusion') not in {'failure', 'timed_out'} or not permits_fallback(job):
                continue
            # check_run_url is data from GitHub; accept an exact expected prefix.
            url = job.get('check_run_url', '')
            prefix = f'https://api.github.com/repos/{repo}/check-runs/'
            if not url.startswith(prefix) or not url[len(prefix):].isdigit():
                continue
            check_id = url[len(prefix):]
            result[job['id']] = self.api.pages(f'/repos/{repo}/check-runs/{check_id}/annotations')
        return result

    def inspect_repository(self, repository, quota, runners):
        repo = repository['full_name']
        if repository.get('archived') or repository.get('name') == self.control_repository:
            return
        variables = self.api.variables(repo)
        if variables.get('KODIFY_RUNNER_MANAGED') != '1':
            self.counts['unenrolled_repositories'] += 1
            return
        self.counts['managed_repositories'] += 1
        preferred = preferred_runner(quota, runners, private=repository['private'], reserve_minutes=self.reserve)
        if variables.get('KODIFY_RUNNER_MODE') != preferred:
            self.set_variable(repo, 'KODIFY_RUNNER_MODE', preferred)
            self.counts['routing_changes'] += 1
        online = any(r.get('status') == 'online' and RUNNER_LABEL in {
            str(x['name']).lower() for x in r.get('labels', [])} for r in runners)
        state = json.loads(variables.get(RECOVERY_VAR, '[]'))
        remaining = []
        for entry in state:
            if entry.get('stage') == 'rerun-submitted':
                live = self.api.request('GET', f'/repos/{repo}/actions/runs/{int(entry["run_id"])}')
                if int(live['run_attempt']) <= int(entry['attempt']):
                    self.counts['manual_intervention'] += 1
                    remaining.append(entry)
                else:
                    self.counts['recoveries_verified'] += 1
                continue
            updated = self.attempt_recovery(repo, entry)
            if updated:
                remaining.append(updated)
        if state != remaining:
            self.save_state(repo, remaining)
        known = {int(x['run_id']) for x in remaining}
        runs = {}
        for status in ['queued', 'in_progress', 'waiting', 'pending']:
            for run in self.api.pages(f'/repos/{repo}/actions/runs?status={status}', 'workflow_runs'):
                runs[run['id']] = run
        since = (self.now - timedelta(hours=2)).strftime('%Y-%m-%dT%H:%M:%SZ')
        for run in self.api.pages(f'/repos/{repo}/actions/runs?status=failure&created=%3E%3D{since}', 'workflow_runs'):
            runs[run['id']] = run
        safe_names = frozenset(json.loads(variables.get('KODIFY_RUNNER_SAFE_JOBS', '[]')))
        for run in runs.values():
            if run['id'] in known or int(run.get('run_attempt', 1)) != 1:
                continue
            jobs = self.api.pages(f'/repos/{repo}/actions/runs/{run["id"]}/jobs?filter=latest', 'jobs')
            annotations = self.read_annotations(repo, jobs) if run.get('conclusion') == 'failure' else {}
            decision = recovery_candidate(run, jobs, annotations, now=self.now,
                                          runners_online=online, safe_job_names=safe_names)
            self.counts[decision] += 1
            if decision == 'manual':
                # Private metadata remains in the private repository, not public logs.
                if self.apply:
                    self.api.set_variable(repo, 'KODIFY_RUNNER_ATTENTION', str(run['id']))
            elif decision == 'cancel-queued' and self.apply:
                # Re-fetch immediately before cancellation to narrow a dispatch race.
                latest_jobs = self.api.pages(f'/repos/{repo}/actions/runs/{run["id"]}/jobs?filter=latest', 'jobs')
                latest = self.api.request('GET', f'/repos/{repo}/actions/runs/{run["id"]}')
                if recovery_candidate(latest, latest_jobs, {}, now=self.now,
                                      runners_online=online) != 'cancel-queued':
                    continue
                entry = {'run_id': run['id'], 'attempt': run['run_attempt'], 'stage': 'cancel-requested'}
                remaining.append(entry)
                self.save_state(repo, remaining)
                self.api.request('POST', f'/repos/{repo}/actions/runs/{run["id"]}/cancel')
                # Read back the exact run; the cancellation can finish next tick.
                self.api.request('GET', f'/repos/{repo}/actions/runs/{run["id"]}')
            elif decision == 'retry-failed' and self.apply:
                self.api.request('POST', f'/repos/{repo}/actions/runs/{run["id"]}/rerun-failed-jobs')
                check = self.api.request('GET', f'/repos/{repo}/actions/runs/{run["id"]}')
                if int(check['run_attempt']) > int(run['run_attempt']):
                    self.counts['recoveries_verified'] += 1

    def tick(self):
        try:
            report = self.api.request('GET', f'/organizations/{self.owner}/settings/billing/usage/summary'
                f'?product=Actions&year={self.now.year}&month={self.now.month}')
            quota = quota_from_usage(report, self.included)
        except (APIError, ValueError, KeyError):
            quota = None
            self.counts['billing_unavailable'] += 1
        runners = self.api.pages(f'/orgs/{self.owner}/actions/runners', 'runners')
        repositories = self.api.pages('/installation/repositories', 'repositories')
        for repo in repositories:
            if repo.get('owner', {}).get('login', '').lower() != self.owner.lower():
                continue
            try:
                self.inspect_repository(repo, quota, runners)
            except (APIError, ValueError, KeyError, TypeError, RuntimeError):
                self.counts['repository_errors'] += 1
        return dict(self.counts)


def main():
    api = GitHub(os.environ['GH_TOKEN'])
    supervisor = Supervisor(api, os.environ['RUNNER_OWNER'],
        apply=os.environ.get('SUPERVISOR_APPLY') == 'true',
        included=int(os.environ.get('INCLUDED_MINUTES', '2000')),
        reserve=int(os.environ.get('RESERVE_MINUTES', '100')))
    try:
        counts = supervisor.tick()
    except (APIError, ValueError, KeyError, TypeError, RuntimeError):
        print('Supervisor failed safely; no private API response is printed.')
        return 1
    print(json.dumps(counts, sort_keys=True))
    if path := os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(path, 'a') as file:
            file.write('## Runner control\n\nAggregate counts only; private targets remain private.\n\n')
            for key, value in sorted(counts.items()):
                file.write(f'- {key}: {value}\n')
    return 1 if counts.get('repository_errors') or counts.get('billing_unavailable') else 0


if __name__ == '__main__':
    raise SystemExit(main())
