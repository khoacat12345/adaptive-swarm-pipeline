#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""M3 execution engine: isolated apply, review, repair and checkpoint.

The engine is intentionally provider-agnostic. Tests inject deterministic coder
and reviewer backends. A production adapter can wrap the existing OpenCode
workers later; the safety rules stay in this module.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from . import isolation
from .attempts import VERIFYING, WORKING, AttemptHistory
from .budget import BudgetConfig, BudgetController
from .capability_runtime import build_context as build_capability_context
from .errors import (
    BudgetBlocked,
    BudgetError,
    CrossFamilyError,
    ExecutionError,
    NoProgressError,
    ReviewerUnavailable,
    SecurityVeto,
    TestIntegrityError,
)

from .plan import plan_hash
from .runner import V2Runner

TEST_NAME_RE = re.compile(r"(^test_.*\.py$|.*_test\.py$|^conftest\.py$)", re.IGNORECASE)
SECRET_RE = re.compile(
    r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"
    r"|sk-[A-Za-z0-9]{20,}"
    r"|AKIA[0-9A-Z]{16}"
    r"|ghp_[A-Za-z0-9]{20,}"
    r"|xox[baprs]-[A-Za-z0-9-]{10,}",
    re.IGNORECASE,
)
REVIEW_ROLES = ("security", "qa", "perf", "docs")
TIER_ORDER = ("E1", "E2", "E3", "E4")
TIER_REVIEWERS: dict[str, tuple[str, ...]] = {
    "E1": (),
    "E2": ("security",),
    "E3": REVIEW_ROLES,
    "E4": REVIEW_ROLES,
}
TIER_PLANNERS = {"E1": "P1", "E2": "P1", "E3": "P2", "E4": "P3"}
SENSITIVE_SEGMENT_RE = re.compile(
    r"^(?:"
    r"\.env(?:\..*)?"
    r"|\.npmrc"
    r"|\.pypirc"
    r"|\.netrc"
    r"|\.ssh"
    r"|\.gnupg"
    r"|secrets?(?:\..*)?"
    r"|tokens?(?:\..*)?"
    r"|credentials?(?:\..*)?"
    r"|passwords?(?:\..*)?"
    r"|passwd"
    r"|id_(?:rsa|ed25519|ecdsa)"
    r"|[^/]*\.(?:pem|key|p12|pfx|crt|cer|jks|keystore|gpg)"
    r"|auth(?:z|entication|orization)?(?:\..*)?"
    r"|security(?:\..*)?"
    r"|permissions?(?:\..*)?"
    r"|crypto(?:\..*)?"
    r"|payments?(?:\..*)?"
    r")$",
    re.IGNORECASE,
)


class CoderBackend(Protocol):
    def propose(
        self,
        work_order: Mapping[str, Any],
        *,
        cwd: Path,
        round_index: int,
        feedback: str | None,
    ) -> dict[str, str]:
        ...


class ReviewBackend(Protocol):
    def review(
        self,
        role: str,
        *,
        diff: str,
        work_order: Mapping[str, Any],
        phase: Mapping[str, Any],
    ) -> dict[str, Any]:
        ...


@dataclass(frozen=True)
class ExecutionSeat:
    role: str
    model: str
    family: str
    backend: Any
    is_free: bool = True
    cost_usd: float = 0.0


@dataclass
class ReviewFinding:
    role: str
    verdict: str
    severity: str
    evidence: list[str] = field(default_factory=list)
    required_change: str = ""

    @classmethod
    def from_payload(cls, role: str, payload: Mapping[str, Any]) -> "ReviewFinding":
        verdict = str(payload.get("verdict") or "abstain").lower()
        if verdict not in {"approve", "reject", "abstain"}:
            verdict = "reject"
        severity = str(payload.get("severity") or "medium").lower()
        if severity not in {"blocker", "high", "medium", "low"}:
            severity = "high"
        return cls(
            role=role,
            verdict=verdict,
            severity=severity,
            evidence=[str(item) for item in (payload.get("evidence") or [])],
            required_change=str(payload.get("required_change") or ""),
        )


@dataclass(frozen=True)
class TierProfile:
    """Runtime review policy for one execution tier.

    The policy is intentionally small and explicit: E1 has no independent
    reviewer, E2 has one security reviewer, E3 has the four-role panel and E4
    uses the same panel through the blackboard meeting protocol.
    """

    tier: str
    reviewer_roles: tuple[str, ...]
    planner: str
    meeting: bool
    low_risk_only: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "tier": self.tier,
            "reviewer_roles": list(self.reviewer_roles),
            "planner": self.planner,
            "meeting": self.meeting,
            "low_risk_only": self.low_risk_only,
        }


def normalize_tier(value: Any, *, default: str = "E3") -> str:
    tier = str(value or default).strip().upper()
    if tier not in TIER_ORDER:
        raise ExecutionError(f"unknown execution tier: {value!r}")
    return tier


def signed_tier(plan: Mapping[str, Any]) -> str:
    """The tier recorded in the signed plan, defaulting to the E3 wave."""
    return normalize_tier(plan.get("execution_tier") or plan.get("tier") or "E3")


def tier_profile(tier: str) -> TierProfile:
    tier = normalize_tier(tier)
    return TierProfile(
        tier=tier,
        reviewer_roles=TIER_REVIEWERS[tier],
        planner=TIER_PLANNERS[tier],
        meeting=tier == "E4",
        low_risk_only=tier == "E1",
    )


def tier_reviewer_roles(tier: str) -> tuple[str, ...]:
    return tier_profile(tier).reviewer_roles


def is_sensitive_path(relative: str) -> bool:
    """True when a target path belongs to a security-sensitive file class."""
    text = str(relative or "").replace("\\", "/").strip("/")
    if not text:
        return False
    if text == ".agents/config.json":
        return True
    return any(SENSITIVE_SEGMENT_RE.match(part) for part in text.split("/"))


def sensitive_targets(plan: Mapping[str, Any]) -> list[tuple[str, str, str]]:
    found: list[tuple[str, str, str]] = []
    for phase in plan.get("phases") or []:
        phase_id = str(phase.get("id") or "")
        for work_order in phase.get("work_orders") or []:
            work_id = str(work_order.get("id") or "work")
            for relative in work_order.get("target_files") or []:
                if is_sensitive_path(str(relative)):
                    found.append((phase_id, work_id, str(relative)))
    return found


def resolve_tier_profile(
    plan: Mapping[str, Any],
    *,
    tier: str | None = None,
    low_risk_opt_in: bool = False,
) -> TierProfile:
    """Resolve and enforce the tier policy for a signed plan.

    Old plans without a tier default to E3 so their existing four-reviewer
    behavior never changes silently. E1 is strictly opt-in and may not touch a
    sensitive path; that decision is made before the coder is called.
    """
    profile = tier_profile(tier or signed_tier(plan))
    if profile.low_risk_only:
        plan_opt_in = plan.get("low_risk_opt_in") is True
        if not plan_opt_in and not low_risk_opt_in:
            raise ExecutionError(
                "E1 requires explicit low-risk opt-in: pass --e1-opt-in or set "
                "low_risk_opt_in=true in the signed plan"
            )
        blocked = sensitive_targets(plan)
        if blocked:
            detail = ", ".join(
                f"{phase}/{work}:{path}" for phase, work, path in blocked[:8]
            )
            raise ExecutionError(f"E1 low-risk policy blocks sensitive target(s): {detail}")
        for phase in plan.get("phases") or []:
            for work_order in phase.get("work_orders") or []:
                if not str(work_order.get("verify_command") or "").strip():
                    raise ExecutionError(
                        "E1 requires a verify_command for every work order"
                    )
    return profile


def assert_tier_matches_signoff(
    plan: Mapping[str, Any],
    profile: TierProfile,
    *,
    requested_tier: str | None,
) -> None:
    """Fail closed when a runtime tier override bypasses the signed estimate.

    The Usage approval is hash-bound to the plan, and the plan's tier decides
    the reviewer count in that estimate. Allowing ``--tier`` to select a
    different tier afterwards would run seats the user never approved, so the
    override must re-plan and re-sign instead.
    """
    if requested_tier is None:
        return
    signed = signed_tier(plan)
    requested = normalize_tier(requested_tier)
    if requested != signed:
        raise ExecutionError(
            f"runtime tier {requested} differs from the signed plan tier {signed}; "
            "re-plan and re-approve the Usage estimate instead of overriding "
            "the reviewed tier"
        )


def topo_phases(plan: Mapping[str, Any]) -> list[dict[str, Any]]:
    phases = {str(item["id"]): dict(item) for item in plan.get("phases") or []}
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    visiting: set[str] = set()

    def visit(phase_id: str) -> None:
        if phase_id in seen:
            return
        if phase_id in visiting:
            raise ExecutionError(f"dependency cycle at {phase_id}")
        visiting.add(phase_id)
        phase = phases[phase_id]
        for dep in phase.get("depends_on") or []:
            if dep in phases:
                visit(str(dep))
        visiting.remove(phase_id)
        seen.add(phase_id)
        result.append(phase)

    for phase_id in phases:
        visit(phase_id)
    return result


def model_family(model: str) -> str:
    """Best-effort family from the local router table, then provider prefix."""
    try:
        from router import MODEL_FAMILIES  # type: ignore

        if model in MODEL_FAMILIES:
            return str(MODEL_FAMILIES[model])
    except Exception:
        pass
    name = str(model or "").lower()
    for family in ("deepseek", "glm", "qwen", "kimi", "claude", "gemini", "gpt", "nemotron", "mimo"):
        if family in name:
            return family
    if "/" in name:
        return name.split("/", 1)[0]
    return "unknown"


def _is_test_path(relative: str) -> bool:
    path = str(relative).replace("\\", "/").lower()
    name = path.rsplit("/", 1)[-1]
    return "/tests/" in f"/{path}" or bool(TEST_NAME_RE.match(name))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _diff(old: str, new: str, relative: str) -> str:
    import difflib

    return "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=f"a/{relative}",
            tofile=f"b/{relative}",
        )
    )


def _quota_evidence(store: Any) -> dict[str, Any]:
    """Latest quota snapshot recorded by the runner, if any."""
    try:
        for event in reversed(store.read_events()):
            if event.get("event_type") == "quota.snapshot":
                data = event.get("data")
                return dict(data) if isinstance(data, Mapping) else {}
    except Exception:  # noqa: BLE001 - evidence only
        pass
    return {}


@dataclass
class ExecutionResult:
    run_id: str
    status: str
    phases: list[dict[str, Any]]
    checkpoints: list[dict[str, Any]]
    requests: int
    repairs: int
    handoff: dict[str, Any] | None = None
    tier: str = "E3"
    planner: str = "P2"
    reviewer_roles: tuple[str, ...] = REVIEW_ROLES
    low_risk_opt_in: bool = False
    attempts: list[dict[str, Any]] = field(default_factory=list)
    graph: dict[str, Any] = field(default_factory=dict)
    quota: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "tier": self.tier,
            "planner": self.planner,
            "reviewer_roles": list(self.reviewer_roles),
            "low_risk_opt_in": self.low_risk_opt_in,
            "phases": self.phases,
            "checkpoints": self.checkpoints,
            "requests": self.requests,
            "repairs": self.repairs,
            "handoff": self.handoff,
            "attempts": self.attempts,
            "graph": self.graph,
            "quota": self.quota,
        }


class ExecutionEngine:
    def __init__(
        self,
        runner: V2Runner,
        *,
        max_repair_attempts: int = 3,
        max_no_progress: int = 2,
        verify_timeout: int = 300,
    ):
        self.runner = runner
        self.max_repair_attempts = max(0, int(max_repair_attempts))
        self.max_no_progress = max(1, int(max_no_progress))
        self.verify_timeout = int(verify_timeout)

    # ------------------------------------------------------------------
    # Guards
    # ------------------------------------------------------------------

    def _load(self, run_id: str) -> tuple[Any, dict[str, Any], dict[str, Any], isolation.Workspace]:
        store = self.runner.load(run_id)
        self.runner.assert_signed(run_id)
        plan_path = store.run_dir / "plan.json"
        if not plan_path.exists():
            raise ExecutionError(f"run has no plan: {run_id}")
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        workspace = self.runner.workspace(run_id)
        if plan_hash(plan) != store.load().get("plan_hash"):
            raise ExecutionError("plan hash changed after signoff")
        return store, plan, plan, workspace

    def _budget(self, store: Any, plan: Mapping[str, Any]) -> BudgetController:
        path = store.budget_path
        if not path.exists():
            raise ExecutionError("run has no budget state")
        payload = json.loads(path.read_text(encoding="utf-8"))
        config = BudgetConfig.from_dict(payload.get("config") or {})
        return BudgetController(store.run_dir, config)

    def _snapshot_tests(self, worktree: Path, plan: Mapping[str, Any]) -> dict[str, str]:
        hashes: dict[str, str] = {}
        for phase in plan.get("phases") or []:
            for work_order in phase.get("work_orders") or []:
                for relative in work_order.get("target_files") or []:
                    if _is_test_path(relative):
                        target = (worktree / relative).resolve()
                        if target.is_file() and isolation._is_within(target, worktree):
                            hashes[relative] = _sha256_file(target)
        # Also freeze conventional test directories even if not targets.
        for path in worktree.rglob("*"):
            if not path.is_file() or not isolation._is_within(path, worktree):
                continue
            relative = path.relative_to(worktree).as_posix()
            if _is_test_path(relative):
                hashes.setdefault(relative, _sha256_file(path))
        return hashes

    def _check_test_integrity(
        self,
        worktree: Path,
        files: Mapping[str, str],
        test_hashes: Mapping[str, str],
    ) -> None:
        for relative in files:
            if _is_test_path(relative):
                raise TestIntegrityError(f"coder may not modify test file: {relative}")
            if SECRET_RE.search(str(files[relative])):
                raise TestIntegrityError(f"secret-like content in {relative}")
        for relative, expected in test_hashes.items():
            target = (worktree / relative).resolve()
            if target.is_file() and _sha256_file(target) != expected:
                raise TestIntegrityError(f"test file changed during run: {relative}")

    # ------------------------------------------------------------------
    # Apply and verify
    # ------------------------------------------------------------------

    def _apply(
        self,
        worktree: Path,
        work_order: Mapping[str, Any],
        files: Mapping[str, str],
        test_hashes: Mapping[str, str],
    ) -> tuple[Any, dict[str, Any], str, dict[str, str]]:
        targets = list(work_order.get("target_files") or [])
        if set(files) - set(targets):
            raise TestIntegrityError("coder touched files outside the work order")
        self._check_test_integrity(worktree, files, test_hashes)
        old_files: dict[str, str] = {}
        diff_parts: list[str] = []
        for relative, content in files.items():
            target = (worktree / relative).resolve()
            if not isolation._is_within(target, worktree):
                raise ExecutionError(f"path escapes worktree: {relative}")
            old = target.read_text(encoding="utf-8", errors="replace") if target.exists() else ""
            old_files[relative] = old
            diff_parts.append(_diff(old, str(content), relative))
        from .aci_context import validate_proposed_files
        from .adaptive_flags import load_flags

        aci_mode = load_flags().get("ACI")
        if aci_mode in {"shadow", "on"}:
            validation = validate_proposed_files(
                worktree, old_files, files, work_order, test_hashes=test_hashes
            )
            if aci_mode == "on" and not validation.ok:
                raise ExecutionError("ACI validation failed: " + "; ".join(validation.issues[:8]))

        import safe_writer  # type: ignore

        tx = safe_writer.Transaction(
            root=worktree,
            log=False,
            reason=f"v2-m3:{work_order.get('id', 'work')}",
        )
        try:
            targets_pairs = [(relative, worktree / relative) for relative in files]
            tx.begin(targets_pairs)
            for relative, content in files.items():
                tx.write(relative, str(content), encoding="utf-8")
            verify = tx.verify(
                str(work_order.get("verify_command") or ""),
                cwd=worktree,
                timeout=self.verify_timeout,
            )
        except Exception:
            if getattr(tx, "state", "") == "open":
                try:
                    tx.rollback("M3 apply failed")
                except Exception:
                    pass
            raise
        if verify.get("exit_code") != 0:
            tx.rollback("M3 verify failed")
            return None, verify, "".join(diff_parts), dict(files)
        return tx, verify, "".join(diff_parts), dict(files)

    # ------------------------------------------------------------------
    # Budget helpers
    # ------------------------------------------------------------------

    def _call(
        self,
        budget: BudgetController,
        phase_id: str,
        seat: ExecutionSeat,
        fn: Callable[[], Any],
        *,
        repair: bool = False,
    ) -> Any:
        if not seat.is_free and seat.cost_usd <= 0:
            raise BudgetBlocked(
                f"paid model requires an explicit cost estimate: {seat.model}"
            )
        cost = max(0.0, float(seat.cost_usd))
        reservation = budget.reserve(
            phase_id,
            role=seat.role,
            model=seat.model,
            cost_usd=cost,
            repair=repair,
        )
        settled = False
        try:
            if seat.is_free:
                result = fn()
                budget.settle(reservation, requests=1, cost_usd=0.0,
                              cost_confidence="high")
                settled = True
                return result
            import spend_reconcile
            before = spend_reconcile.snapshot_from_quota(self.runner.root)
            if before.source == "unavailable":
                raise BudgetBlocked(
                    f"paid model requires an actual usage source: {seat.model}"
                )
            result = fn()
            after = spend_reconcile.snapshot_from_quota(self.runner.root)
            reconciled = spend_reconcile.reconcile(
                before, after, estimated_cost_usd=cost,
                max_cost_usd=max(cost, float(seat.cost_usd)),
            )
            actual_cost = reconciled.actual_cost_usd
            if reconciled.over_budget:
                budget.stop(
                    f"actual spend exceeded reservation for {seat.model}: "
                    f"{actual_cost} > {cost}"
                )
            budget.settle(
                reservation, requests=1, cost_usd=actual_cost,
                cost_confidence="high",
            )
            settled = True
            return result
        except Exception:
            if not settled:
                try:
                    budget.settle(reservation, requests=1, cost_usd=cost)
                except Exception:
                    pass
            raise

    # ------------------------------------------------------------------
    # Review
    # ------------------------------------------------------------------

    def _review(
        self,
        budget: BudgetController,
        phase_id: str,
        phase: Mapping[str, Any],
        work_order: Mapping[str, Any],
        diff: str,
        reviewers: Mapping[str, ExecutionSeat],
        coder_family: str,
        roles: tuple[str, ...] = REVIEW_ROLES,
    ) -> list[ReviewFinding]:
        findings: list[ReviewFinding] = []
        for role in roles:
            seat = reviewers.get(role)
            if seat is None:
                raise ExecutionError(f"missing reviewer seat: {role}")
            if seat.family == coder_family:
                raise CrossFamilyError(
                    f"reviewer {role} family {seat.family} equals coder family {coder_family}"
                )
            payload = self._call(
                budget,
                phase_id,
                seat,
                lambda seat=seat: seat.backend.review(
                    role, diff=diff, work_order=work_order, phase=phase
                ),
            )
            finding = ReviewFinding.from_payload(role, payload or {})
            findings.append(finding)
            if role == "security" and finding.verdict == "reject" and finding.severity == "blocker":
                raise SecurityVeto(
                    "security blocker: " + "; ".join(finding.evidence)[:600]
                )
            if finding.verdict == "reject" and finding.severity in {"blocker", "high"}:
                raise ExecutionError(
                    f"{role} rejected: {finding.required_change or '; '.join(finding.evidence)[:300]}"
                )
        return findings

    def _review_meeting(
        self,
        store: Any,
        budget: BudgetController,
        phase_id: str,
        phase: Mapping[str, Any],
        work_order: Mapping[str, Any],
        diff: str,
        reviewers: Mapping[str, ExecutionSeat],
        coder_family: str,
        verify: Mapping[str, Any],
    ) -> list[ReviewFinding]:
        """E4: run the v1 blackboard meeting protocol through v2 seats."""
        from types import SimpleNamespace

        from core.meeting import MeetingContext, MeetingOrchestrator

        seating: dict[str, Any] = {}
        engine = self

        class MeetingSeat:
            def __init__(self, seat: ExecutionSeat):
                self.seat = seat

            def review(self, order, *, diff, new_content="", old_content="",
                       level=1, sink=None):
                if self.seat.family == coder_family:
                    raise CrossFamilyError(
                        f"reviewer {self.seat.role} family equals coder family"
                    )
                payload = engine._call(
                    budget, phase_id, self.seat,
                    lambda: self.seat.backend.review(
                        self.seat.role,
                        diff=diff,
                        work_order=dict(work_order),
                        phase=dict(phase),
                    ),
                ) or {}
                verdict = str(payload.get("verdict") or "error").lower()
                reasons = [str(item) for item in (payload.get("evidence") or
                                                   payload.get("reasons") or [])]
                if verdict == "error":
                    raise ReviewerUnavailable(
                        f"reviewer {self.seat.role} infrastructure failure"
                    )
                if verdict not in {"approve", "reject", "abstain"}:
                    verdict = "reject"
                return {
                    "verdict": verdict,
                    "reasons": reasons,
                    "evidence_refs": [],
                    "model": self.seat.model,
                }

        for role, seat in reviewers.items():
            if role not in REVIEW_ROLES:
                continue
            if seat.family == coder_family:
                raise CrossFamilyError(
                    f"reviewer {role} family equals coder family {coder_family}"
                )
            seating[role] = MeetingSeat(seat)

        def sink(event_type: str, data: dict, status: str = "info") -> None:
            try:
                store.append_event(event_type, data)
            except Exception:
                pass

        order = SimpleNamespace(
            task_id=str(work_order.get("id") or "work"),
            objective=str(work_order.get("objective") or ""),
            level=int(work_order.get("level", 2) or 2),
            run_id=str(store.run_id),
        )
        orchestrator = MeetingOrchestrator(
            seating, event_sink=sink, run_id=str(store.run_id),
        )
        context = MeetingContext(
            work_order=order,
            brief=str(work_order.get("objective") or ""),
            diff=diff,
            new_content="",
            old_content="",
            level=order.level,
            verify_result={"exit_code": int(verify.get("exit_code", 0) or 0)},
        )
        orchestrator.open(order, context)
        round1 = orchestrator.run_round1(order, context, sink=sink)
        if round1.unavailable:
            raise ReviewerUnavailable(
                "meeting reviewer unavailable: " + ", ".join(round1.unavailable)
            )
        if not round1.all_approved:
            participants = orchestrator.participants_for_rebuttal(round1.conflicts)
            if participants:
                round2 = orchestrator.run_round2(
                    order, context, participants, sink=sink,
                )
                if not round2.all_approved:
                    raise ExecutionError(
                        "meeting rejected after Round 2: "
                        + "; ".join(
                            reason for finding in round2.findings
                            for reason in (finding.get("reasons") or [])
                        )[:500]
                    )
                round1 = round2
            else:
                raise ExecutionError("meeting rejected without a rebuttal participant")

        findings: list[ReviewFinding] = []
        for item in round1.findings:
            role = str(item.get("role") or "")
            verdict = str(item.get("verdict") or "reject").lower()
            reasons = [str(value) for value in (item.get("reasons") or [])]
            if verdict == "reject" and role == "security":
                raise SecurityVeto("meeting security veto: " + "; ".join(reasons)[:500])
            severity = "blocker" if role == "security" else "high"
            findings.append(ReviewFinding(
                role=role,
                verdict=verdict,
                severity=severity if verdict == "reject" else "low",
                evidence=reasons,
                required_change=reasons[0] if reasons else "",
            ))
        return findings

    # ------------------------------------------------------------------
    # F02/F11 evidence helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _begin_attempt(
        history: AttemptHistory,
        task_id: str,
        attempt: int,
        seat: ExecutionSeat,
        family: str,
        worktree: Path,
        work_order: Mapping[str, Any],
        *,
        run_id: str = "",
        phase_id: str = "",
    ) -> Any:
        """Open a durable attempt (DISPATCHED). Never breaks the run."""
        try:
            spec = json.dumps(dict(work_order), sort_keys=True, ensure_ascii=False)
            return history.start(
                task_id,
                run_id=run_id,
                phase_id=phase_id,
                worker=seat.role or "coder",
                model=seat.model,
                number=attempt,
                metadata={
                    "level": int(work_order.get("level", 1) or 1),
                    "family": family,
                    "worktree": str(worktree),
                    "spec_hash": hashlib.sha256(spec.encode("utf-8")).hexdigest()[:16],
                },
            )
        except Exception:  # noqa: BLE001 - evidence is best effort
            return None

    @staticmethod
    def _mark_working(history: AttemptHistory, record: Any) -> None:
        if record is None:
            return
        try:
            if record.state == "DISPATCHED":
                history.transition(record.attempt_id, WORKING, reason="propose")
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _finish_attempt(
        history: AttemptHistory,
        record: Any,
        *,
        ok: bool,
        verified: bool = False,
        error: Any = None,
    ) -> None:
        if record is None:
            return
        try:
            if verified and record.state == WORKING:
                history.transition(record.attempt_id, VERIFYING, reason="verify")
            if ok:
                history.settle(record.attempt_id, reason="verified")
            else:
                history.fail(record.attempt_id, reason=str(error or "failed")[:1500])
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _record_verify(
        store: Any,
        *,
        phase_id: str,
        work_id: str,
        work_order: Mapping[str, Any],
        verify: Mapping[str, Any],
    ) -> None:
        try:
            exit_code = verify.get("exit_code")
            store.append_event("task.verify", {
                "task_id": work_id,
                "phase_id": phase_id,
                "command": str(work_order.get("verify_command") or ""),
                "exit_code": exit_code,
                "output_tail": str(
                    verify.get("output_tail") or verify.get("stderr") or ""
                )[-2000:],
                "status": "verified" if exit_code == 0 else "failed",
            })
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _record_reviews(store: Any, *, phase_id: str, work_id: str,
                        findings: list[ReviewFinding]) -> None:
        try:
            store.append_event("task.reviews", {
                "task_id": work_id,
                "phase_id": phase_id,
                "reviews": [dict(finding.__dict__) for finding in findings],
            })
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------
    # Public execution
    # ------------------------------------------------------------------

    def _record_lesson(
        self,
        *,
        kind: str,
        error: str,
        work_order: Mapping[str, Any],
        phase_id: str,
    ) -> None:
        from .adaptive_flags import load_flags

        if load_flags().get("LESSONS") == "off":
            return
        try:
            from .lesson_runtime import FailureLessonRuntime

            FailureLessonRuntime.for_root(self.runner.root).record_failure(
                kind=kind,
                error=error,
                files=[str(item) for item in (work_order.get("target_files") or [])],
                cause=kind,
                source_run=str(getattr(self.runner, "root", "")),
                tags=[str(phase_id)],
            )
        except Exception:
            # Memory is best-effort; never break a run because of a lesson write.
            return

    def execute(
        self,
        run_id: str,
        *,
        coder: ExecutionSeat,
        reviewers: Mapping[str, ExecutionSeat],
        tier: str | None = None,
        low_risk_opt_in: bool = False,
    ) -> ExecutionResult:
        store, plan, _, workspace = self._load(run_id)
        store.assert_state("PROVISIONING")
        import approval_policy
        try:
            self.runner.assert_usage(run_id)
        except approval_policy.ApprovalError as exc:
            raise ExecutionError(f"usage approval: {exc}") from exc
        profile = resolve_tier_profile(
            plan, tier=tier, low_risk_opt_in=low_risk_opt_in,
        )
        assert_tier_matches_signoff(plan, profile, requested_tier=tier)
        required_roles = profile.reviewer_roles
        missing = [role for role in required_roles if role not in reviewers]
        if missing:
            raise ExecutionError(
                f"{profile.tier} requires reviewer seat(s): {', '.join(missing)}"
            )
        manifest = store.load()
        active_seats = [coder] + [reviewers[role] for role in required_roles]
        if manifest.get("preset") == "free-only":
            paid = [seat.model for seat in active_seats if not seat.is_free]
            if paid:
                raise ExecutionError(
                    "free-only preset blocks paid seats: " + ", ".join(paid)
                )
        coder_family = coder.family or model_family(coder.model)
        for role in required_roles:
            seat = reviewers[role]
            if (seat.family or model_family(seat.model)) == coder_family:
                raise CrossFamilyError(
                    f"reviewer {role} must be a different family from coder {coder_family}"
                )
        try:
            store.append_event("tier.selected", {
                "tier": profile.tier,
                "signed_tier": signed_tier(plan),
                "planner": profile.planner,
                "reviewer_roles": list(profile.reviewer_roles),
                "meeting": profile.meeting,
                "low_risk_opt_in": bool(
                    low_risk_opt_in or plan.get("low_risk_opt_in") is True
                ),
            })
        except Exception:
            pass

        budget = self._budget(store, plan)
        test_hashes = self._snapshot_tests(workspace.path, plan)
        phases = topo_phases(plan)
        if not phases:
            raise ExecutionError("plan has no phases")
        # F02: durable attempt history under the run directory.
        attempt_history = AttemptHistory(store.run_dir)
        # F01: validate/project the task graph once, before any work starts.
        from .taskgraph import TaskGraph, TaskGraphError

        graph_problems: list[str] = []
        task_graph: Any = None
        try:
            task_graph = TaskGraph.from_plan(plan, include_work_orders=True)
        except TaskGraphError as exc:
            graph_problems = [str(exc)]
        except Exception as exc:  # noqa: BLE001 - projection must not block work
            graph_problems = [f"{type(exc).__name__}: {exc}"]
        if task_graph is not None:
            try:
                graph_view = task_graph.as_dict()
                store.append_event("taskgraph.validated", {
                    "run_id": run_id,
                    "size": graph_view.get("size"),
                    "waves": graph_view.get("waves"),
                    "problems": graph_problems,
                })
            except Exception:  # noqa: BLE001
                pass
        else:
            try:
                store.append_event("taskgraph.invalid", {
                    "run_id": run_id, "problems": graph_problems,
                })
            except Exception:  # noqa: BLE001
                pass
        store.transition("worktree_ready")
        phase_results: list[dict[str, Any]] = []
        checkpoints: list[dict[str, Any]] = []
        requests = 0
        repairs = 0
        repair_count = 0
        last_signature: tuple[str, str] | None = None
        repeated = 0
        execution_tier = profile.tier
        meeting_mode = profile.meeting or bool(plan.get("meeting_mode"))
        active_reviewers = {
            role: reviewers[role] for role in required_roles if role in reviewers
        }

        for phase_index, phase in enumerate(phases):
            phase_id = str(phase["id"])
            store.update(current_phase=phase_id)
            phase_result = {"phase_id": phase_id, "work_orders": [], "status": "running"}
            for work_order in phase.get("work_orders") or []:
                work_id = str(work_order.get("id") or "work")
                capability = build_capability_context(
                    self.runner.root,
                    workspace.path,
                    str(work_order.get("objective") or ""),
                    execution_tier,
                    target_files=work_order.get("target_files") or [],
                )
                if capability.text:
                    work_order = dict(work_order)
                    existing = str(work_order.get("input_context") or "")
                    work_order["input_context"] = (
                        existing + "\n\n" + capability.text
                    ).strip()
                try:
                    store.append_event("capability.context", {
                        "tier": execution_tier,
                        "work_order": work_id,
                        "components": sorted(capability.components),
                        "errors": capability.errors,
                    })
                except Exception:
                    pass
                feedback: str | None = None
                verified = False
                last_attempt_record: Any = None
                for attempt in range(0, self.max_repair_attempts + 1):
                    is_repair = attempt > 0
                    tx = None
                    attempt_record = self._begin_attempt(
                        attempt_history, work_id, attempt + 1, coder, coder_family,
                        workspace.path, work_order, run_id=run_id, phase_id=phase_id,
                    )
                    if attempt_record is not None:
                        last_attempt_record = attempt_record
                    try:
                        self._mark_working(attempt_history, attempt_record)
                        files = self._call(
                            budget,
                            phase_id,
                            coder,
                            lambda wo=work_order, attempt=attempt, feedback=feedback: coder.backend.propose(
                                wo,
                                cwd=workspace.path,
                                round_index=attempt + 1,
                                feedback=feedback,
                            ),
                            repair=is_repair,
                        )
                        requests += 1
                        if is_repair:
                            repairs += 1
                            repair_count += 1
                        if not isinstance(files, dict) or not files:
                            raise ExecutionError(f"coder returned no files for {work_id}")
                        files = {str(k): str(v) for k, v in files.items()}
                        tx, verify, diff, files = self._apply(
                            workspace.path, work_order, files, test_hashes
                        )
                        self._record_verify(
                            store, phase_id=phase_id, work_id=work_id,
                            work_order=work_order, verify=verify,
                        )
                        signature = (
                            work_id,
                            f"{verify.get('exit_code')}:{str(verify.get('output_tail') or verify.get('stderr') or '')[:200]}",
                        )
                        if tx is None:
                            self._finish_attempt(
                                attempt_history, attempt_record, ok=False,
                                verified=True,
                                error=f"verify failed: {signature[1]}",
                            )
                            self._record_lesson(
                                kind="verify_failure", error=signature[1],
                                work_order=work_order, phase_id=phase_id,
                            )
                            if signature == last_signature:
                                repeated += 1
                            else:
                                last_signature = signature
                                repeated = 0
                            if repeated >= self.max_no_progress:
                                raise NoProgressError(
                                    f"no progress after repeated failure: {work_id}"
                                )
                            feedback = f"verify failed: {signature[1]}"
                            phase_result["work_orders"].append(
                                {"id": work_id, "status": "failed", "attempt": attempt + 1}
                            )
                            continue
                        if meeting_mode:
                            findings = self._review_meeting(
                                store, budget, phase_id, phase, work_order,
                                diff, active_reviewers, coder_family, verify,
                            )
                        elif required_roles:
                            findings = self._review(
                                budget,
                                phase_id,
                                phase,
                                work_order,
                                diff,
                                active_reviewers,
                                coder_family,
                                roles=required_roles,
                            )
                        else:
                            # E1 is an explicit, low-risk opt-in: verify and
                            # secret scanning still run, but no independent seat.
                            findings = []
                        tx.commit()
                        tx = None
                        verified = True
                        self._record_reviews(
                            store, phase_id=phase_id, work_id=work_id, findings=findings,
                        )
                        self._finish_attempt(
                            attempt_history, attempt_record, ok=True, verified=True,
                        )
                        phase_result["work_orders"].append(
                            {
                                "id": work_id,
                                "status": "verified",
                                "attempt": attempt + 1,
                                "reviews": [finding.__dict__ for finding in findings],
                            }
                        )
                        break
                    except ReviewerUnavailable as exc:
                        self._finish_attempt(
                            attempt_history, attempt_record, ok=False, error=exc,
                        )
                        if tx is not None and getattr(tx, "state", "") == "open":
                            try:
                                tx.rollback("reviewer unavailable")
                            except Exception:
                                pass
                        try:
                            self.runner.pause(run_id, reason=f"reviewer unavailable: {str(exc)[:300]}")
                        except Exception:
                            pass
                        try:
                            from .alerts import notify_event

                            notify_event(self.runner.root, "CRASH", {
                                "run_id": run_id, "status": "PAUSED",
                                "phase_id": phase_id,
                                "reason": f"reviewer unavailable: {str(exc)[:300]}",
                            })
                        except Exception:
                            pass
                        raise
                    except (SecurityVeto, TestIntegrityError, CrossFamilyError, NoProgressError) as exc:
                        self._finish_attempt(
                            attempt_history, attempt_record, ok=False, error=exc,
                        )
                        self._record_lesson(
                            kind=exc.code, error=str(exc),
                            work_order=work_order, phase_id=phase_id,
                        )
                        if tx is not None and getattr(tx, "state", "") == "open":
                            try:
                                tx.rollback("M3 review/integrity failure")
                            except Exception:
                                pass
                        reason = exc.code
                        try:
                            self.runner.quarantine_phase(
                                run_id,
                                phase_id,
                                reason=reason,
                                verify={"phase": phase_id, "work_order": work_id,
                                        "error": str(exc)[:500]},
                            )
                        except Exception:
                            pass
                        raise
                    except BudgetError as exc:
                        self._finish_attempt(
                            attempt_history, attempt_record, ok=False, error=exc,
                        )
                        if tx is not None and getattr(tx, "state", "") == "open":
                            try:
                                tx.rollback("M3 budget stop")
                            except Exception:
                                pass
                        try:
                            from .alerts import notify_event

                            notify_event(self.runner.root, "BUDGET_OVERRUN", {
                                "run_id": run_id, "status": "CRASH",
                                "phase_id": phase_id,
                                "reason": str(exc)[:300],
                            })
                        except Exception:
                            pass
                        raise
                    except ExecutionError as exc:
                        self._finish_attempt(
                            attempt_history, attempt_record, ok=False, error=exc,
                        )
                        self._record_lesson(
                            kind="review_reject", error=str(exc),
                            work_order=work_order, phase_id=phase_id,
                        )
                        if tx is not None and getattr(tx, "state", "") == "open":
                            try:
                                tx.rollback("M3 review rejected")
                            except Exception:
                                pass
                        feedback = str(exc)
                        phase_result["work_orders"].append(
                            {"id": work_id, "status": "failed", "attempt": attempt + 1,
                             "error": str(exc)[:300]}
                        )
                        continue
                    except Exception as exc:
                        self._finish_attempt(
                            attempt_history, attempt_record, ok=False, error=exc,
                        )
                        if tx is not None and getattr(tx, "state", "") == "open":
                            try:
                                tx.rollback("M3 apply failed")
                            except Exception:
                                pass
                        feedback = str(exc)
                        phase_result["work_orders"].append(
                            {"id": work_id, "status": "failed", "attempt": attempt + 1,
                             "error": str(exc)[:300]}
                        )
                        continue
                if not verified:
                    phase_result["status"] = "failed"
                    phase_results.append(phase_result)
                    self.runner.quarantine_phase(
                        run_id,
                        phase_id,
                        reason=f"work order {work_id} exhausted repairs",
                        verify={"phase": phase_id, "work_order": work_id},
                    )
                    raise ExecutionError(f"phase {phase_id} failed at {work_id}")

            store.transition("verify_start")
            checkpoint = self.runner.checkpoint_phase(run_id, phase_id)
            checkpoints.append(checkpoint)
            phase_result["status"] = "committed"
            phase_result["checkpoint"] = checkpoint
            phase_results.append(phase_result)
            if phase_index < len(phases) - 1:
                store.transition("next_phase")

        handoff = self.runner.handoff(run_id)
        try:
            from .alerts import notify_event

            notify_event(self.runner.root, "HANDOFF_READY", {
                "run_id": run_id,
                "status": store.load().get("status") or "HANDOFF_READY",
                "requests": requests,
            })
        except Exception:
            pass
        attempt_summary = attempt_history.summary()
        try:
            store.append_event("attempts.summary", attempt_summary)
        except Exception:  # noqa: BLE001
            pass
        graph_payload: dict[str, Any] = {
            "available": False, "problems": graph_problems, "states": {},
        }
        if task_graph is not None:
            states: dict[str, str] = {}
            attempts_by_node: dict[str, int] = {}
            for item in phase_results:
                phase_id = str(item.get("phase_id") or "")
                for order in item.get("work_orders") or []:
                    node_id = f"{phase_id}/{order.get('id')}"
                    states[node_id] = (
                        "done" if order.get("status") == "verified" else "failed"
                    )
                    attempts_by_node[node_id] = int(order.get("attempt") or 0)
            graph_payload = task_graph.as_dict()
            graph_payload.update({
                "available": True,
                "problems": graph_problems,
                "states": states,
                "attempts": attempts_by_node,
            })
        return ExecutionResult(
            run_id=run_id,
            status=store.load().get("status") or "HANDOFF_READY",
            phases=phase_results,
            checkpoints=checkpoints,
            requests=requests,
            repairs=repairs,
            handoff=handoff,
            tier=profile.tier,
            planner=profile.planner,
            reviewer_roles=profile.reviewer_roles,
            low_risk_opt_in=bool(
                low_risk_opt_in or plan.get("low_risk_opt_in") is True
            ),
            attempts=attempt_history.as_list(),
            graph=graph_payload,
            quota=_quota_evidence(store),
        )
