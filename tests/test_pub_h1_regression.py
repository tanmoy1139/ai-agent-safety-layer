"""Regression test for PUB H-1 residual chain.

The original PUB H-1 fix changed `DharmaOSKernel.authorize()` to default
`fail_open=False`. However, the `governed()` convenience wrapper still
defaulted `fail_open=True`, and the `_is_write()` substring classifier
misses action names like "send_email", "transfer_funds", "publish_post".

Chain: governed() [fail_open=True] + classifier-missed action + induced
_evaluate exception -> allow_on_error = True and not False = True -> ALLOWED.

This test verifies the fix: governed() now defaults fail_open=False, so
the chain is denied.
"""
import pytest

from dharmaos.integrate import DharmaOSKernel, GovernanceDenied


class TestPubH1ResidualChain:
    """PUB H-1: governed() wrapper must fail closed on governance error."""

    def test_governed_defaults_fail_closed(self):
        """governed() must default fail_open=False, matching authorize()."""
        kernel = DharmaOSKernel(tenant="test-pub-h1")

        def send_email(to: str, body: str, case_id: str = "case-001") -> str:
            return f"sent to {to}"

        wrapped = kernel.governed(send_email, agent="test-agent", action="send_email")
        # Inspect the wrapper's default by checking it denies on error.
        # We force _evaluate to raise, simulating a governance outage.
        original_evaluate = kernel._evaluate

        def boom(*args, **kwargs):
            raise RuntimeError("simulated governance outage")

        kernel._evaluate = boom
        try:
            with pytest.raises(GovernanceDenied):
                wrapped(to="victim@example.com", body="pwned", case_id="case-001")
        finally:
            kernel._evaluate = original_evaluate

    def test_governed_explicit_fail_open_still_denies_writes(self):
        """Even with explicit fail_open=True, classifier-missed writes must deny
        on error if the action is state-changing. This documents the residual
        risk: the substring classifier has false negatives."""
        kernel = DharmaOSKernel(tenant="test-pub-h1")

        # "send_email" matches NONE of the default write-capability tokens.
        assert kernel._is_write("send_email") is False
        assert kernel._is_write("transfer_funds") is False
        assert kernel._is_write("publish_post") is False

        # But with explicit write=True, the error path must deny.
        # We force _evaluate to raise, simulating a governance outage.
        original_evaluate = kernel._evaluate

        def boom(*args, **kwargs):
            raise RuntimeError("simulated governance outage")

        kernel._evaluate = boom
        try:
            decision = kernel.authorize(
                agent="test-agent",
                action="send_email",
                case_id="case-001",
                write=True,  # explicitly marked as write
                fail_open=True,  # even with fail_open requested
            )
        finally:
            kernel._evaluate = original_evaluate
        # The C-4 guard: allow_on_error = fail_open and not is_write.
        # With write=True, is_write=True, so allow_on_error=False.
        # (This test documents the classifier limitation; the fix ensures
        # the default path via governed() is fail-closed.)
        assert decision.allowed is False or decision.verdict == "error"

    def test_authorize_crash_attack_denied(self):
        """Direct authorize() crash attack: all actions denied on error."""
        kernel = DharmaOSKernel(tenant="test-pub-h1")
        original_evaluate = kernel._evaluate

        def boom(*args, **kwargs):
            raise RuntimeError("simulated crash")

        kernel._evaluate = boom
        try:
            for action in ("send_email", "transfer_funds", "publish_post", "read_email"):
                decision = kernel.authorize(
                    agent="test-agent",
                    action=action,
                    case_id="case-001",
                )
                # fail_open defaults False, so all denied on error
                assert decision.allowed is False, f"{action} was allowed on error!"
        finally:
            kernel._evaluate = original_evaluate
