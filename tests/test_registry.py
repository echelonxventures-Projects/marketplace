"""Canonical registries and traceability (GOV-02/05/06, LAW-ARCH-02).

Asserts the constitutional guarantees that hold across the whole platform:
one write owner per concept, no duplicate canonical concepts, and that every
registered requirement traces to capabilities and tests.
"""

import pathlib

import pytest

from marketplace.kernel.errors import DuplicateCanonicalConceptError, NotFoundError
from marketplace.kernel.registry import CAPABILITIES, CONCEPTS, INVARIANTS, REQUIREMENTS
from marketplace.universes.u12_intelligence.constitution import umfdvi_coverage

pytestmark = pytest.mark.registry


class TestConceptRegistry:
    """LAW-ARCH-02 / GOV-02: exactly one universe owns each canonical concept."""

    def test_party_owned_by_u01(self):
        record = CONCEPTS.get("party")
        assert record.owner_universe == "U01"
        assert record.write_path.startswith("U01")

    def test_order_owned_by_u08(self):
        assert CONCEPTS.get("order").owner_universe == "U08"

    def test_duplicate_owner_returns_existing(self):
        record = CONCEPTS.get("party")
        from marketplace.kernel.registry import ConceptRecord

        same = ConceptRecord(
            concept="party", owner_universe="U01", description="d",
            identifier_prefix="party", write_path="U01.IdentityService.register_party",
        )
        assert CONCEPTS.register(same) is record

    def test_competing_owner_rejected(self):
        from marketplace.kernel.registry import ConceptRecord

        interloper = ConceptRecord(
            concept="party", owner_universe="U05", description="d",
            identifier_prefix="party", write_path="U05.PricingService",
        )
        with pytest.raises(DuplicateCanonicalConceptError):
            CONCEPTS.register(interloper)

    def test_assert_owner(self):
        assert CONCEPTS.assert_owner("party", "U01").concept == "party"
        with pytest.raises(DuplicateCanonicalConceptError):
            CONCEPTS.assert_owner("party", "U02")

    def test_unknown_concept(self):
        with pytest.raises(NotFoundError):
            CONCEPTS.get("not_a_concept")

    def test_concepts_of_universe(self):
        owned = CONCEPTS.concepts_of("U01")
        assert "party" in owned
        assert "identity" in owned

    def test_no_duplicate_concepts_detected(self):
        duplicates = CONCEPTS.detect_duplicates()
        # any overlap must be between concepts owned by the same universe
        for duplicate in duplicates:
            owners = {CONCEPTS.get(c).owner_universe for c in duplicate["concepts"]}
            assert len(owners) == 1, duplicate

    def test_decision_case_owned_by_u12(self):
        assert CONCEPTS.get("decision_case").owner_universe == "U12"


class TestRequirementRegistry:
    """GOV-06: requirements trace to capabilities and tests."""

    def test_requirement_registry_is_usable(self):
        from marketplace.kernel.registry import RequirementRecord

        record = RequirementRecord(
            requirement_id="REQ-TEST-001", domain="d", statement="s", source="t",
            capability_ids=["CAP-1"], test_ids=["test_x"],
        )
        try:
            assert REQUIREMENTS.register(record) is record
            assert REQUIREMENTS.get("REQ-TEST-001").trace_complete()
            assert REQUIREMENTS.get("REQ-TEST-001").implemented
        finally:
            REQUIREMENTS._unregister("REQ-TEST-001")

    def test_every_implemented_requirement_traces(self):
        # Non-vacuous: a registry that registered nothing would also have no
        # untraced requirements, so assert the population is real first.
        implemented = [r for r in REQUIREMENTS.all() if r.status == "implemented"]
        assert len(implemented) >= 50, (
            "the FDVI requirement population must be registered before this "
            "assertion means anything"
        )
        untraced = REQUIREMENTS.untraced()
        assert untraced == [], (
            "requirements claiming 'implemented' must trace to capabilities and "
            f"tests: {[r.requirement_id for r in untraced]}"
        )

    def test_no_requirement_is_in_a_hidden_state(self):
        """Every requirement is implemented-and-traced or an declared gap (GOV-06).

        A requirement may not sit in an unaccounted third state, such as
        ``implemented`` with no test trace, or an unrecognised status: that is
        how an unfinished requirement hides. This holds whether or not any gap
        currently exists.
        """
        all_reqs = list(REQUIREMENTS.all())
        assert all_reqs, "the requirement population must be registered first"
        recognised = set()
        for requirement in all_reqs:
            assert requirement.status in {"implemented", "not_implemented"}, (
                f"{requirement.requirement_id} has unrecognised status "
                f"{requirement.status!r}"
            )
            if requirement.status == "implemented":
                assert requirement.trace_complete(), (
                    f"{requirement.requirement_id} claims 'implemented' but "
                    "traces to no capability or test"
                )
            else:
                assert requirement.gap, requirement.requirement_id
            recognised.add(requirement.status)
        # The gap view and the status field must agree exactly.
        assert {r.requirement_id for r in REQUIREMENTS.gaps()} == {
            r.requirement_id for r in all_reqs if r.status == "not_implemented"
        }

    def test_get_unknown_requirement(self):
        with pytest.raises(NotFoundError):
            REQUIREMENTS.get("REQ-NOPE-000")

    def test_duplicate_registration_rejected(self):
        from marketplace.kernel.registry import RequirementRecord

        record = RequirementRecord(requirement_id="REQ-DUP-001", domain="d", statement="s", source="t")
        try:
            REQUIREMENTS.register(record)
            with pytest.raises(DuplicateCanonicalConceptError):
                REQUIREMENTS.register(record)
        finally:
            REQUIREMENTS._unregister("REQ-DUP-001")


class TestCapabilityRegistry:
    def test_capability_registry_is_usable(self):
        from marketplace.kernel.registry import CapabilityRecord

        record = CapabilityRecord(
            capability_id="CAP-TEST-001", domain="d", name="n", purpose="p",
            owner_universe="U01", implementing_module="m", disposition="implement",
        )
        try:
            assert CAPABILITIES.register(record) is record
            assert CAPABILITIES.get("CAP-TEST-001") == record
        finally:
            CAPABILITIES._unregister("CAP-TEST-001")

    def test_no_dead_capabilities(self):
        CAPABILITIES.assert_no_dead_capabilities()

    def test_fdvi_capabilities_are_registered(self):
        # Non-vacuous: assert the FDVI capability population is real.
        from marketplace.universes.u12_intelligence.traceability import DOMAIN

        fdvi = [c for c in CAPABILITIES.all() if c.domain == DOMAIN]
        assert len(fdvi) == 10
        for capability in fdvi:
            assert capability.owner_universe == "U12"
            assert capability.implementing_module.startswith("marketplace.")

    def test_get_unknown_capability(self):
        with pytest.raises(NotFoundError):
            CAPABILITIES.get("CAP-NOPE")


class TestInvariantRegistry:
    def test_invariant_registry_is_usable(self):
        from marketplace.kernel.registry import InvariantRecord

        record = InvariantRecord(
            invariant_id="INV-TEST-001", statement="s", enforced_by="e",
            test_ids=["test_y"],
        )
        try:
            assert INVARIANTS.register(record) is record
            assert INVARIANTS.get("INV-TEST-001") == record
        finally:
            INVARIANTS._unregister("INV-TEST-001")

    def test_invariants_are_registered(self):
        # Non-vacuous: the §27.2 and §34 populations must actually be present.
        ids = {i.invariant_id for i in INVARIANTS.all()}
        assert len(ids) >= 60
        for number in range(1, 21):
            assert f"INV-{number:03d}" in ids
        for number in range(1, 41):
            assert f"AI-{number:02d}" in ids

    def test_get_unknown_invariant(self):
        with pytest.raises(NotFoundError):
            INVARIANTS.get("INV-NOPE")


class TestConstitutionalLaws:
    def test_law_ids_use_canonical_prefix(self):
        from marketplace.constitution.laws import all_laws

        for law in all_laws():
            assert law.law_id.startswith("LAW-"), law.law_id

    def test_law_ids_are_unique(self):
        from marketplace.constitution.laws import all_laws

        ids = [law.law_id for law in all_laws()]
        assert len(ids) == len(set(ids))

    def test_get_law(self):
        from marketplace.constitution.laws import get_law

        law = get_law("LAW-GOV-01")
        assert law.law_id == "LAW-GOV-01"

    def test_unknown_law_raises(self):
        from marketplace.constitution.laws import get_law

        with pytest.raises(KeyError):
            get_law("LAW-NOPE-999")


class TestUmfdviConstitution:
    """Spec 030: every proposed UMFDVI law resolves to a canonical law."""

    def test_coverage_is_complete(self):
        coverage = umfdvi_coverage()
        assert len(coverage) == 40
        for proposed, canonical in coverage.items():
            assert proposed.startswith("UMFDVI-")
            assert canonical.startswith("LAW-")

    def test_coverage_resolves_registered_laws(self):
        from marketplace.constitution.laws import all_laws

        registered = {law.law_id for law in all_laws()}
        for canonical in umfdvi_coverage().values():
            assert canonical in registered, canonical


class TestEnforcementHooksResolve:
    """Every FDVI law's enforcement hook must name code that really exists.

    A law registry that points at modules which were never created is a
    traceability claim with no substance behind it, so this resolves each hook
    against the package and fails on any dangling path.
    """

    def test_no_dangling_enforcement_hooks(self):
        from marketplace.universes.u12_intelligence.constitution import (
            dangling_enforcements,
            enforcement_checks,
        )

        checks = enforcement_checks()
        assert len(checks) == 27
        dangling = dangling_enforcements()
        assert dangling == [], (
            "every LAW-UMFDVI enforcement hook must resolve to real code: "
            f"{[(c.law_id, c.enforcement) for c in dangling]}"
        )

    def test_law_enforcement_names_the_law_it_enforces(self):
        from marketplace.universes.u12_intelligence.constitution import (
            enforcement_checks,
        )

        for check in enforcement_checks():
            assert check.enforcement.startswith("universes."), check
            assert "u12_intelligence" in check.enforcement, check


class TestTraceabilityPointsAtRealTests:
    """GOV-06 / LAW-GOV-11: a traced test id must be a test that exists."""

    @pytest.fixture(scope="class")
    def collected(self):
        import subprocess

        out = subprocess.run(
            ["python3", "-m", "pytest", "--collect-only"],
            capture_output=True, text=True, check=True,
            cwd=str(pathlib.Path(__file__).resolve().parent.parent),
        ).stdout
        ids = {
            line.split()[0]
            for line in out.splitlines()
            if "::" in line and line.strip().startswith("tests/")
        }
        assert ids, "pytest collection produced no node ids"
        return ids

    def test_requirement_test_ids_are_real(self, collected):
        from marketplace.kernel.registry import REQUIREMENTS

        for requirement in REQUIREMENTS.all():
            for test_id in requirement.test_ids:
                assert test_id in collected, (
                    f"{requirement.requirement_id} traces to {test_id}, which does "
                    "not exist in the collected suite"
                )

    def test_invariant_test_ids_are_real(self, collected):
        from marketplace.kernel.registry import INVARIANTS

        for invariant in INVARIANTS.all():
            for test_id in invariant.test_ids:
                assert test_id in collected, (
                    f"{invariant.invariant_id} traces to {test_id}, which does "
                    "not exist in the collected suite"
                )

    def test_capabilities_reference_registered_requirements(self):
        from marketplace.kernel.registry import CAPABILITIES, REQUIREMENTS

        known = {r.requirement_id for r in REQUIREMENTS.all()}
        for capability in CAPABILITIES.all():
            for requirement_id in capability.requirement_ids:
                assert requirement_id in known, (
                    f"{capability.capability_id} satisfies {requirement_id}, which "
                    "is not a registered requirement"
                )


class TestStateMachinesAreGoverned:
    """Every stateful concept has a registered machine (§7.1)."""

    def test_machines_registered(self):
        from marketplace.kernel.state import REGISTRY

        names = {m.name for m in REGISTRY.all()}
        assert "inventory.reservation" in names
        assert "fdvi.decision" in names
        assert "consent.lifecycle" in names

    def test_machine_concepts_align_with_owned_concepts(self):
        from marketplace.kernel.state import REGISTRY

        for machine in REGISTRY.all():
            # a machine's concept should exist in the concept registry
            try:
                CONCEPTS.get(machine.concept)
            except NotFoundError:
                pytest.fail(f"machine {machine.name} governs unregistered concept {machine.concept}")
