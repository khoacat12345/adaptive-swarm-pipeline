#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Phase 3 - deliberative policy.

The policy is deliberately conservative:

  * Security veto requires evidence of a security issue.
  * QA veto requires a failed machine verify or evidence of test tampering.
  * Perf/Docs are advisory: if only they reject, the meeting escalates to a
    human instead of auto-applying.
  * No policy path can override a failed verify.
  * No policy path can make a third round.
"""

from __future__ import annotations

from dataclasses import dataclass

from .protocol import Decision, Vote


HARD_VETO_ROLES = ("security", "qa")
ADVISORY_ROLES = ("perf", "docs")


def _as_votes(votes) -> dict[str, Vote]:
    if isinstance(votes, dict):
        raw = votes.values()
    else:
        raw = votes or []
    result: dict[str, Vote] = {}
    for item in raw:
        vote = item if isinstance(item, Vote) else Vote.from_dict(item)
        result[vote.role] = vote
    return result


def _conflict_list(conflicts) -> list[dict]:
    output: list[dict] = []
    for item in conflicts or []:
        if isinstance(item, dict):
            output.append(item)
        elif hasattr(item, "to_dict"):
            output.append(item.to_dict())
        else:
            output.append({"role": getattr(item, "role", "?"),
                           "verdict": getattr(item, "verdict", "reject"),
                           "evidence_refs": getattr(item, "evidence_refs", []),
                           "body": getattr(item, "body", "")})
    return output


def _has_evidence(conflict: dict) -> bool:
    refs = conflict.get("evidence_refs") or []
    return bool(refs)


def _conflict_for(role: str, conflicts: list[dict]) -> dict | None:
    for conflict in conflicts:
        if str(conflict.get("role") or "").lower() == role:
            return conflict
    return None


@dataclass
class VetoPolicy:
    """Deterministic decision policy for Round 2."""

    hard_roles: tuple[str, ...] = HARD_VETO_ROLES
    advisory_roles: tuple[str, ...] = ADVISORY_ROLES

    def evaluate(self, votes, conflicts=None, *,
                 verify_result: dict | None = None,
                 qa_test_tampering: bool = False,
                 round_no: int = 2) -> Decision:
        votes_map = _as_votes(votes)
        conflicts_list = _conflict_list(conflicts)
        verify = dict(verify_result or {})
        verify_failed = verify.get("exit_code") not in (0, "0", None)
        # Explicit flag or evidence text can prove QA test tampering.  The
        # evidence requirement is still enforced by the conflict check below.
        qa_conflict = _conflict_for("qa", conflicts_list) or {}
        qa_text = str(qa_conflict.get("body") or "").lower()
        tamper_words = ("xóa test", "xoa test", "delete test", "test tamper",
                        "sửa test", "sua test", "remove test")
        if any(word in qa_text for word in tamper_words):
            qa_test_tampering = True
        rejecting = [role for role, vote in votes_map.items()
                     if vote.verdict == "reject"]
        errored = [role for role, vote in votes_map.items()
                   if vote.verdict == "error"]
        reasons: list[str] = []
        vetoes: list[str] = []
        dissent: list[str] = []

        # Fail closed: a reviewer that never answered is not an approval.
        # Without this, 3 errors + 1 approve would fall through to
        # "all reviewers approve".
        if errored:
            reasons.append("reviewer unavailable: " + ", ".join(sorted(errored)))
            return Decision("reject", reasons, vetoes=[],
                            dissent=sorted(errored), round=round_no)

        # Hard vetoes require an evidence-backed conflict.  A bare reject is
        # escalated rather than treated as a veto.
        if "security" in rejecting:
            conflict = _conflict_for("security", conflicts_list)
            if conflict is not None and _has_evidence(conflict):
                vetoes.append("security")
                reasons.append("security veto: evidence-backed security issue")
            else:
                dissent.append("security")
                reasons.append("security reject without evidence -> escalate")
        if "qa" in rejecting:
            conflict = _conflict_for("qa", conflicts_list)
            if verify_failed:
                vetoes.append("qa")
                reasons.append("qa veto: machine verify failed")
            elif qa_test_tampering:
                vetoes.append("qa")
                reasons.append("qa veto: test tampering")
            elif conflict is not None and _has_evidence(conflict):
                vetoes.append("qa")
                reasons.append("qa veto: evidence-backed QA issue")
            else:
                dissent.append("qa")
                reasons.append("qa reject without evidence -> escalate")

        for role in rejecting:
            if role in vetoes:
                continue
            if role not in dissent:
                dissent.append(role)
            reasons.append(f"{role} dissent: {votes_map[role].reasons[0] if votes_map[role].reasons else 'reject'}")

        if vetoes:
            return Decision("reject", reasons, vetoes=vetoes,
                            dissent=sorted(set(dissent) - set(vetoes)),
                            round=round_no)

        if verify_failed:
            return Decision("reject", ["machine verify failed"],
                            vetoes=["qa"], dissent=[], round=round_no)

        if not rejecting:
            return Decision("approve", ["all reviewers approve"],
                            vetoes=[], dissent=[], round=round_no)

        advisory_only = all(role in self.advisory_roles for role in rejecting)
        if advisory_only:
            return Decision("escalate",
                            reasons + ["only advisory roles reject; human decision required"],
                            vetoes=[], dissent=sorted(set(rejecting)), round=round_no)

        # Mixed/noisy rejection is never auto-approved.
        return Decision("escalate", reasons + ["unresolved rejection; human decision required"],
                        vetoes=[], dissent=sorted(set(dissent)), round=round_no)
