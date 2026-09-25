# ARM64 tests-only edge runner

This is an alternative to the rootless all-purpose runtime, for a trusted private fleet whose edge host also runs protected workloads. It deliberately cannot build images, mount service containers or deploy applications from CI. Release jobs must stay hosted.

## Boundary

The trusted launcher uses the existing host daemon only to launch one fixed, pinned test image. The job gets **no Docker socket, host mounts, devices, extra capabilities, privileged mode or host namespace**. Its private container filesystem and network namespace are disposable. The runner user is non-root with `no-new-privileges`. PostgreSQL 18 and pgvector run inside that same disposable container on loopback with a 5-minute statement timeout. Hosted communication tests launch a matching PostgreSQL container on their own localhost.

The CI-only bridge has targeted iptables forwarding and host-input guards: reject host/private/LAN/VPN/metadata destinations, allow public dependencies. Existing Docker/POS firewall rules, containers, volumes and services are not flushed or restarted. Containers still share the kernel; this is not VM-grade isolation for hostile code.

## Budgets

- One slot enforced by a root-owned launcher flock.
- 2 CPU, low CPU shares, 6 GiB memory, no swap, 512 tasks, elevated OOM score for the test workload.
- pytest automatic workers capped at 2; uv build/install concurrency bounded.
- Job YAML timeout 20 minutes; independent launcher watchdog 30 minutes.
- Refuse new work below 20 GiB free Docker-disk space; terminate only the CI container below 10 GiB.
- Bounded container logs and `--rm`; no persistent workspace or dependency caches shared with jobs.
- Block-I/O weights are deliberately not claimed: some edge kernels do not enforce them.

## Installation and secrets

1. Inventory existing workload health, CPU/memory/disk and kernel capabilities. Do not update the host kernel, Docker daemon, power mode or desktop packages as a side effect.
2. Build this image for native ARM64 with bounded build resources. Pin the resulting local image ID into root-owned `/etc/kodify-tests.json` (`{"image":"sha256:..."}`).
3. Install root-owned `launch.py` and `network.py` into `/opt/kodify-tests`, install/enable the network guard unit. Run `launch.py probe`; verify private TCP denial against a genuinely listening host endpoint, public HTTPS and original workload health.
4. Create a runner group restricted to the explicitly selected private repositories; never expose the group to all organization repos or public forks.
5. Keep durable GitHub authentication on a separate trusted controller. `controller.py` obtains one-job JIT configuration and streams it over verified SSH and container stdin; it never persists a PAT/App key on the edge machine or exposes those credentials to tests. Use a dedicated SSH key stored outside source control.
6. Root-owned controller configuration supplies organization, runner_group_id, ssh_destination, ssh_key and nonsecret state_path. Render `controller.service` placeholders for the actual control account. Persistent state contains runner IDs only. The edge launcher verifies image/config ownership and only runs the configured image.
7. Migrate workflow routing with `tools/tests_only_routing.py`; review all release-step/dependency/secret equality assertions, lint, and manual `tests_only=true` runs before activation. Disable legacy enrollment while changing architectures: an online ARM runner with the compatibility label must not make old X64 workflows select an unavailable pool.

`kodify-vps` is a supervisor compatibility label, not the runner architecture. Workflow tests also require `ARM64` and `kodify-tests`. Unsupported container/service jobs fail the migration utility closed. Runtime/test failures are not hidden with broad automatic retries.

## Verification and cutover

Verify full real suites, not just hello-world. Read GitHub runner identity, skipped release jobs, host resource limits, container mount/capability state and protected workload health during the suite. Verify two independent jobs see fresh files/DBs and different JIT registrations. Verify hosted test fallback separately. Keep PRs and image IDs as rollback evidence.

Before retiring an old runtime, verify there are no active release jobs on it. Stop only its dedicated services and leave its image/archive for rollback. Do not prune unrelated images/volumes. Read back every changed repository/branch and routing variable after cutover; don't call open PRs active configuration.
