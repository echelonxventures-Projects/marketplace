"""Composition root: one place where every universe service is instantiated.

Importing this module also imports all universes, which registers their state
machines, canonical concepts and capabilities (registration happens at import,
the same way ``tests/conftest.py`` drives it).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Optional

# Importing the universes registers their concepts/machines/capabilities.
import marketplace.universes.u01_identity  # noqa: F401
import marketplace.universes.u02_commerce  # noqa: F401
import marketplace.universes.u03_catalog  # noqa: F401
import marketplace.universes.u04_offer  # noqa: F401
import marketplace.universes.u05_pricing  # noqa: F401
import marketplace.universes.u06_inventory  # noqa: F401
import marketplace.universes.u07_seller  # noqa: F401
import marketplace.universes.u08_order  # noqa: F401
import marketplace.universes.u09_finance  # noqa: F401
import marketplace.universes.u10_fulfillment  # noqa: F401
import marketplace.universes.u11_u15  # noqa: F401
import marketplace.universes.u12_intelligence.constitution  # noqa: F401
import marketplace.universes.u12_intelligence.constraints  # noqa: F401
import marketplace.universes.u12_intelligence.entities  # noqa: F401
import marketplace.universes.u12_intelligence.evaluation  # noqa: F401
import marketplace.universes.u12_intelligence.governance  # noqa: F401
import marketplace.universes.u12_intelligence.monitoring  # noqa: F401
import marketplace.universes.u12_intelligence.review  # noqa: F401
import marketplace.universes.u12_intelligence.scenarios  # noqa: F401
import marketplace.universes.u12_intelligence.traceability  # noqa: F401

from marketplace.kernel import AuthorizationContext
from marketplace.kernel.identifiers import IdentifierMappingStore
from marketplace.universes.u01_identity.service import IdentityService, TokenAuthorizationResolver
from marketplace.universes.u03_catalog.service import CatalogService, SchemaRegistry
from marketplace.universes.u04_offer.service import OfferService
from marketplace.universes.u05_pricing.service import PricingService
from marketplace.universes.u06_inventory.service import InventoryService
from marketplace.universes.u07_seller.service import SellerService
from marketplace.universes.u08_order.service import OrderService
from marketplace.universes.u09_finance.ledger import LedgerService
from marketplace.universes.u09_finance.payments import DeterministicPaymentAdapter, PaymentService
from marketplace.universes.u11_u15.services import (
    ExperienceService,
    GovernanceService,
    IntelligenceService,
    PlatformService,
    TrustService,
)

DEFAULT_TENANT = "tenant-A"
DEFAULT_CURRENCY = "USD"


@dataclass
class Runtime:
    """The whole wired platform for one local test process.

    Services are stateful and in-memory by design (the kernel's
    ``InMemoryStore`` reference adapter); restarting the server resets state and
    re-seeds the demo dataset.
    """

    tenant_id: str = DEFAULT_TENANT
    currency: str = DEFAULT_CURRENCY
    identity: IdentityService = field(default_factory=IdentityService)
    schemas: SchemaRegistry = field(default_factory=SchemaRegistry)
    mappings: IdentifierMappingStore = field(default_factory=IdentifierMappingStore)
    catalog: CatalogService = field(default_factory=lambda: CatalogService(
        SchemaRegistry(), IdentifierMappingStore()
    ))
    pricing: PricingService = field(default_factory=PricingService)
    inventory: InventoryService = field(default_factory=InventoryService)
    offers: OfferService = field(default_factory=OfferService)
    sellers: SellerService = field(default_factory=SellerService)
    orders: OrderService = field(default_factory=OrderService)
    payments: PaymentService = field(default_factory=PaymentService)
    ledger: LedgerService = field(default_factory=LedgerService)
    experience: ExperienceService = field(default_factory=ExperienceService)
    trust: TrustService = field(default_factory=TrustService)
    governance: GovernanceService = field(default_factory=GovernanceService)
    intelligence: IntelligenceService = field(default_factory=IntelligenceService)
    platform: PlatformService = field(default_factory=PlatformService)
    tokens: dict[str, str] = field(default_factory=dict)
    seed_report: Optional[dict] = None
    _lock: threading.RLock = field(default_factory=threading.RLock)

    def __post_init__(self) -> None:
        # Reuse the registries the catalog was actually constructed with.
        self.catalog.schemas = self.schemas
        self.catalog.mappings = self.mappings
        self.resolver = TokenAuthorizationResolver(self.identity)
        # The demo payment provider always succeeds; it is registered up front
        # so checkout works out of the box. Other providers can be added later.
        self.payments.register_provider("demo", DeterministicPaymentAdapter())

    # -- authentication ---------------------------------------------------

    def mint_token(self, identity_id: str, tenant_id: str, roles: list[str]) -> str:
        """Build a bearer token in the resolver's reference format.

        The token encodes ``identity_id:tenant:roles``. The canonical
        ``TokenAuthorizationResolver`` validates the identity is active before
        the roles are trusted, so a minted token is only usable while the
        identity stays active.
        """

        return f"{identity_id}:{tenant_id}:{','.join(sorted(set(roles)))}"

    def resolve(self, token: Optional[str]) -> AuthorizationContext:
        """Resolve a bearer token into an AuthorizationContext.

        Raises ``AuthenticationError`` when the token is malformed or its
        identity is not active; the caller must deny rather than proceed
        (LAW-ARCH-07).
        """

        if not token:
            from marketplace.kernel.errors import AuthenticationError

            raise AuthenticationError("Missing bearer token")
        return self.resolver.resolve(token)

    def remember_token(self, key: str, token: str) -> None:
        with self._lock:
            self.tokens[key] = token

    def token_for(self, key: str) -> Optional[str]:
        with self._lock:
            return self.tokens.get(key)


def build_runtime(*, seed: bool = True) -> Runtime:
    """Construct a wired runtime, optionally seeding the demo dataset."""

    runtime = Runtime()
    if seed:
        from .seed import seed_demo

        runtime.seed_report = seed_demo(runtime)
    return runtime
