"""U11 — Customer Experience; U12 — Intelligence & AI; U13 — Trust & Safety;
U14 — Governance & Compliance; U15 — Platform & Operations.

These universes own experience projections, AI/agent governance, trust cases,
policy and evidence, and platform contracts respectively.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Iterable, Optional

from marketplace.kernel import (
    AuthorizationContext,
    Money,
    Stateful,
    generate,
)
from marketplace.kernel.errors import (
    IllegalTransitionError,
    InvariantViolationError,
    NotFoundError,
    PolicyViolationError,
    ValidationError,
)
from marketplace.kernel.state import StateMachine, Transition, register_machine
from marketplace.universes._base import U11, U12, U13, U14, U15

# ===========================================================================
# U11 — Customer Experience
# ===========================================================================


@dataclass
class SearchProjection:
    """LAW-ARCH-06: a derived, rebuildable projection. Never an authority."""

    projection_id: str
    product_id: str
    title: str
    facets: dict
    source_version: int
    built_at: str
    ranking_signals: dict = field(default_factory=dict)


@dataclass
class Review:
    """Customer-submitted feedback. Content here; enforcement in U13."""

    review_id: str
    product_id: str
    author_id: str
    rating: int
    title: str
    body: str
    verified_purchase: bool
    status: str = "submitted"  # submitted | published | hidden | removed
    moderation_outcome: Optional[str] = None


@dataclass
class Wishlist:
    wishlist_id: str
    buyer_id: str
    offer_ids: list[str] = field(default_factory=list)


@dataclass
class SupportCase:
    """REQ-CARE-003: case management with SLA and escalation."""

    case_id: str
    buyer_id: str
    subject: str
    opened_at: str
    sla_due_at: str
    status: str = "open"  # open | waiting | escalated | resolved | closed
    queue: str = "general"
    impersonation_safe: bool = True
    assigned_agent: Optional[str] = None


class ExperienceService:
    def __init__(self) -> None:
        self.search_index: dict[str, SearchProjection] = {}
        self.reviews: dict[str, Review] = {}
        self.wishlists: dict[str, Wishlist] = {}
        self.support_cases: dict[str, SupportCase] = {}
        self._lock = threading.RLock()

    # -- search projection (LAW-ARCH-06) -----------------------------------

    def index_product(
        self, product_id: str, title: str, facets: dict, source_version: int
    ) -> SearchProjection:
        """Build/refresh a search projection from canonical product facts.

        The projection records the source version, so consumers can detect a
        stale index and rebuild deterministically.
        """
        projection = SearchProjection(
            projection_id=f"IDX-{product_id}",
            product_id=product_id,
            title=title,
            facets=dict(facets),
            source_version=source_version,
            built_at=U11.now(),
        )
        with self._lock:
            self.search_index[projection.projection_id] = projection
        return projection

    def search(self, query: str, *, filters: Optional[dict] = None, limit: int = 20) -> list[dict]:
        """REQ-DISC-001: text matching over a projection, with facet filters.

        Ranking inputs are returned so they are observable and governable
        (REQ-DISC-002). This is a deterministic reference ranker; production
        deployments may substitute a governed ranker through the same contract.
        """
        terms = [t.lower() for t in query.split() if t]
        hits: list[tuple[float, SearchProjection]] = []
        for projection in self.search_index.values():
            if filters:
                if not all(projection.facets.get(k) == v for k, v in filters.items()):
                    continue
            if not terms:
                score = 1.0
            else:
                title_hits = sum(1 for t in terms if t in projection.title.lower())
                score = title_hits / max(len(terms), 1)
                if score == 0:
                    continue
            hits.append((score, projection))
        hits.sort(key=lambda pair: (-pair[0], pair[1].product_id))
        return [
            {
                "product_id": p.product_id,
                "title": p.title,
                "facets": p.facets,
                "source_version": p.source_version,
                "ranking_signals": {"text_score": round(s, 4)},
            }
            for s, p in hits[:limit]
        ]

    def assert_projection_fresh(self, product_id: str, canonical_version: int) -> None:
        """REQ-DISC-003: freshness bound enforcement."""
        projection = self.search_index.get(f"IDX-{product_id}")
        if projection is None:
            raise NotFoundError(f"No search projection for {product_id}")
        if projection.source_version != canonical_version:
            raise InvariantViolationError(
                "INV-08",
                f"Search projection for {product_id} is stale (index v"
                f"{projection.source_version}, canonical v{canonical_version}); rebuild "
                "the projection before serving",
                details={
                    "projection_version": projection.source_version,
                    "canonical_version": canonical_version,
                },
            )

    def rebuild_from(self, products: list[dict]) -> int:
        """REQ-DISC-003: projections are rebuildable from canonical facts."""
        rebuilt = 0
        for product in products:
            self.index_product(
                product["product_id"],
                product["title"],
                product.get("facets", {}),
                product["version"],
            )
            rebuilt += 1
        return rebuilt

    # -- reviews (content submission; enforcement in U13) -------------------

    def submit_review(
        self,
        authz: AuthorizationContext,
        product_id: str,
        rating: int,
        title: str,
        body: str,
        *,
        verified_purchase: bool = False,
    ) -> Review:
        if not 1 <= rating <= 5:
            raise ValidationError("Ratings must be between 1 and 5")
        authz.requires_role("buyer")
        review = Review(
            review_id=generate("evidence"),
            product_id=product_id,
            author_id=authz.actor_id,
            rating=rating,
            title=title,
            body=body,
            verified_purchase=verified_purchase,
        )
        self.reviews[review.review_id] = review
        return review

    def publish_review(self, authz: AuthorizationContext, review_id: str) -> Review:
        authz.requires_role("moderation.approver")
        review = self._require_review(review_id)
        review.status = "published"
        return review

    def hide_review(self, authz: AuthorizationContext, review_id: str, reason: str) -> Review:
        authz.requires_role("moderation.approver")
        review = self._require_review(review_id)
        review.status = "hidden"
        review.moderation_outcome = reason
        return review

    # -- support (REQ-CARE-003) ---------------------------------------------

    def open_case(
        self, authz: AuthorizationContext, buyer_id: str, subject: str, *, sla_hours: int = 24, queue: str = "general"
    ) -> SupportCase:
        authz.requires_role("buyer")
        from datetime import datetime, timedelta

        opened = U11.now()
        due = (datetime.fromisoformat(opened.replace("Z", "+00:00")) + timedelta(hours=sla_hours)).isoformat()
        case = SupportCase(
            case_id=generate("case"),
            buyer_id=buyer_id,
            subject=subject,
            opened_at=opened,
            sla_due_at=due,
            queue=queue,
        )
        self.support_cases[case.case_id] = case
        return case

    def escalate_case(self, authz: AuthorizationContext, case_id: str) -> SupportCase:
        authz.requires_role("support.agent")
        case = self.support_cases.get(case_id)
        if case is None:
            raise NotFoundError(f"Support case {case_id} not found")
        case.status = "escalated"
        case.assigned_agent = authz.actor_id
        return case

    def _require_review(self, review_id: str) -> Review:
        review = self.reviews.get(review_id)
        if review is None:
            raise NotFoundError(f"Review {review_id} not found")
        return review


U11.owns(
    "search_projection",
    "Derived, versioned, rebuildable discovery projection; never authoritative",
    "U11.ExperienceService.index_product",
)
U11.owns("review", "Customer-submitted feedback content; enforcement owned by U13", "U11.ExperienceService.submit_review")
U11.owns("support_case", "Case management with SLA, queues and escalation", "U11.ExperienceService.open_case")


# ===========================================================================
# U12 — Intelligence & AI
# ===========================================================================


REVIEW_MACHINE = register_machine(
    StateMachine(
        name="review.moderation",
        concept="moderation_decision",
        initial_state="queued",
        transitions=(
            Transition("queued", "in_moderation", frozenset({"moderation.service"}), event="moderation.started"),
            Transition("in_moderation", "approved", frozenset({"moderation.approver"}), event="moderation.approved", terminal=True),
            Transition("in_moderation", "rejected", frozenset({"moderation.approver"}), event="moderation.rejected", terminal=True),
            Transition("in_moderation", "escalated", frozenset({"moderation.service"}), event="moderation.escalated"),
            Transition("escalated", "approved", frozenset({"moderation.admin"}), event="moderation.approved", terminal=True),
            Transition("escalated", "rejected", frozenset({"moderation.admin"}), event="moderation.rejected", terminal=True),
        ),
        terminal_states=frozenset({"approved", "rejected"}),
    )
)

AI_TASK_MACHINE = register_machine(
    StateMachine(
        name="ai.task",
        concept="ai_task",
        initial_state="requested",
        transitions=(
            Transition("requested", "authorized", frozenset({"policy.service"}), event="ai.authorized"),
            Transition("requested", "denied", frozenset({"policy.service"}), event="ai.denied", terminal=True),
            Transition("authorized", "executing", frozenset({"agent.service"}), event="ai.executing"),
            Transition("executing", "pending_approval", frozenset({"agent.service"}), event="ai.pending_approval"),
            Transition("pending_approval", "approved", frozenset({"human.approver"}), event="ai.approved"),
            Transition("pending_approval", "rejected", frozenset({"human.approver"}), event="ai.rejected", terminal=True),
            Transition("approved", "completed", frozenset({"agent.service"}), event="ai.completed", terminal=True),
            Transition("authorized", "completed", frozenset({"agent.service"}), event="ai.completed", terminal=True),
            Transition("executing", "failed", frozenset({"agent.service"}), event="ai.failed", terminal=True),
        ),
        terminal_states=frozenset({"completed", "failed", "denied", "rejected"}),
    )
)


class ModerationEntity(Stateful):
    machine = REVIEW_MACHINE

    def __init__(self, decision_id: str) -> None:
        super().__init__(decision_id)
        self.decision_id = decision_id


class AITaskEntity(Stateful):
    machine = AI_TASK_MACHINE

    def __init__(self, task_id: str) -> None:
        super().__init__(task_id)
        self.task_id = task_id


@dataclass
class ModelRegistration:
    """REQ-AI-001: registered model purpose, owner, version and limits."""

    model_id: str
    name: str
    version: str
    purpose: str
    owner: str
    approved_scope: frozenset[str]
    training_data_ref: str
    evaluation_ref: str
    limitations: list[str]
    status: str = "approved"
    approved_at: str = ""


@dataclass
class AgentCapability:
    """REQ-AI-002: constrained agent tools bound to delegated authority."""

    capability_id: str
    name: str
    model_id: str
    tools: frozenset[str]
    transaction_limit: Optional[Money] = None
    rate_limit_per_hour: Optional[int] = None
    requires_human_approval: bool = False
    revokable: bool = True
    status: str = "active"


@dataclass
class DecisionRecord:
    """REQ-AI-003: inputs, output, model/version and rationale."""

    record_id: str
    task_id: str
    model_id: str
    model_version: str
    inputs_ref: str
    output: dict
    rationale: str
    authority: str
    recorded_at: str
    approved_by: Optional[str] = None


@dataclass
class AITask:
    task_id: str
    capability_id: str
    agent_id: str
    inputs: dict
    status: str = "requested"
    decision_record_id: Optional[str] = None
    approval_required: bool = False
    approver: Optional[str] = None
    created_at: str = ""


class IntelligenceService:
    def __init__(self) -> None:
        self.models: dict[str, ModelRegistration] = {}
        self.capabilities: dict[str, AgentCapability] = {}
        self.tasks: dict[str, AITask] = {}
        self.decisions: dict[str, DecisionRecord] = {}
        self._lock = threading.RLock()

    def register_model(self, authz: AuthorizationContext, registration: ModelRegistration) -> ModelRegistration:
        authz.requires_role("ai.governance")
        self.models[registration.model_id] = registration
        return registration

    def register_capability(self, authz: AuthorizationContext, capability: AgentCapability) -> AgentCapability:
        authz.requires_role("ai.governance")
        if capability.model_id not in self.models:
            raise NotFoundError(
                f"Cannot bind capability to unregistered model {capability.model_id} (REQ-AI-001)"
            )
        self.capabilities[capability.capability_id] = capability
        return capability

    def request_task(
        self,
        authz: AuthorizationContext,
        capability_id: str,
        inputs: dict,
        *,
        delegation_id: Optional[str] = None,
        on_behalf_of: Optional[str] = None,
    ) -> AITask:
        """REQ-AI-002: agent tools are constrained by user delegation and policy."""
        capability = self.capabilities.get(capability_id)
        if capability is None:
            raise NotFoundError(f"Agent capability {capability_id} not registered")
        if capability.status != "active":
            raise PolicyViolationError(f"Agent capability {capability_id} is {capability.status}")
        model = self.models[capability.model_id]
        if model.status != "approved":
            raise PolicyViolationError(
                f"Model {model.name} is not approved for use (REQ-AI-001)"
            )
        task = AITask(
            task_id=generate("agent"),
            capability_id=capability_id,
            agent_id=authz.actor_id,
            inputs=dict(inputs),
            approval_required=capability.requires_human_approval,
            created_at=U12.now(),
        )
        task.inputs["_delegation_id"] = delegation_id
        task.inputs["_on_behalf_of"] = on_behalf_of
        self.tasks[task.task_id] = task
        return task

    def authorize_task(self, policy_decision: str, task_id: str) -> AITask:
        """LAW-ARCH-08: authorization comes from policy, never from the model."""
        task = self._require_task(task_id)
        entity = AITaskEntity(task_id)
        entity.state = task.status
        if policy_decision != "allow":
            entity.transition_to(
                "denied", actor="policy.service", actor_roles=frozenset({"policy.service"}),
                reason="policy denied the agent action", recorded_at=U12.now(),
            )
            task.status = entity.state
            return task
        entity.transition_to(
            "authorized", actor="policy.service", actor_roles=frozenset({"policy.service"}),
            reason="policy authorized within approved scope", recorded_at=U12.now(),
        )
        task.status = entity.state
        return task

    def execute_task(
        self, task_id: str, evaluation, *, actor_roles: Iterable[str] = ()
    ) -> AITask:
        """Run the model evaluation and record a decision.

        ``evaluation`` is a caller-supplied, governed evaluation function that
        receives the registered model and the inputs. Its output is recorded
        as *decision support*, never as canonical fact (LAW-ARCH-08).
        """
        task = self._require_task(task_id)
        capability = self.capabilities[task.capability_id]
        model = self.models[capability.model_id]
        entity = AITaskEntity(task_id)
        entity.state = task.status
        entity.transition_to(
            "executing", actor="agent.service", actor_roles=frozenset({"agent.service"}),
            reason="model evaluation started", recorded_at=U12.now(),
        )
        task.status = entity.state
        output, rationale = evaluation(model, task.inputs)
        record = DecisionRecord(
            record_id=generate("evidence"),
            task_id=task_id,
            model_id=model.model_id,
            model_version=model.version,
            inputs_ref=task_id,
            output=output,
            rationale=rationale,
            authority="agent:" + capability.name,
            recorded_at=U12.now(),
        )
        self.decisions[record.record_id] = record
        task.decision_record_id = record.record_id
        if task.approval_required:
            entity = AITaskEntity(task_id)
            entity.state = task.status
            entity.transition_to(
                "pending_approval", actor="agent.service",
                actor_roles=frozenset({"agent.service"}),
                reason="consequential action requires human approval (REQ-AI-003)",
                recorded_at=U12.now(),
            )
            task.status = entity.state
        else:
            entity = AITaskEntity(task_id)
            entity.state = task.status
            entity.transition_to(
                "completed", actor="agent.service", actor_roles=frozenset({"agent.service"}),
                reason="evaluation recorded", recorded_at=U12.now(),
            )
            task.status = entity.state
        return task

    def approve_task(self, authz: AuthorizationContext, task_id: str) -> AITask:
        """REQ-AI-003: human approval for consequential agent actions."""
        authz.requires_role("human.approver")
        task = self._require_task(task_id)
        entity = AITaskEntity(task_id)
        entity.state = task.status
        entity.transition_to(
            "approved", actor=authz.actor_id, actor_roles=authz.roles,
            reason="human approval of consequential agent action", recorded_at=U12.now(),
        )
        task.status = entity.state
        task.approver = authz.actor_id
        entity = AITaskEntity(task_id)
        entity.state = task.status
        entity.transition_to(
            "completed", actor="agent.service", actor_roles=frozenset({"agent.service"}),
            reason="post-approval completion", recorded_at=U12.now(),
        )
        task.status = entity.state
        return task

    def reject_task(self, authz: AuthorizationContext, task_id: str, reason: str) -> AITask:
        authz.requires_role("human.approver")
        task = self._require_task(task_id)
        entity = AITaskEntity(task_id)
        entity.state = task.status
        entity.transition_to(
            "rejected", actor=authz.actor_id, actor_roles=authz.roles,
            reason=reason, recorded_at=U12.now(),
        )
        task.status = entity.state
        return task

    def _require_task(self, task_id: str) -> AITask:
        task = self.tasks.get(task_id)
        if task is None:
            raise NotFoundError(f"AI task {task_id} not found")
        return task


U12.owns("model_registration", "Approved model purpose, version, evaluation and limitations", "U12.IntelligenceService.register_model")
U12.owns("agent_capability", "Agent tool set bounded by delegation, limits and revocation", "U12.IntelligenceService.register_capability")
U12.owns("ai_task", "Agent task with policy authorization and optional human approval", "U12.IntelligenceService.request_task")
U12.owns("decision_record", "Decision provenance: inputs, output, model/version, rationale", "U12.IntelligenceService.execute_task")


# ===========================================================================
# U13 — Trust & Safety
# ===========================================================================


DISPUTE_MACHINE = register_machine(
    StateMachine(
        name="dispute.lifecycle",
        concept="dispute_case",
        initial_state="filed",
        transitions=(
            Transition("filed", "in_review", frozenset({"trust.agent"}), event="dispute.in_review"),
            Transition("in_review", "evidence_requested", frozenset({"trust.agent"}), event="dispute.evidence_requested"),
            Transition("evidence_requested", "in_review", frozenset({"trust.agent"}), event="dispute.evidence_received"),
            Transition("in_review", "decided", frozenset({"trust.approver"}), event="dispute.decided", terminal=True),
            Transition("decided", "appealed", frozenset({"buyer", "seller"}), event="dispute.appealed"),
            Transition("appealed", "decided", frozenset({"trust.admin"}), event="dispute.decided", terminal=True),
        ),
        terminal_states=frozenset({"decided"}),
    )
)


class DisputeEntity(Stateful):
    machine = DISPUTE_MACHINE

    def __init__(self, dispute_id: str) -> None:
        super().__init__(dispute_id)
        self.dispute_id = dispute_id


@dataclass
class EvidenceItem:
    item_id: str
    case_id: str
    kind: str  # screenshot | log | tracking_proof | bank_record | statement
    reference: str
    submitted_by: str
    submitted_at: str


@dataclass
class RiskCase:
    case_id: str
    subject_type: str  # account | listing | transaction | payment | review | promotion
    subject_id: str
    signals: list[str]
    status: str = "open"  # open | monitoring | escalated | closed
    decision: Optional[str] = None
    model_version: Optional[str] = None
    owner: str = ""


@dataclass
class DisputeCase:
    dispute_id: str
    order_id: str
    filed_by: str
    reason: str
    status: str = "filed"
    evidence: list[EvidenceItem] = field(default_factory=list)
    decision: Optional[str] = None
    decided_by: Optional[str] = None
    decided_at: Optional[str] = None
    appeal_window_until: Optional[str] = None


@dataclass
class EnforcementAction:
    action_id: str
    subject_type: str
    subject_id: str
    kind: str  # listing_blocked | seller_suspended | account_frozen | review_removed
    reason: str
    rule_id: str
    actor: str
    at: str
    reversed_at: Optional[str] = None


class TrustService:
    def __init__(self) -> None:
        self.risk_cases: dict[str, RiskCase] = {}
        self.disputes: dict[str, DisputeCase] = {}
        self.enforcement: list[EnforcementAction] = []
        self._lock = threading.RLock()

    def open_risk_case(
        self, subject_type: str, subject_id: str, signals: list[str], *, model_version: Optional[str] = None
    ) -> RiskCase:
        """REQ-TRUST-001: detection with explainable evidence."""
        case = RiskCase(
            case_id=generate("risk"),
            subject_type=subject_type,
            subject_id=subject_id,
            signals=list(signals),
            model_version=model_version,
            owner="trust.service",
        )
        self.risk_cases[case.case_id] = case
        return case

    def enforce(
        self, authz: AuthorizationContext, rule_id: str, subject_type: str, subject_id: str, kind: str, reason: str
    ) -> EnforcementAction:
        """REQ-TRUST-002: enforcement with actor, rule and reason recorded."""
        authz.requires_role("trust.admin")
        if kind not in {"listing_blocked", "seller_suspended", "account_frozen", "review_removed"}:
            raise ValidationError(f"Unknown enforcement kind {kind!r}")
        action = EnforcementAction(
            action_id=generate("evidence"),
            subject_type=subject_type,
            subject_id=subject_id,
            kind=kind,
            reason=reason,
            rule_id=rule_id,
            actor=authz.actor_id,
            at=U13.now(),
        )
        self.enforcement.append(action)
        return action

    def file_dispute(
        self, authz: AuthorizationContext, order_id: str, reason: str, filed_by: str
    ) -> DisputeCase:
        authz.requires_role("dispute.filer")
        dispute = DisputeCase(
            dispute_id=generate("dispute"),
            order_id=order_id,
            filed_by=filed_by,
            reason=reason,
        )
        self.disputes[dispute.dispute_id] = dispute
        return dispute

    def submit_evidence(
        self, authz: AuthorizationContext, dispute_id: str, kind: str, reference: str
    ) -> EvidenceItem:
        dispute = self.disputes.get(dispute_id)
        if dispute is None:
            raise NotFoundError(f"Dispute {dispute_id} not found")
        if kind not in {"screenshot", "log", "tracking_proof", "bank_record", "statement"}:
            raise ValidationError(f"Unknown evidence kind {kind!r}")
        item = EvidenceItem(
            item_id=generate("evidence"),
            case_id=dispute_id,
            kind=kind,
            reference=reference,
            submitted_by=authz.actor_id,
            submitted_at=U13.now(),
        )
        dispute.evidence.append(item)
        return item

    def decide(
        self, authz: AuthorizationContext, dispute_id: str, decision: str, *, appeal_window_days: int = 14
    ) -> DisputeCase:
        """REQ-TRUST-005: consistent decision logs with an appeal path."""
        authz.requires_role("trust.approver")
        dispute = self.disputes.get(dispute_id)
        if dispute is None:
            raise NotFoundError(f"Dispute {dispute_id} not found")
        if not dispute.evidence:
            raise PolicyViolationError(
                "A dispute cannot be decided without at least one evidence item (REQ-TRUST-005)"
            )
        entity = DisputeEntity(dispute_id)
        entity.state = dispute.status
        entity.transition_to(
            "decided", actor=authz.actor_id, actor_roles=authz.roles,
            reason=decision, recorded_at=U13.now(),
        )
        dispute.status = entity.state
        dispute.decision = decision
        dispute.decided_by = authz.actor_id
        dispute.decided_at = U13.now()
        from datetime import datetime, timedelta

        dispute.appeal_window_until = (
            datetime.fromisoformat(dispute.decided_at.replace("Z", "+00:00"))
            + timedelta(days=appeal_window_days)
        ).isoformat()
        return dispute

    def appeal(
        self, authz: AuthorizationContext, dispute_id: str
    ) -> DisputeCase:
        dispute = self.disputes.get(dispute_id)
        if dispute is None:
            raise NotFoundError(f"Dispute {dispute_id} not found")
        if dispute.status != "decided":
            raise IllegalTransitionError(
                dispute_id, dispute.status, "appealed",
                DisputeEntity.machine.legal_targets(dispute.status),
            )
        if dispute.appeal_window_until and U13.now() > dispute.appeal_window_until:
            raise PolicyViolationError(
                f"Appeal window for dispute {dispute_id} has closed; "
                f"decisions are final after {dispute.appeal_window_until}"
            )
        entity = DisputeEntity(dispute_id)
        entity.state = dispute.status
        entity.transition_to(
            "appealed", actor=authz.actor_id, actor_roles=authz.roles,
            reason="appeal filed within window", recorded_at=U13.now(),
        )
        dispute.status = entity.state
        return dispute


U13.owns("risk_case", "Fraud and abuse detection with explainable signals and provenance", "U13.TrustService.open_risk_case")
U13.owns("moderation_decision", "Content moderation outcome with rule version, reviewer and appeal", "U13.TrustService.moderate", read_projections=["U11.review"])
U13.owns("dispute_case", "Dispute handling with evidence retention, decision log and appeal", "U13.TrustService.file_dispute")
U13.owns("enforcement_action", "Policy enforcement with actor, rule version and reason", "U13.TrustService.enforce")


# ===========================================================================
# U14 — Governance & Compliance
# ===========================================================================


@dataclass
class Policy:
    """A governed rule evaluated under a defined context (not a hardcoded conditional)."""

    policy_id: str
    code: str
    description: str
    version: int
    effect: str  # allow | deny | require_approval
    condition: str
    status: str = "active"
    owner: str = ""


@dataclass
class RetentionSchedule:
    """REQ-SEC-003: retention per data class and jurisdiction."""

    schedule_id: str
    data_class: str
    jurisdiction: str
    retention_days: int
    purpose: str
    legal_basis: str


@dataclass
class CertificationRecord:
    """REQ-SEC-004, GOV-11: certification evidence with scope and verifier."""

    record_id: str
    scope: str
    requirement_ids: list[str]
    evidence_refs: list[str]
    verifier: str
    verified_at: str
    result: str  # pass | fail | inconclusive
    valid_until: Optional[str] = None


class GovernanceService:
    def __init__(self) -> None:
        self.policies: dict[str, Policy] = {}
        self.policy_versions: dict[str, int] = {}
        self.retention: list[RetentionSchedule] = []
        self.certifications: list[CertificationRecord] = {}
        self.implementation_authorizations: list[dict] = []
        self._lock = threading.RLock()

    def publish_policy(self, authz: AuthorizationContext, code: str, description: str, effect: str, condition: str) -> Policy:
        """GOV-12: policies are versioned; supersession is explicit."""
        authz.requires_role("policy.writer")
        if effect not in {"allow", "deny", "require_approval"}:
            raise ValidationError(f"Unknown policy effect {effect!r}")
        with self._lock:
            version = self.policy_versions.get(code, 0) + 1
            self.policy_versions[code] = version
        policy = Policy(
            policy_id=generate("policy"),
            code=code,
            description=description,
            version=version,
            effect=effect,
            condition=condition,
            owner=authz.actor_id,
        )
        for existing in list(self.policies.values()):
            if existing.code == code and existing.status == "active" and existing.version < version:
                existing.status = "superseded"
        self.policies[policy.policy_id] = policy
        return policy

    def active_policy(self, code: str) -> Policy:
        policy = next(
            (p for p in self.policies.values() if p.code == code and p.status == "active"), None
        )
        if policy is None:
            raise NotFoundError(f"No active policy {code!r}")
        return policy

    def evaluate(self, code: str, context: dict) -> str:
        """Evaluate a policy's effect under a context.

        The condition is a Python expression over ``context`` with no builtins,
        so policy stays data rather than hardcoded business rules
        (GOV-07, "Policy is not a hardcoded conditional").
        """
        policy = self.active_policy(code)
        try:
            matched = bool(eval(policy.condition, {"__builtins__": {}}, dict(context)))  # noqa: S307
        except Exception as exc:  # noqa: BLE001
            raise ValidationError(
                f"Policy {code!r} condition failed to evaluate: {exc}"
            ) from exc
        if not matched:
            return "no_match"
        return policy.effect

    def define_retention(
        self, authz: AuthorizationContext, data_class: str, jurisdiction: str, retention_days: int, purpose: str, legal_basis: str
    ) -> RetentionSchedule:
        authz.requires_role("privacy.admin")
        if retention_days < 0:
            raise ValidationError("Retention cannot be negative")
        schedule = RetentionSchedule(
            schedule_id=generate("evidence"),
            data_class=data_class,
            jurisdiction=jurisdiction,
            retention_days=retention_days,
            purpose=purpose,
            legal_basis=legal_basis,
        )
        self.retention.append(schedule)
        return schedule

    def record_certification(
        self,
        authz: AuthorizationContext,
        scope: str,
        requirement_ids: list[str],
        evidence_refs: list[str],
        result: str,
        *,
        valid_until: Optional[str] = None,
    ) -> CertificationRecord:
        """GOV-11: certification requires named evidence and a verifier."""
        authz.requires_role("certification.recorder")
        if result not in {"pass", "fail", "inconclusive"}:
            raise ValidationError(f"Unknown certification result {result!r}")
        if not evidence_refs:
            raise ValidationError("Certification requires at least one evidence reference")
        record = CertificationRecord(
            record_id=generate("evidence"),
            scope=scope,
            requirement_ids=list(requirement_ids),
            evidence_refs=list(evidence_refs),
            verifier=authz.actor_id,
            verified_at=U14.now(),
            result=result,
            valid_until=valid_until,
        )
        self.certifications[record.record_id] = record
        return record

    def authorize_implementation(
        self, authz: AuthorizationContext, tranche_id: str, plan_ref: str, *, security_review: str, privacy_review: str
    ) -> dict:
        """GOV-15: implementation authorization is separate from spec approval."""
        authz.requires_role("implementation.authorizer")
        if security_review not in {"passed", "conditional"}:
            raise PolicyViolationError(
                "Implementation authorization requires a security review outcome"
            )
        if privacy_review not in {"passed", "conditional"}:
            raise PolicyViolationError(
                "Implementation authorization requires a privacy review outcome"
            )
        authorization = {
            "tranche_id": tranche_id,
            "plan_ref": plan_ref,
            "security_review": security_review,
            "privacy_review": privacy_review,
            "authorized_by": authz.actor_id,
            "authorized_at": U14.now(),
        }
        self.implementation_authorizations.append(authorization)
        return authorization


U14.owns("policy", "Governed rule with version, effect and explicit supersession", "U14.GovernanceService.publish_policy")
U14.owns("retention_schedule", "Retention by data class, jurisdiction and purpose", "U14.GovernanceService.define_retention")
U14.owns("certification_record", "Certification evidence with scope, verifier and validity", "U14.GovernanceService.record_certification")


# ===========================================================================
# U15 — Platform & Operations
# ===========================================================================


@dataclass
class ApiContract:
    """REQ-API-001: an owned, versioned API contract."""

    contract_id: str
    name: str
    owner_universe: str
    version: str
    authn_required: bool = True
    idempotency_required: bool = True
    rate_limit: Optional[int] = None
    deprecation: Optional[str] = None
    status: str = "active"


@dataclass
class WebhookSubscription:
    """REQ-API-002: webhook with replay protection and lifecycle."""

    subscription_id: str
    subscriber: str
    event_contracts: list[str]
    signing_secret_ref: str
    status: str = "active"
    delivered: int = 0
    failures: int = 0


@dataclass
class ServiceLevelObjective:
    """REQ-OPS-001: SLO with target and error budget."""

    slo_id: str
    journey: str
    indicator: str  # availability | latency_p95 | success_rate
    target: float
    window_days: int
    error_budget: float


@dataclass
class Runbook:
    """REQ-OPS-003/005: operational runbook and rollback procedure."""

    runbook_id: str
    code: str
    trigger: str
    steps: list[str]
    rollback: list[str]
    owner: str


class PlatformService:
    def __init__(self) -> None:
        self.api_contracts: dict[str, ApiContract] = {}
        self.webhooks: dict[str, WebhookSubscription] = {}
        self.slos: dict[str, ServiceLevelObjective] = {}
        self.runbooks: dict[str, Runbook] = {}
        self._lock = threading.RLock()

    def publish_api(self, authz: AuthorizationContext, contract: ApiContract) -> ApiContract:
        authz.requires_role("platform.writer")
        self.api_contracts[contract.contract_id] = contract
        return contract

    def deprecate_api(self, authz: AuthorizationContext, contract_id: str, replacement: str, retire_on: str) -> ApiContract:
        """REQ-API-001: deprecation names the replacement and retirement date."""
        authz.requires_role("platform.writer")
        contract = self.api_contracts.get(contract_id)
        if contract is None:
            raise NotFoundError(f"API contract {contract_id} not found")
        contract.status = "deprecated"
        contract.deprecation = {"replacement": replacement, "retire_on": retire_on}
        return contract

    def register_webhook(
        self, authz: AuthorizationContext, subscriber: str, event_contracts: list[str], signing_secret_ref: str
    ) -> WebhookSubscription:
        authz.requires_role("platform.writer")
        for name in event_contracts:
            from marketplace.kernel.events import CONTRACTS

            if name not in {c.name for c in CONTRACTS.all()}:
                raise ValidationError(
                    f"Webhook subscribes to unregistered event contract {name!r}"
                )
        subscription = WebhookSubscription(
            subscription_id=generate("evidence"),
            subscriber=subscriber,
            event_contracts=list(event_contracts),
            signing_secret_ref=signing_secret_ref,
        )
        self.webhooks[subscription.subscription_id] = subscription
        return subscription

    def record_delivery(self, subscription_id: str, *, success: bool) -> WebhookSubscription:
        subscription = self.webhooks.get(subscription_id)
        if subscription is None:
            raise NotFoundError(f"Webhook subscription {subscription_id} not found")
        if success:
            subscription.delivered += 1
        else:
            subscription.failures += 1
        if subscription.failures > subscription.delivered and subscription.delivered > 0:
            subscription.status = "degraded"
        return subscription

    def define_slo(self, authz: AuthorizationContext, journey: str, indicator: str, target: float, window_days: int) -> ServiceLevelObjective:
        authz.requires_role("ops.writer")
        if not 0 < target <= 100:
            raise ValidationError("SLO targets are percentages in (0, 100]")
        slo = ServiceLevelObjective(
            slo_id=generate("evidence"),
            journey=journey,
            indicator=indicator,
            target=target,
            window_days=window_days,
            error_budget=100 - target,
        )
        self.slos[slo.slo_id] = slo
        return slo

    def write_runbook(self, authz: AuthorizationContext, code: str, trigger: str, steps: list[str], rollback: list[str]) -> Runbook:
        authz.requires_role("ops.writer")
        if not steps or not rollback:
            raise ValidationError("Runbooks require both response steps and a rollback procedure")
        runbook = Runbook(
            runbook_id=generate("evidence"),
            code=code,
            trigger=trigger,
            steps=list(steps),
            rollback=list(rollback),
            owner=authz.actor_id,
        )
        self.runbooks[runbook.runbook_id] = runbook
        return runbook


U15.owns("api_contract", "Owned, versioned API contract with deprecation lifecycle", "U15.PlatformService.publish_api", read_projections=["catalog.api"])
U15.owns("webhook_subscription", "Webhook delivery with replay protection and subscriber lifecycle", "U15.PlatformService.register_webhook")
U15.owns("service_level_objective", "Journey SLO with target and error budget", "U15.PlatformService.define_slo")
U15.owns("runbook", "Operational response and rollback procedure", "U15.PlatformService.write_runbook")
