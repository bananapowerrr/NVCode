"""Regression tests for fail-closed verification boundaries."""

from types import SimpleNamespace

from core.rp_verify import RPVerifyMixin


class _Log:
    def write(self, _message):
        return None


class _Worker:
    name = "test-worker"


class _Task:
    id = "task-verify-fail-closed"
    attempts = 1
    metadata = {}


class _Ctx:
    root = "."


class _Plan:
    commitable = True
    stage = ["src/example.py"]

    def describe(self):
        return "test-plan"


class _GitOpsCommitRaises:
    def commit(self, *_args, **_kwargs):
        raise RuntimeError("commit exploded")


class _Mixin(RPVerifyMixin):
    log = _Log()

    def _set_phase(self, *_args):
        return None

    def _emit(self, *_args, **_kwargs):
        return None

    def _verify_escalating(self, *_args, **_kwargs):
        return True, ""

    def _verification_engine_gate(self, *_args, **_kwargs):
        return True, ""

    def _reset_verify_fails(self, *_args):
        return None

    def _bump_verify_fails(self, *_args):
        return None

    def _diff_budget_gate(self, *_args, **_kwargs):
        return True


def test_git_commit_failure_is_not_done():
    result = SimpleNamespace(ok=True, stderr="")

    _Mixin()._phase_verify_and_commit(
        result=result,
        task=_Task(),
        ctx=_Ctx(),
        tests=None,
        worker=_Worker(),
        gitops=_GitOpsCommitRaises(),
        before_snapshot=None,
        plan=_Plan(),
        message="work",
        attempt_messages={},
    )

    assert result.ok is False
    assert "git_commit_failed" in result.stderr


class _GateRaisesMixin(_Mixin):
    def _verification_engine_gate(self, *_args, **_kwargs):
        raise RuntimeError("gate exploded")


def test_verification_gate_exception_is_fail_closed():
    result = SimpleNamespace(ok=True, stderr="")

    _GateRaisesMixin()._phase_verify_and_commit(
        result=result,
        task=_Task(),
        ctx=_Ctx(),
        tests=None,
        worker=_Worker(),
        gitops=None,
        before_snapshot=None,
        plan=None,
        message="work",
        attempt_messages={},
    )

    assert result.ok is False
    assert "verification_gate_exception" in result.stderr

class _VerifierRaisesMixin(_Mixin):
    def _verify_escalating(self, *_args, **_kwargs):
        raise RuntimeError("verifier exploded")


def test_verifier_exception_is_fail_closed():
    result = SimpleNamespace(ok=True, stderr="")

    _VerifierRaisesMixin()._phase_verify_and_commit(
        result=result,
        task=_Task(),
        ctx=_Ctx(),
        tests=None,
        worker=_Worker(),
        gitops=None,
        before_snapshot=None,
        plan=None,
        message="work",
        attempt_messages={},
    )

    assert result.ok is False
    assert "verification_exception" in result.stderr


class _GitOpsOk:
    def commit(self, *_args, **_kwargs):
        return "abc123"


def test_empty_stage_requires_no_commit():
    """TD-001: пустой stage — коммит не нужен, задача не должна падать."""
    result = SimpleNamespace(ok=True, stderr="")
    plan = _Plan()
    plan.stage = []

    _Mixin()._phase_verify_and_commit(
        result=result,
        task=_Task(),
        ctx=_Ctx(),
        tests=None,
        worker=_Worker(),
        gitops=_GitOpsOk(),
        before_snapshot=None,
        plan=plan,
        message="work",
        attempt_messages={},
    )

    assert result.ok is True
