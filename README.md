# Actions Runner Control

Generic quota-aware GitHub Actions runner routing and conservative infrastructure recovery. The independent supervisor runs on standard **GitHub-hosted** runners in this public repository. It never depends on the self-hosted machine it monitors.

No application source, private inventory, workflow logs, billing reports or credentials belong in this repository. Supervisor output contains aggregate counters only.

## Status

**Activation is explicitly scoped, not organization-wide by default.** The dedicated App has authenticated successfully from GitHub-hosted supervision. A real offline-runner test recovered queued jobs on GitHub while preserving prior successful work. The isolated runtime has executed checkout, Node/Python setup, Buildx builds and container jobs with PostgreSQL services; consecutive runs and an idle restart verified clean disposable state. Each target repository must still be deliberately enrolled and its workload compatibility checked. This generic repository does not publish private fleet inventory or claim that every repository is enrolled.

## Execution policy

Migrated compatible workflows expose `runner_target` (`auto`, `github`, `vps`) and `allow_fallback` (default true) in **Actions → Run workflow**.

- `auto`: GitHub while included compute remains; VPS near quota exhaustion if a matching runner is online.
- `github`: always the original hosted image.
- `vps`: explicitly select the self-hosted pool. Disable fallback for a strict local test.
- A rerun with fallback allowed uses GitHub. Reruns retain the original event, ref and SHA; changing inputs requires a new manual dispatch.
- Public repositories, fork PRs, Dependabot and `pull_request_target` stay hosted. Public standard runners are already free.
- Windows, ARM64, macOS, special labels and dynamic matrices are not silently translated to Linux x64. Unsupported jobs retain their original runner and require separate capacity or review.

The router is an expression in `runs-on`: there is **no extra billed selector job** in private repositories. Configuration is repository-scoped because GitHub Free does not expose organization variables to private repositories.

The current default entitlement is 2,000 Linux-equivalent minutes with a 100-minute reserve and a baseline price of USD 0.006/minute. Mixed compute SKUs are normalized by their billed gross price; storage is excluded. Any already-paid compute conclusively selects VPS when available. Configure these explicitly if the plan or rates change. Billing data and runner health are eventually consistent; the reserve is not a guarantee of zero overage.

## Safe recovery

The supervisor runs approximately every five minutes. GitHub schedules are best-effort and may be delayed; this is not a hard availability SLA.

- Only labeled, opted-in jobs on their first attempt can be rescued automatically.
- A queue stalled with no online matching runner can be cancelled after a grace period, provided no job is executing or waiting for environment approval.
- The controller records its own cancellation before recovery. Human cancellations are not retry requests.
- `rerun-failed-jobs` resumes cancelled/failed work without replaying successful jobs. A real synthetic test verified two queued jobs and their dependent job resumed on hosted runners, keeping the successful setup job's original execution.
- The controller rechecks cancellation races: if a supposedly queued job actually started, it requests human attention instead of replaying side effects.
- Ordinary code/test failures are not retried.
- Recognized runner communication failures can be retried before work starts. Already-started jobs require an explicit reviewed idempotency allowlist (`KODIFY_RUNNER_SAFE_JOBS`). Deployments and migrations are **not** automatically assumed safe.
- Private run IDs needing review are stored in that repository's `KODIFY_RUNNER_ATTENTION` variable, not printed in public logs.

## Dedicated GitHub App

Create an organization-owned **private App** (separate from this public repository), with webhooks disabled:

Repository permissions:
- Actions: read/write.
- Variables: read/write.
- Checks: read.
- Contents: read.
- Metadata: read (implicit).

Organization permissions:
- Administration: read (billing usage).
- Self-hosted runners: read.

Install it for the organization repositories. Store its PEM only as the repository secret `RUNNER_APP_PRIVATE_KEY`, and its App ID as the repository variable `RUNNER_APP_ID`. Never paste the PEM in an issue, chat, commit or log. The supervisor requests short-lived installation tokens and revokes them on completion; it does not use a personal administrator token.

Control repository secret `RUNNER_REPOSITORIES` contains an explicit comma-separated list of enrolled repository names (without the owner). This keeps private inventory out of public workflow logs. The supervisor refuses mutation without this allowlist and never inspects repositories outside it; the enrollment variable is an additional gate, not a replacement.

Control repository variables:
- `INCLUDED_MINUTES`: entitlement, default `2000`.
- `RESERVE_MINUTES`: safety margin, default `100`.
- `SUPERVISOR_ENABLED`: default absent/off. Set `true` **only after integration validation**.

Target repository variables:
- `KODIFY_RUNNER_MANAGED=1`: explicit enrollment after its workflows are migrated.
- `KODIFY_RUNNER_MODE`: `github` or `vps`, maintained by the supervisor.
- `KODIFY_RUNNER_SAFE_JOBS`: JSON list of reviewed idempotent job names, default `[]`.
- Recovery state variables are internal controller state; do not edit mid-recovery.

Without App configuration, scheduled jobs are skipped. With an App but without activation, the supervisor is read-only. A manually requested `apply=true` is an explicit mutation request.

## Local validation and migration

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m tools.migrate_workflow input.yml output.yml
```

The migration utility operates locally, refuses to overwrite its destination, preserves executable steps/dependencies/matrices/permissions, adds branch guards to newly manual push workflows, and propagates choices to local reusable workflows. Review a clean-worktree diff, compare existing lint failures, and validate the real target branch before committing. Historical feature branches must integrate their updated base; new hardcoded workflows are not magically rewritten by an organization setting.

## Isolated runtime

The units implement a dedicated Unix user, rootless Docker, an isolated data filesystem, a two-core CPU quota, 3,500 MiB memory ceiling and 512 MiB swap ceiling for the entire runtime, including Docker build descendants. The Docker namespace hides home directories and application volumes. Never replace this with a root host Docker socket or an assistant-home mount. Containers still share the host kernel; this is defense in depth, not VM-grade isolation for arbitrary hostile code.

The trusted lifecycle process registers a **one-job JIT runner** and passes its configuration over stdin, not an environment variable or logged command. Existing host CLI authentication remains on the host and is never exported to Actions or the container. Between jobs the service stops the complete dedicated runtime, checks the isolated mount boundary, discards its contents, and restores a root-owned runner image archive. Registration metadata (not credentials) survives lifecycle restarts so stale runners can be removed. The bootstrap user and home are explicit template substitutions; its CLI directory must be present in the systemd PATH.

Host-local and private/VPN/metadata destinations are denied for the isolated Unix UID with nftables (the system DNS stub is the narrow exception). Public egress remains available for GitHub and dependency registries. Rootless port publishing is disabled; the namespace must not expose job services on the VPS. Regenerate the rules after host address changes. Workflows that legitimately require private networking need a separately reviewed runner, not a blanket exception here.

Container jobs share the disposable runner `externals`, work directory and tool cache through identical absolute paths visible to the isolated daemon. The root-owned Docker wrapper translates only the runner's well-known socket volume source to the dedicated rootless socket; it never uses a host root Docker socket. Container-to-service networking is supported and tested. Jobs relying on host-published localhost service ports remain hosted; the migrator must not silently route these jobs to a runtime without that capability.

`infra/runner-data.mount` is a template to install as `var-lib-kodifyci-data.mount`. It expects an already-initialized **regular image file**, not a block device. `infra/runtime-smoke.yml` is a synthetic manual test fixture for a private repository, not a customer deployment. Initialization, namespace isolation checks, resource-limit checks and ephemeral registration must be verified before enrollment. The 8 GiB filesystem budget leaves only several GiB for each build after loading the tools; large Docker/Android builds need separate capacity validation. New image archives require versioned rebuilding and smoke testing. A clean runtime reload adds startup latency between jobs.

## Remaining activation gates

1. Verify dedicated App authentication, billing read, runner read and repository variable writes.
2. Validate capacity against representative application builds; synthetic Linux success is not proof that every production build fits.
3. Verify scheduled execution and its bounded public activity heartbeat (`.github/runner-control-heartbeat`, at most once per 28 days); GitHub schedules remain best-effort.
4. Apply reviewed changes to all intended default/integration branches and verify their deployed contents; keep routing hosted until ready.
5. Exercise automatic quota routing, strict VPS, offline queue recovery, code-failure non-retry and partial-deployment safety.
6. Enable enrollment/supervision, verify the scheduled path and monitor real workload capacity.

Rollback: set each managed repository's `KODIFY_RUNNER_MODE=github` and disable `SUPERVISOR_ENABLED`, then verify the next run is hosted. Strict manual VPS requests intentionally remain strict until the operator selects GitHub.
