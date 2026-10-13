"""U11 Customer Experience: search projections, reviews and support cases."""

from datetime import datetime, timedelta

import pytest

from marketplace.kernel import AuthorizationContext
from marketplace.kernel.errors import InvariantViolationError, NotFoundError, ValidationError
from marketplace.universes.u11_u15.services import ExperienceService

pytestmark = pytest.mark.unit


@pytest.fixture
def service():
    return ExperienceService()


@pytest.fixture
def buyer():
    return AuthorizationContext(
        actor_id="buyer-1",
        actor_type="person",
        roles=frozenset({"buyer"}),
        tenant_id="tenant-A",
    )


@pytest.fixture
def moderator():
    return AuthorizationContext(
        actor_id="mod-1",
        actor_type="person",
        roles=frozenset({"moderation.approver"}),
        tenant_id="tenant-A",
    )


@pytest.fixture
def agent():
    return AuthorizationContext(
        actor_id="agent-1",
        actor_type="person",
        roles=frozenset({"support.agent"}),
        tenant_id="tenant-A",
    )


class TestSearchProjection:
    def test_index_and_search(self, service):
        service.index_product("P1", "Wireless Headphones", {"category": "audio"}, 1)
        service.index_product("P2", "USB Cable", {"category": "accessory"}, 1)
        hits = service.search("headphones")
        assert [h["product_id"] for h in hits] == ["P1"]

    def test_search_without_terms_returns_all(self, service):
        service.index_product("P1", "Wireless Headphones", {"category": "audio"}, 1)
        service.index_product("P2", "USB Cable", {"category": "accessory"}, 1)
        assert len(service.search("")) == 2

    def test_facet_filters_applied(self, service):
        service.index_product("P1", "Wireless Headphones", {"category": "audio"}, 1)
        service.index_product("P2", "USB Cable", {"category": "accessory"}, 1)
        hits = service.search("", filters={"category": "audio"})
        assert [h["product_id"] for h in hits] == ["P1"]

    def test_ranking_signals_are_observable(self, service):
        service.index_product("P1", "Wireless Headphones", {}, 1)
        hits = service.search("wireless")
        assert "ranking_signals" in hits[0]
        assert hits[0]["ranking_signals"]["text_score"] == 1.0

    def test_projection_is_stale_until_rebuilt(self, service):
        service.index_product("P1", "Headphones", {}, 1)
        with pytest.raises(InvariantViolationError):
            service.assert_projection_fresh("P1", 2)
        service.index_product("P1", "Headphones", {}, 2)
        assert service.assert_projection_fresh("P1", 2) is None

    def test_freshness_check_requires_projection(self, service):
        with pytest.raises(NotFoundError):
            service.assert_projection_fresh("MISSING", 1)

    def test_rebuild_from_canonical_facts(self, service):
        rebuilt = service.rebuild_from(
            [
                {"product_id": "P1", "title": "A", "facets": {}, "version": 3},
                {"product_id": "P2", "title": "B", "facets": {}, "version": 4},
            ]
        )
        assert rebuilt == 2
        assert service.assert_projection_fresh("P1", 3) is None

    def test_search_respects_limit(self, service):
        for i in range(10):
            service.index_product(f"P{i}", "Headphones", {}, 1)
        assert len(service.search("headphones", limit=3)) == 3


class TestReviews:
    def test_submit_and_publish(self, service, buyer, moderator):
        review = service.submit_review(buyer, "P1", 5, "Great", "Loved it", verified_purchase=True)
        assert review.status == "submitted"
        published = service.publish_review(moderator, review.review_id)
        assert published.status == "published"

    def test_hide_records_reason(self, service, buyer, moderator):
        review = service.submit_review(buyer, "P1", 1, "Bad", "Broken")
        hidden = service.hide_review(moderator, review.review_id, "spam")
        assert hidden.status == "hidden"
        assert hidden.moderation_outcome == "spam"

    def test_rating_out_of_range_rejected(self, service, buyer):
        with pytest.raises(ValidationError):
            service.submit_review(buyer, "P1", 6, "x", "y")
        with pytest.raises(ValidationError):
            service.submit_review(buyer, "P1", 0, "x", "y")

    def test_buyer_role_required(self, service, moderator):
        with pytest.raises(Exception):
            service.submit_review(moderator, "P1", 5, "x", "y")

    def test_moderation_role_required(self, service, buyer, moderator):
        review = service.submit_review(buyer, "P1", 5, "x", "y")
        with pytest.raises(Exception):
            service.publish_review(buyer, review.review_id)

    def test_unknown_review_raises(self, service, moderator):
        with pytest.raises(NotFoundError):
            service.publish_review(moderator, "nope")


class TestSupportCases:
    def test_open_case_sets_sla(self, service, buyer):
        case = service.open_case(buyer, "buyer-1", "Order never arrived", sla_hours=48)
        assert case.status == "open"
        opened = datetime.fromisoformat(case.opened_at.replace("Z", "+00:00"))
        due = datetime.fromisoformat(case.sla_due_at.replace("Z", "+00:00"))
        assert due - opened == timedelta(hours=48)

    def test_escalate_assigns_agent(self, service, buyer, agent):
        case = service.open_case(buyer, "buyer-1", "Problem")
        escalated = service.escalate_case(agent, case.case_id)
        assert escalated.status == "escalated"
        assert escalated.assigned_agent == "agent-1"

    def test_agent_role_required(self, service, buyer):
        case = service.open_case(buyer, "buyer-1", "Problem")
        with pytest.raises(Exception):
            service.escalate_case(buyer, case.case_id)

    def test_unknown_case_raises(self, service, agent):
        with pytest.raises(NotFoundError):
            service.escalate_case(agent, "nope")


class TestU11ConceptOwnership:
    def test_concepts_owned(self):
        from marketplace.kernel.registry import CONCEPTS

        owned = {c.concept for c in CONCEPTS.all() if c.owner_universe == "U11"}
        assert {"search_projection", "review", "support_case"} <= owned
