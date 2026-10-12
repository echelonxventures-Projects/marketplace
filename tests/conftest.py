"""Shared test fixtures: deterministic clocks, authz contexts, helpers."""

import pytest

# Importing the universes registers their state machines, concepts and
# capabilities in the canonical registries (registration happens at import).
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
import marketplace.universes.u12_intelligence.traceability  # noqa: F401

from marketplace.kernel import AuthorizationContext


@pytest.fixture
def authz():
    """A privileged actor in the default tenant."""
    return AuthorizationContext(
        actor_id="actor-1",
        actor_type="person",
        roles=frozenset(
            {
                "party.writer",
                "party.admin",
                "inventory.writer",
                "inventory.feed",
                "pricing.writer",
                "constraint.writer",
                "decision.requester",
                "decision.owner",
                "decision.approver",
                "decision.reviewer",
                "decision.service",
                "execution.service",
                "model.governance",
                "consent.admin",
                "delegator",
                "inventory.feed",
            }
        ),
        tenant_id="tenant-A",
    )


@pytest.fixture
def other_tenant_authz():
    """An actor in a different tenant, for isolation tests."""
    return AuthorizationContext(
        actor_id="actor-2",
        actor_type="person",
        roles=frozenset({"party.writer"}),
        tenant_id="tenant-B",
    )


@pytest.fixture
def requester():
    return AuthorizationContext(
        actor_id="proposer",
        actor_type="person",
        roles=frozenset({"decision.requester", "decision.owner"}),
        tenant_id="tenant-A",
    )


@pytest.fixture
def approver():
    return AuthorizationContext(
        actor_id="approver",
        actor_type="person",
        roles=frozenset({"decision.approver"}),
        tenant_id="tenant-A",
    )


@pytest.fixture
def executor():
    return AuthorizationContext(
        actor_id="executor",
        actor_type="service",
        roles=frozenset({"execution.service"}),
        tenant_id="tenant-A",
    )


NOW = "2026-01-01T00:00:00+00:00"
THEN = "2026-06-01T00:00:00+00:00"
INTERVAL = (NOW, "2027-01-01T00:00:00+00:00")
