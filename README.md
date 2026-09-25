# Actions Runner Control

Generic, quota-aware GitHub Actions runner routing and infrastructure recovery.

This repository is public so the independent supervisor can use standard GitHub-hosted runners without consuming a private organization's included compute quota. It contains no application source, private repository inventory, workflow logs, billing reports, or credentials.

## Status

Bootstrap in progress. No organization workflows have been migrated or enabled by this repository yet.

## Security boundaries

- A dedicated GitHub App supplies short-lived installation tokens.
- Application credentials must remain repository Actions secrets; never commit them.
- The supervisor only executes reviewed controller code from the default branch, never code from monitored runs or pull requests.
- Supervision must not execute on the self-hosted machine it monitors.
- Public/untrusted contributions and unsupported operating systems/architectures stay GitHub-hosted.
- Failed tests must not trigger automatic retries. Interrupted deployments and migrations must not be replayed blindly.
- Routing configuration is distributed at repository scope to support GitHub Free organizations.

Implementation, tests and operating instructions will be added before activation.
