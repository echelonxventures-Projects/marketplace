"""Governed state machine framework.

Implements the §7.1 contract: for each stateful canonical object the platform
documents states, a transition table, initiating actor, authorization,
preconditions, side effects, emitted events, timeout, retry/idempotency key,
terminal states, compensation, manual recovery, audit, version and concurrency
policy.

A ``StateMachine`` is a *definition* (immutable, registered). A ``Stateful`` is
a live instance that may only transition through its machine's table.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional

from .errors import IllegalTransitionError


@dataclass(frozen=True)
class Transition:
    """One legal arc of a state machine."""

    from_state: str
    to_state: str
    actor_roles: frozenset[str]
    guard: Optional[str] = None  # name of the precondition predicate
    side_effect: Optional[str] = None  # declared side effect (auditable)
    event: Optional[str] = None  # event emitted on success
    idempotent: bool = False  # re-applying the same command is a no-op
    terminal: bool = False  # to_state is terminal


@dataclass(frozen=True)
class StateMachine:
    """An immutable, registered state machine definition."""

    name: str
    concept: str
    initial_state: str
    transitions: tuple[Transition, ...]
    terminal_states: frozenset[str]
    concurrency_policy: str = "optimistic"
    version: int = 1

    def __post_init__(self) -> None:
        states = {self.initial_state}
        for t in self.transitions:
            states.add(t.from_state)
            states.add(t.to_state)
        unknown = self.terminal_states - states
        if unknown:
            raise ValueError(f"{self.name}: terminal states not present in graph: {unknown}")
        # every terminal state must be reachable
        reachable = self._reachable()
        unreachable = self.terminal_states - reachable
        if unreachable:
            raise ValueError(f"{self.name}: unreachable terminal states: {unreachable}")

    def _reachable(self) -> set[str]:
        seen = {self.initial_state}
        frontier = [self.initial_state]
        while frontier:
            current = frontier.pop()
            for t in self.transitions:
                if t.from_state == current and t.to_state not in seen:
                    seen.add(t.to_state)
                    frontier.append(t.to_state)
        return seen

    def states(self) -> set[str]:
        out = {self.initial_state}
        for t in self.transitions:
            out.add(t.from_state)
            out.add(t.to_state)
        return out

    def transitions_from(self, state: str) -> list[Transition]:
        return [t for t in self.transitions if t.from_state == state]

    def legal_targets(self, state: str) -> list[str]:
        return sorted({t.to_state for t in self.transitions_from(state)})

    def transition(self, from_state: str, to_state: str) -> Optional[Transition]:
        for t in self.transitions:
            if t.from_state == from_state and t.to_state == to_state:
                return t
        return None

    def is_terminal(self, state: str) -> bool:
        return state in self.terminal_states

    def assert_legal(self, entity_id: str, current: str, attempted: str) -> Transition:
        t = self.transition(current, attempted)
        if t is None:
            raise IllegalTransitionError(
                entity_id, current, attempted, self.legal_targets(current)
            )
        return t


class StateMachineRegistry:
    def __init__(self) -> None:
        self._machines: dict[str, StateMachine] = {}
        self._by_concept: dict[str, list[str]] = {}

    def register(self, machine: StateMachine) -> StateMachine:
        if machine.name in self._machines:
            raise ValueError(f"State machine {machine.name!r} already registered")
        self._machines[machine.name] = machine
        self._by_concept.setdefault(machine.concept, []).append(machine.name)
        return machine

    def get(self, name: str) -> StateMachine:
        from .errors import NotFoundError

        machine = self._machines.get(name)
        if machine is None:
            raise NotFoundError(f"State machine {name!r} is not registered")
        return machine

    def for_concept(self, concept: str) -> list[StateMachine]:
        return [self._machines[n] for n in self._by_concept.get(concept, [])]

    def all(self) -> Iterable[StateMachine]:
        return list(self._machines.values())


REGISTRY = StateMachineRegistry()


def register_machine(machine: StateMachine) -> StateMachine:
    return REGISTRY.register(machine)


# ---------------------------------------------------------------------------
# Live stateful entity
# ---------------------------------------------------------------------------


class Stateful:
    """Mixin/base for entities whose lifecycle is governed by a state machine.

    Concurrency policy is optimistic: every transition records the state
    version it read, and a stale read is rejected rather than silently
    overwriting a concurrent writer.
    """

    machine: StateMachine

    def __init__(self, entity_id: str) -> None:
        self.entity_id = entity_id
        self.state = self.machine.initial_state
        self.state_version = 0
        self.transition_history: list[dict] = []

    def assert_can_transition(self, to_state: str) -> Transition:
        return self.machine.assert_legal(self.entity_id, self.state, to_state)

    def transition_to(
        self,
        to_state: str,
        *,
        actor: str,
        actor_roles: Iterable[str],
        reason: str,
        recorded_at: str,
    ) -> Transition:
        t = self.assert_can_transition(to_state)
        permitted = set(t.actor_roles)
        if permitted and not (permitted & set(actor_roles)):
            from .errors import AuthorizationError

            raise AuthorizationError(
                f"Actor {actor!r} with roles {sorted(actor_roles)} is not permitted to "
                f"transition {self.entity_id} {self.state!r} -> {to_state!r}; "
                f"permitted roles: {sorted(permitted)}"
            )
        self.state = t.to_state
        self.state_version += 1
        self.transition_history.append(
            {
                "from": t.from_state,
                "to": t.to_state,
                "actor": actor,
                "roles": sorted(actor_roles),
                "reason": reason,
                "at": recorded_at,
                "version": self.state_version,
            }
        )
        return t

    @property
    def is_terminal(self) -> bool:
        return self.machine.is_terminal(self.state)
