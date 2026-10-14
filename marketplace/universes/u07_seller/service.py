"""U07 — Marketplace & Seller.

Canonical concepts owned: seller_profile, onboarding_case, seller_contract,
commission_terms, seller_performance.

REQ-IAM-005, REQ-IAM-006. Seller identity references Party (U01); the
commercial relationship lives here. Onboarding is a governed state machine
with verification evidence (§7.1 required machine).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Iterable, Optional

from marketplace.kernel import (
    AuthorizationContext,
    Money,
    Stateful,
    generate,
)
from marketplace.kernel.errors import IllegalTransitionError, InvariantViolationError, NotFoundError, ValidationError
from marketplace.kernel.state import StateMachine, Transition, register_machine
from marketplace.universes._base import U07

# ---------------------------------------------------------------------------
# State machine: seller onboarding (§7.1 required machine)
# ---------------------------------------------------------------------------

ONBOARDING_MACHINE = register_machine(
    StateMachine(
        name="seller.onboarding",
        concept="onboarding_case",
        initial_state="submitted",
        transitions=(
            Transition("submitted", "verifying", frozenset({"onboarding.agent"}), event="onboarding.verification_started"),
            Transition("verifying", "verified", frozenset({"onboarding.approver"}), event="onboarding.verified"),
            Transition("verifying", "remediation", frozenset({"onboarding.agent"}), event="onboarding.remediation_requested"),
            Transition("remediation", "verifying", frozenset({"seller", "onboarding.agent"}), event="onboarding.resubmitted"),
            Transition("verifying", "rejected", frozenset({"onboarding.approver"}), event="onboarding.rejected", terminal=True),
            Transition("verified", "contracted", frozenset({"contracts.writer"}), event="seller.contract_signed", terminal=True),
            Transition("contracted", "suspended", frozenset({"trust.admin"}), event="seller.suspended"),
            Transition("suspended", "contracted", frozenset({"trust.admin"}), event="seller.reinstated"),
            Transition("contracted", "exited", frozenset({"contracts.admin"}), event="seller.exited", terminal=True),
        ),
        terminal_states=frozenset({"rejected", "exited"}),
    )
)


class OnboardingCaseEntity(Stateful):
    machine = ONBOARDING_MACHINE

    def __init__(self, case_id: str) -> None:
        super().__init__(case_id)
        self.case_id = case_id


# ---------------------------------------------------------------------------
# Entities
# ---------------------------------------------------------------------------


@dataclass
class VerificationEvidence:
    evidence_id: str
    case_id: str
    kind: str  # business_registry | kyb | beneficial_ownership | tax_id | bank_account | insurance
    status: str  # pending | passed | failed | expired
    reference: str
    verified_at: Optional[str] = None
    expires_at: Optional[str] = None


@dataclass
class OnboardingCase:
    case_id: str
    seller_id: str
    party_id: str
    status: str = "submitted"
    evidence: list[VerificationEvidence] = field(default_factory=list)
    submitted_at: str = ""
    decision_audit: list[dict] = field(default_factory=list)


@dataclass
class CommissionTerms:
    """REQ-FIN-005: transparent commission computation."""

    terms_id: str
    rate: Decimal  # fraction of net sales
    fixed_fee: Optional[Money] = None
    minimum_fee: Optional[Money] = None
    currency: str = "USD"
    effective_from: str = ""
    effective_until: Optional[str] = None


@dataclass
class SellerContract:
    """REQ-IAM-006: seller agreement, fee terms, territories and permissions."""

    contract_id: str
    seller_id: str
    status: str = "draft"  # draft | active | suspended | terminated
    territories: frozenset[str] = field(default_factory=lambda: frozenset())
    channels: frozenset[str] = field(default_factory=lambda: frozenset())
    catalog_permissions: frozenset[str] = field(default_factory=lambda: frozenset({"create_listing"}))
    commission_terms_id: Optional[str] = None
    valid_from: str = ""
    valid_until: Optional[str] = None


@dataclass
class SellerProfile:
    """The commercial marketplace relationship. Legal party is U01."""

    seller_id: str
    party_id: str
    display_name: str
    status: str = "prospective"  # prospective | active | suspended | exited
    contract_id: Optional[str] = None
    performance: dict = field(default_factory=dict)
    version: int = 0


# ---------------------------------------------------------------------------
# Seller service
# ---------------------------------------------------------------------------


class SellerService:
    def __init__(self) -> None:
        self.profiles: dict[str, SellerProfile] = {}
        self.cases: dict[str, OnboardingCase] = {}
        self.contracts: dict[str, SellerContract] = {}
        self.commission_terms: dict[str, CommissionTerms] = {}

    def register_seller(
        self, authz: AuthorizationContext, party_id: str, display_name: str
    ) -> SellerProfile:
        authz.requires_role("seller.recruiter")
        profile = SellerProfile(
            seller_id=generate("seller"),
            party_id=party_id,
            display_name=display_name,
        )
        self.profiles[profile.seller_id] = profile
        return profile

    def open_onboarding(
        self, authz: AuthorizationContext, seller_id: str
    ) -> OnboardingCase:
        authz.requires_role("onboarding.agent")
        profile = self._require_profile(seller_id)
        case = OnboardingCase(
            case_id=generate("case"),
            seller_id=seller_id,
            party_id=profile.party_id,
            submitted_at=U07.now(),
        )
        self.cases[case.case_id] = case
        return case

    def submit_evidence(
        self, authz: AuthorizationContext, case_id: str, kind: str, reference: str
    ) -> VerificationEvidence:
        authz.requires_role("onboarding.agent")
        case = self._require_case(case_id)
        if kind not in {
            "business_registry", "kyb", "beneficial_ownership", "tax_id",
            "bank_account", "insurance",
        }:
            raise ValidationError(f"Unknown verification evidence kind {kind!r}")
        evidence = VerificationEvidence(
            evidence_id=generate("evidence"),
            case_id=case_id,
            kind=kind,
            status="pending",
            reference=reference,
        )
        case.evidence.append(evidence)
        return evidence

    def record_verification_result(
        self, authz: AuthorizationContext, case_id: str, evidence_id: str, *, passed: bool, note: str
    ) -> VerificationEvidence:
        authz.requires_role("onboarding.approver")
        case = self._require_case(case_id)
        evidence = next((e for e in case.evidence if e.evidence_id == evidence_id), None)
        if evidence is None:
            raise NotFoundError(f"Evidence {evidence_id} not on case {case_id}")
        evidence.status = "passed" if passed else "failed"
        evidence.verified_at = U07.now()
        case.decision_audit.append(
            {
                "evidence_id": evidence_id,
                "passed": passed,
                "note": note,
                "by": authz.actor_id,
                "at": evidence.verified_at,
            }
        )
        if case.status == "submitted":
            entity = OnboardingCaseEntity(case_id)
            entity.state = case.status
            entity.transition_to(
                "verifying", actor=authz.actor_id, actor_roles=authz.roles,
                reason="verification started", recorded_at=U07.now(),
            )
            case.status = entity.state
        return evidence

    def complete_verification(self, authz: AuthorizationContext, case_id: str) -> OnboardingCase:
        authz.requires_role("onboarding.approver")
        case = self._require_case(case_id)
        required = {"business_registry", "tax_id"}
        kinds = {e.kind for e in case.evidence}
        missing = required - kinds
        if missing:
            raise ValidationError(
                f"Mandatory verification evidence missing: {sorted(missing)}"
            )
        failed = [e.evidence_id for e in case.evidence if e.status != "passed"]
        if failed:
            raise InvariantViolationError(
                "GOV-11",
                "Cannot complete verification with failed evidence; request remediation",
                details={"failed": failed},
            )
        entity = OnboardingCaseEntity(case_id)
        entity.state = case.status
        entity.transition_to(
            "verified", actor=authz.actor_id, actor_roles=authz.roles,
            reason="all mandatory verification evidence passed",
            recorded_at=U07.now(),
        )
        case.status = entity.state
        return case

    def sign_contract(
        self,
        authz: AuthorizationContext,
        case_id: str,
        *,
        territories: Iterable[str],
        channels: Iterable[str],
        commission_rate: Decimal,
        currency: str,
        valid_from: str,
        valid_until: Optional[str] = None,
    ) -> SellerContract:
        authz.requires_role("contracts.writer")
        case = self._require_case(case_id)
        if case.status != "verified":
            raise IllegalTransitionError(
                case_id, case.status, "contracted",
                OnboardingCaseEntity.machine.legal_targets(case.status),
            )
        terms = CommissionTerms(
            terms_id=generate("evidence"),
            rate=commission_rate,
            currency=currency,
            effective_from=valid_from,
            effective_until=valid_until,
        )
        self.commission_terms[terms.terms_id] = terms
        contract = SellerContract(
            contract_id=generate("evidence"),
            seller_id=case.seller_id,
            status="active",
            territories=frozenset(territories),
            channels=frozenset(channels),
            commission_terms_id=terms.terms_id,
            valid_from=valid_from,
            valid_until=valid_until,
        )
        self.contracts[contract.contract_id] = contract
        entity = OnboardingCaseEntity(case_id)
        entity.state = case.status
        entity.transition_to(
            "contracted", actor=authz.actor_id, actor_roles=authz.roles,
            reason="seller agreement executed", recorded_at=U07.now(),
        )
        case.status = entity.state
        profile = self.profiles[case.seller_id]
        profile.contract_id = contract.contract_id
        profile.status = "active"
        profile.version += 1
        return contract

    def suspend(self, authz: AuthorizationContext, seller_id: str, reason: str) -> SellerProfile:
        authz.requires_role("trust.admin")
        profile = self._require_profile(seller_id)
        profile.status = "suspended"
        profile.version += 1
        U07.provenance(
            authz, "suspend_seller", "seller_profile", seller_id,
            reason=reason, source="U07.SellerService",
            version_before=profile.version - 1, version_after=profile.version,
        )
        return profile

    def commission_for(self, seller_id: str) -> Optional[CommissionTerms]:
        profile = self.profiles.get(seller_id)
        if profile is None or profile.contract_id is None:
            return None
        contract = self.contracts[profile.contract_id]
        return self.commission_terms.get(contract.commission_terms_id) if contract.commission_terms_id else None

    def commission_amount(self, seller_id: str, net_sales: Money) -> Money:
        """REQ-FIN-005: commission = rate * net + fixed fee, floored at minimum."""
        terms = self.commission_for(seller_id)
        if terms is None:
            raise NotFoundError(f"No commission terms for seller {seller_id}")
        if terms.currency != net_sales.currency.code:
            raise InvariantViolationError(
                "GOV-05",
                f"Commission terms are in {terms.currency}, settlement is {net_sales.currency}",
            )
        amount = (net_sales * terms.rate).round_to_currency()
        if terms.fixed_fee is not None:
            amount = amount + terms.fixed_fee
        if terms.minimum_fee is not None and amount < terms.minimum_fee:
            amount = terms.minimum_fee
        return amount

    def _require_profile(self, seller_id: str) -> SellerProfile:
        profile = self.profiles.get(seller_id)
        if profile is None:
            raise NotFoundError(f"Seller {seller_id} not found")
        return profile

    def _require_case(self, case_id: str) -> OnboardingCase:
        case = self.cases.get(case_id)
        if case is None:
            raise NotFoundError(f"Onboarding case {case_id} not found")
        return case


U07.owns("seller_profile", "Commercial marketplace relationship and seller status; legal party is U01", "U07.SellerService.register_seller")
U07.owns("onboarding_case", "Seller verification case with KYB evidence and decision audit", "U07.SellerService.open_onboarding")
U07.owns("seller_contract", "Seller agreement: territories, channels, catalog permissions, status", "U07.SellerService.sign_contract", read_projections=["U04.offer"])
U07.owns("commission_terms", "Commission rate, fixed and minimum fees with validity", "U07.SellerService.sign_contract", read_projections=["U09.settlement"])
