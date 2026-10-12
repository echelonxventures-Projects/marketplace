"""State machine governance: legal transitions, role gating, terminal states."""

import pytest

from marketplace.kernel.errors import AuthorizationError, IllegalTransitionError, NotFoundError
from marketplace.kernel.state import REGISTRY, StateMachine, Stateful, Transition, register_machine

pytestmark = pytest.mark.state


def machine(**over):
    base = dict(
        name="test.sm",
        concept="test_concept",
        initial_state="open",
        transitions=(
            Transition("open", "closed", frozenset({"clerk"}), event="closed"),
            Transition("closed", "done", frozenset({"clerk"}), terminal=True),
        ),
        terminal_states=frozenset({"done"}),
    )
    base.update(over)
    return StateMachine(**base)


def entity_for(m: StateMachine, entity_id: str = "x") -> Stateful:
    """Build a live entity bound to ``m`` (machine must exist before __init__)."""

    class _Entity(Stateful):
        machine = m

    return _Entity(entity_id)


class TestMachineDefinitions:
    def test_terminal_states_must_exist_in_graph(self):
        with pytest.raises(ValueError):
            machine(terminal_states=frozenset({"nonexistent"}))

    def test_duplicate_registration_rejected(self):
        register_machine(machine(name="unique.sm"))
        with pytest.raises(ValueError):
            register_machine(machine(name="unique.sm"))

    def test_registry_lookup(self):
        m = register_machine(machine(name="lookup.sm"))
        assert REGISTRY.get("lookup.sm") == m

    def test_registry_missing_machine(self):
        with pytest.raises(NotFoundError):
            REGISTRY.get("does.not.exist")

    def test_unreachable_terminal_rejected(self):
        with pytest.raises(ValueError):
            machine(
                terminal_states=frozenset({"done", "island"}),
                transitions=(
                    Transition("open", "closed", frozenset({"clerk"})),
                    Transition("closed", "done", frozenset({"clerk"}), terminal=True),
                ),
            )


class TestLegalTransitions:
    def test_legal_transition_allowed(self):
        e = entity_for(machine())
        e.transition_to("closed", actor="a", actor_roles={"clerk"}, reason="r", recorded_at="t")
        assert e.state == "closed"

    def test_illegal_transition_rejected(self):
        e = entity_for(machine())
        with pytest.raises(IllegalTransitionError):
            e.transition_to("done", actor="a", actor_roles={"clerk"}, reason="r", recorded_at="t")

    def test_illegal_transition_reports_legal_targets(self):
        e = entity_for(machine())
        with pytest.raises(IllegalTransitionError) as exc:
            e.transition_to("bogus", actor="a", actor_roles={"clerk"}, reason="r", recorded_at="t")
        assert "closed" in str(exc.value)

    def test_role_gating(self):
        e = entity_for(machine())
        with pytest.raises(AuthorizationError):
            e.transition_to("closed", actor="a", actor_roles={"someone"}, reason="r", recorded_at="t")

    def test_terminal_blocks_further_transitions(self):
        e = entity_for(machine())
        e.transition_to("closed", actor="a", actor_roles={"clerk"}, reason="r", recorded_at="t")
        e.transition_to("done", actor="a", actor_roles={"clerk"}, reason="r", recorded_at="t")
        assert e.is_terminal
        with pytest.raises(IllegalTransitionError):
            e.transition_to("open", actor="a", actor_roles={"clerk"}, reason="r", recorded_at="t")

    def test_history_is_recorded(self):
        e = entity_for(machine())
        e.transition_to("closed", actor="alice", actor_roles={"clerk"}, reason="why", recorded_at="t")
        assert e.transition_history[0]["actor"] == "alice"
        assert e.transition_history[0]["reason"] == "why"
        assert e.state_version == 1


class TestRegisteredMachines:
    """The production machines must all be registered and internally consistent."""

    def test_all_machines_have_unique_names(self):
        names = [m.name for m in REGISTRY.all()]
        assert len(names) == len(set(names))

    def test_every_terminal_state_is_reachable(self):
        for m in REGISTRY.all():
            for terminal in m.terminal_states:
                assert not m.is_terminal(m.initial_state) or terminal == m.initial_state

    def test_decision_machine_covers_draft_to_completion(self):
        dm = REGISTRY.get("fdvi.decision")
        assert dm.initial_state == "draft"
        assert "approved" in dm.states()
        assert "completed" in dm.terminal_states

    def test_reservation_machine_initial_state(self):
        rm = REGISTRY.get("inventory.reservation")
        assert rm.initial_state == "requested"
        assert "committed" in rm.terminal_states
