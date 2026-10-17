from safety.health import HealthRegistry
from core.dedupe import DedupeRegistry, task_fingerprint
from _helpers import apply_dedupe


def test_verify_failures_degrade_and_success_recovers(tmp_path):
    h = HealthRegistry(base_cooldown=0, circuit_limit=99, state_file=tmp_path / "health.json")
    h.register("w", 1)
    for i in range(3):
        h.verify_failure("w", f"bad verify {i}")
    st = h.state("w")
    assert st.consecutive_verify_failures == 3
    assert st.status == "DEGRADED"
    assert st.cooldown_until > 0
    h.verify_success("w")
    assert h.state("w").consecutive_verify_failures == 0
    assert h.state("w").status == "AVAILABLE"


def test_verify_failure_penalizes_score(tmp_path):
    h = HealthRegistry(base_cooldown=0, circuit_limit=99, state_file=tmp_path / "health.json")
    h.register("w", 1)
    before = h.score("w")
    h.verify_failure("w", "bad verify")
    h.verify_failure("w", "bad verify")
    after_two = h.score("w")
    assert after_two < before
    h.verify_failure("w", "bad verify")
    assert h.state("w").status == "DEGRADED"


def test_task_fingerprint_ignores_attempt_and_id():
    a = {"id": "1", "project": "p", "message": "fix", "files": ["b.py", "a.py"], "verify": ["pytest"], "run": [], "attempts": 1}
    b = {"id": "2", "project": "p", "message": "fix", "files": ["a.py", "b.py"], "verify": ["pytest"], "run": [], "attempts": 9}
    assert task_fingerprint(a) == task_fingerprint(b)


def test_dedupe_check_and_mark(tmp_path):
    d = DedupeRegistry(tmp_path / "dedupe.json")
    fp = "abc"
    assert d.check_and_mark(fp, "task-1")
    assert not d.check_and_mark(fp, "task-2")
    d.forget(fp)
    assert d.check_and_mark(fp, "task-3")


def test_failed_task_is_not_deduped_and_can_run_again(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    class Result:
        def __init__(self, ok):
            self.ok = ok

    class FakeRuntime:
        def __init__(self):
            self.results = iter([Result(False), Result(True)])

        def process(self, raw):
            return next(self.results)

    apply_dedupe(FakeRuntime)
    runtime = FakeRuntime()
    raw = {"id": "task-1", "project": "p", "message": "fix", "files": ["a.py"]}

    first = runtime.process(raw)
    assert first.ok is False
    fp = task_fingerprint(raw)
    assert not runtime.dedupe.contains(fp)

    second = runtime.process(raw)
    assert second.ok is True
    assert runtime.dedupe.contains(fp)
