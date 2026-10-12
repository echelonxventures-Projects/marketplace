"""U02 — Universal Commerce.

Canonical concepts owned: business_model, trading_relationship, channel,
commerce_context.

Supports B2C, B2B, C2C, D2C, B2B2C, C2B, wholesale, first-party retail,
third-party marketplace, dropship, procurement, cross-border, subscription and
contract buying (master spec §1.1). Models share one commerce foundation while
retaining their own policies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from marketplace.kernel import AuthorizationContext, generate
from marketplace.kernel.errors import NotFoundError, ValidationError
from marketplace.universes._base import U02

BUSINESS_MODELS = frozenset(
    {
        "b2c", "b2b", "c2c", "d2c", "b2b2c", "c2b",
        "wholesale", "first_party_retail", "third_party_marketplace",
        "dropship", "procurement", "cross_border", "local", "subscription",
        "digital_goods", "services", "rental", "auction", "rfq",
    }
)


@dataclass
class BusinessModel:
    model_id: str
    code: str
    seller_of_record: str  # platform | seller
    agency_role: str  # agent | principal
    contract_required: bool = False
    tax_liability: str = "seller"
    default_policies: dict = field(default_factory=dict)
    status: str = "active"


@dataclass
class TradingRelationship:
    """A governed commercial relationship between two parties."""

    relationship_id: str
    from_party_id: str
    to_party_id: str
    model_code: str
    status: str = "active"
    terms: dict = field(default_factory=dict)
    valid_from: str = ""
    valid_until: Optional[str] = None


@dataclass
class Channel:
    channel_id: str
    code: str
    kind: str  # web | mobile | kiosk | social | voice | api | b2b_portal
    status: str = "active"


@dataclass
class CommerceContext:
    """The commercial environment in which a transaction runs.

    Carried through every downstream evaluation so that pricing, tax,
    eligibility and fulfillment rules resolve consistently (master spec §2.1).
    """

    business_model: str
    channel: str
    territory: str
    currency: str
    buyer_class: str = "consumer"
    language: str = "en"
    is_cross_border: bool = False
    contract_id: Optional[str] = None

    def __post_init__(self) -> None:
        if self.business_model not in BUSINESS_MODELS:
            raise ValidationError(
                f"Unknown business model {self.business_model!r}; "
                f"supported: {sorted(BUSINESS_MODELS)}"
            )


class CommerceService:
    def __init__(self) -> None:
        self.models: dict[str, BusinessModel] = {}
        self.relationships: dict[str, TradingRelationship] = {}
        self.channels: dict[str, Channel] = {}

    def define_model(
        self,
        authz: AuthorizationContext,
        code: str,
        *,
        seller_of_record: str = "seller",
        agency_role: str = "agent",
        contract_required: bool = False,
        tax_liability: str = "seller",
    ) -> BusinessModel:
        authz.requires_role("commerce.admin")
        if seller_of_record not in {"platform", "seller"}:
            raise ValidationError(f"Invalid seller_of_record {seller_of_record!r}")
        if agency_role not in {"agent", "principal"}:
            raise ValidationError(f"Invalid agency_role {agency_role!r}")
        model = BusinessModel(
            model_id=generate("evidence"),
            code=code,
            seller_of_record=seller_of_record,
            agency_role=agency_role,
            contract_required=contract_required,
            tax_liability=tax_liability,
        )
        self.models[code] = model
        return model

    def establish_relationship(
        self,
        authz: AuthorizationContext,
        from_party_id: str,
        to_party_id: str,
        model_code: str,
        *,
        valid_from: str,
        valid_until: Optional[str] = None,
        terms: Optional[dict] = None,
    ) -> TradingRelationship:
        authz.requires_role("commerce.writer")
        if model_code not in self.models:
            raise NotFoundError(f"Business model {model_code!r} is not defined")
        relationship = TradingRelationship(
            relationship_id=generate("evidence"),
            from_party_id=from_party_id,
            to_party_id=to_party_id,
            model_code=model_code,
            terms=dict(terms or {}),
            valid_from=valid_from,
            valid_until=valid_until,
        )
        self.relationships[relationship.relationship_id] = relationship
        return relationship

    def register_channel(self, authz: AuthorizationContext, code: str, kind: str) -> Channel:
        authz.requires_role("commerce.admin")
        if kind not in {"web", "mobile", "kiosk", "social", "voice", "api", "b2b_portal"}:
            raise ValidationError(f"Unknown channel kind {kind!r}")
        channel = Channel(channel_id=generate("evidence"), code=code, kind=kind)
        self.channels[code] = channel
        return channel

    def relationship_between(self, from_party_id: str, to_party_id: str) -> Optional[TradingRelationship]:
        return next(
            (
                r
                for r in self.relationships.values()
                if r.from_party_id == from_party_id and r.to_party_id == to_party_id and r.status == "active"
            ),
            None,
        )


U02.owns("business_model", "Commercial model, seller-of-record and agency role semantics", "U02.CommerceService.define_model")
U02.owns("trading_relationship", "Governed commercial relationship between parties", "U02.CommerceService.establish_relationship")
U02.owns("channel", "Commerce channel definition and status", "U02.CommerceService.register_channel")
