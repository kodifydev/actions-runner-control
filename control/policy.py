"""Pure routing/recovery policy. No network, secrets, or repository-specific data."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

RUNNER_LABEL = "kodify-vps"
FALLBACK_LABEL = "kodify-fallback-enabled"
STRICT_LABEL = "kodify-fallback-disabled"


@dataclass(frozen=True)
class Quota:
    used_equivalent_minutes: Decimal
    included_minutes: Decimal
    paid_compute: bool

    def needs_vps(self, reserve_minutes: Decimal) -> bool:
        return self.paid_compute or self.used_equivalent_minutes >= max(
            Decimal(0), self.included_minutes - reserve_minutes
        )


def quota_from_usage(report: dict[str, Any], included_minutes: int,
                     baseline_price: str = "0.006") -> Quota:
    """Use compute SKU costs to normalize mixed-OS use; exclude storage.

    The entitlement and baseline rate are explicit configuration, not a guess
    inferred from discounts or the account's last invoice. Already-paid compute
    is conclusive even when the usage feed is delayed or rates change.
    """
    baseline = Decimal(baseline_price)
    if baseline <= 0 or included_minutes < 0:
        raise ValueError("Invalid quota configuration")
    if not isinstance(report.get("usageItems"), list):
        raise ValueError("Missing usageItems")
    gross = Decimal(0)
    paid = False
    for item in report["usageItems"]:
        if str(item.get("product", "")).lower() != "actions":
            continue
        if str(item.get("unitType", "")).lower() != "minutes":
            continue
        amount = Decimal(str(item["grossAmount"]))
        net = Decimal(str(item["netAmount"]))
        if not amount.is_finite() or not net.is_finite() or amount < 0 or net < 0:
            raise ValueError("Invalid usage amount")
        gross += amount
        paid |= net > 0
    return Quota(gross / baseline, Decimal(included_minutes), paid)


def preferred_runner(quota: Quota | None, runners: list[dict[str, Any]],
                     *, private: bool, reserve_minutes: int = 100) -> str:
    # Public standard runners are already free; unknown billing fails to hosted.
    if not private or quota is None or not quota.needs_vps(Decimal(reserve_minutes)):
        return "github"
    for runner in runners:
        labels = {str(label.get("name", "")).lower() for label in runner.get("labels", [])}
        if runner.get("status") == "online" and RUNNER_LABEL in labels:
            # Busy means capacity is occupied, not broken; preserve the queue.
            return "vps"
    return "github"


def runner_expression(hosted: str = "ubuntu-latest") -> str:
    """An expression-only router incurs no private hosted selector job.

    Explicit manual choice wins on the first attempt. A rerun with fallback
    allowed switches to hosted, preserving the original event and commit SHA.
    A strict manual VPS run remains strict across retries.
    """
    if hosted not in {"ubuntu-latest", "ubuntu-24.04", "ubuntu-22.04"}:
        raise ValueError("Unsupported hosted platform")
    enabled = '["self-hosted","Linux","X64","kodify-vps","kodify-fallback-enabled"]'
    strict = '["self-hosted","Linux","X64","kodify-vps","kodify-fallback-disabled"]'
    return (
        "${{ (github.event_name != 'pull_request_target' "
        "&& github.actor != 'dependabot[bot]' "
        "&& (github.event_name != 'pull_request' || github.event.pull_request.head.repo.full_name == github.repository) "
        "&& (inputs.runner_target == 'vps' || "
        "(inputs.runner_target != 'github' && vars.KODIFY_RUNNER_MODE == 'vps')) "
        "&& (github.run_attempt == 1 || (github.event_name == 'workflow_dispatch' "
        "&& inputs.allow_fallback == false))) "
        "&& fromJSON((github.event_name != 'workflow_dispatch' || "
        "inputs.allow_fallback != false) && '" + enabled + "' || '" + strict + "') "
        "|| '" + hosted + "' }}"
    )


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


INFRA_MESSAGES = (
    "the runner has lost communication with the server",
    "the self-hosted runner lost communication",
    "the runner has received a shutdown signal",
    "the job was not acquired by runner of type self-hosted",
)


def infra_failure(annotations: list[dict[str, Any]]) -> bool:
    return any(
        item.get("annotation_level") == "failure"
        and any(message in str(item.get("message", "")).lower() for message in INFRA_MESSAGES)
        for item in annotations
    )


def job_labels(job: dict[str, Any]) -> set[str]:
    return {str(label).lower() for label in job.get("labels", [])}


def is_managed(job: dict[str, Any]) -> bool:
    return RUNNER_LABEL in job_labels(job)


def permits_fallback(job: dict[str, Any]) -> bool:
    labels = job_labels(job)
    return RUNNER_LABEL in labels and FALLBACK_LABEL in labels and STRICT_LABEL not in labels


def has_executed_steps(job: dict[str, Any]) -> bool:
    return bool(job.get("runner_id")) or any(
        step.get("status") in {"in_progress", "completed"}
        and step.get("conclusion") != "skipped"
        for step in job.get("steps", [])
    )


def recovery_candidate(run: dict[str, Any], jobs: list[dict[str, Any]],
                       annotations_by_job: dict[int, list[dict[str, Any]]],
                       *, now: datetime, runners_online: bool,
                       queue_grace_seconds: int = 300,
                       safe_job_names: frozenset[str] = frozenset()) -> str:
    """Return 'cancel-queued', 'retry-failed', 'manual', or 'none'.

    No arbitrary test retry, strict-VPS override, or replay of side effects.
    Automatic retry is bounded to the first attempt. Cancellation is forbidden
    while a healthy/running job may still be performing external work.
    """
    if int(run.get("run_attempt", 1)) != 1 or not jobs:
        return "none"
    if run.get("conclusion") == "cancelled":
        # A human cancellation must never be treated as a recovery request.
        return "none"
    active = [j for j in jobs if j.get("status") != "completed"]
    queued = [j for j in active if j.get("status") == "queued" and permits_fallback(j)]
    if queued and not runners_online:
        if any(j.get('conclusion') in {'failure', 'timed_out', 'cancelled'} for j in jobs):
            # A queue rescue must not accidentally retry unrelated test failures
            # or jobs a human already cancelled.
            return 'none'
        created = parse_time(run.get("run_started_at") or run.get("created_at"))
        if created is None or (now - created).total_seconds() < queue_grace_seconds:
            return "none"
        if any(j.get("status") == "in_progress" for j in active):
            return "none"
        # All incomplete runnable jobs must opt in. Waiting environments require
        # human approval, not automatic cancellation or a changed execution path.
        if any(j.get("status") not in {"queued", "pending"} for j in active):
            return "none"
        if any(is_managed(j) and not permits_fallback(j) for j in active):
            return "none"
        return "cancel-queued"
    if run.get("status") != "completed" or run.get("conclusion") != "failure":
        return "none"
    failed = [j for j in jobs if j.get("conclusion") in {"failure", "timed_out"}]
    if not failed or not all(permits_fallback(j) for j in failed):
        return "none"
    if not all(infra_failure(annotations_by_job.get(j["id"], [])) for j in failed):
        return "none"
    if any(has_executed_steps(j) and j.get("name") not in safe_job_names for j in failed):
        return "manual"
    return "retry-failed"
