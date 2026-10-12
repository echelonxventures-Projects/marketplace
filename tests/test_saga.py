"""Idempotency and saga compensation (GOV-13, INV-02/INV-03)."""

import threading

import pytest

from marketplace.kernel.saga import IdempotencyStore, Saga, SagaStep

pytestmark = pytest.mark.concurrency


class TestIdempotencyStore:
    def make_store(self):
        store = IdempotencyStore()
        store.count = 0
        return store

    def op(self, store):
        def _op():
            store.count += 1
            return {"value": store.count}
        return _op

    def test_first_execution_runs_once(self):
        store = self.make_store()
        assert store.execute("k", self.op(store)) == {"value": 1}
        assert store.count == 1

    def test_duplicate_key_returns_recorded_outcome(self):
        store = self.make_store()
        store.execute("k", self.op(store))
        assert store.execute("k", self.op(store)) == {"value": 1}
        assert store.count == 1  # side effect not repeated

    def test_distinct_keys_each_execute(self):
        store = self.make_store()
        store.execute("a", self.op(store))
        assert store.execute("b", self.op(store)) == {"value": 2}

    def test_empty_key_rejected(self):
        store = self.make_store()
        with pytest.raises(ValueError):
            store.execute("", self.op(store))

    def test_failure_is_not_recorded(self):
        store = self.make_store()

        def boom():
            raise RuntimeError("nope")

        with pytest.raises(RuntimeError):
            store.execute("k", boom)
        assert not store.has("k")
        # a later attempt may run again
        assert store.execute("k", self.op(store)) == {"value": 1}

    def test_has_and_outcome_of(self):
        store = self.make_store()
        assert not store.has("k")
        store.execute("k", self.op(store))
        assert store.has("k")
        assert store.outcome_of("k") == {"value": 1}
        assert store.outcome_of("missing") is None

    def test_concurrent_duplicate_waits_for_outcome(self):
        store = self.make_store()
        barrier = threading.Event()
        started = threading.Event()

        def slow():
            started.set()
            barrier.wait(2)
            store.count += 1
            return {"value": store.count}

        def worker():
            return store.execute("k", slow)

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        assert started.wait(2)
        barrier.set()
        for t in threads:
            t.join(5)
        # the operation ran exactly once regardless of thread count
        assert store.count == 1
        assert store.outcome_of("k") == {"value": 1}


class TestSaga:
    def test_all_steps_complete(self):
        saga = Saga("s", [
            SagaStep("one", lambda ctx: {"a": 1}),
            SagaStep("two", lambda ctx: {"b": ctx["a"] + 1}),
        ])
        ex = saga.run("id-1")
        assert ex.state == "completed"
        assert ex.context == {"a": 1, "b": 2}
        assert ex.completed_steps == ["one", "two"]

    def test_failure_compensates_in_reverse(self):
        compensated = []
        saga = Saga("s", [
            SagaStep("one", lambda ctx: {"a": 1}, compensate=lambda ctx, _: compensated.append("one")),
            SagaStep("two", lambda ctx: {"b": 2}, compensate=lambda ctx, _: compensated.append("two")),
            SagaStep("boom", lambda ctx: (_ for _ in ()).throw(RuntimeError("fail"))),
        ])
        ex = saga.run("id-2")
        assert ex.state == "compensated"
        assert compensated == ["two", "one"]  # reverse order
        assert "boom" not in ex.completed_steps

    def test_failure_without_compensation_is_failed(self):
        saga = Saga("s", [SagaStep("one", lambda ctx: {"a": 1}), SagaStep("boom", lambda ctx: 1 / 0)])
        ex = saga.run("id-3")
        assert ex.state == "failed"
        assert "boom" in (ex.error or "")

    def test_retry_then_succeed(self):
        attempts = []
        def flaky(ctx):
            attempts.append(1)
            if len(attempts) < 2:
                raise RuntimeError("transient")
            return {"ok": True}
        saga = Saga("s", [SagaStep("flaky", flaky, retries=1)])
        ex = saga.run("id-4")
        assert ex.state == "completed"
        assert len(attempts) == 2

    def test_retries_exhausted_compensates(self):
        def always():
            raise RuntimeError("permanent")
        saga = Saga("s", [
            SagaStep("prep", lambda ctx: {"x": 1}, compensate=lambda ctx, _: ctx.update(compensated=True)),
            SagaStep("always", always, retries=2),
        ])
        ex = saga.run("id-5")
        assert ex.state == "compensated"
        assert ctx_view(ex) is not None

    def test_execution_is_retrievable(self):
        saga = Saga("s", [SagaStep("one", lambda ctx: {})])
        saga.run("id-6", {"seed": 1})
        assert saga.execution("id-6").context == {"seed": 1}


def ctx_view(ex):
    return ex.context
