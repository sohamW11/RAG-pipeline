"""Change-detection tests (pure strategy over two signatures)."""

from crawler.services.change_detection import (
    ChangeDetector,
    ChangeSignature,
    ChangeType,
)


def test_new_when_no_prior_version():
    decision = ChangeDetector().evaluate(None, ChangeSignature(sha256="a"))
    assert decision.change_type is ChangeType.NEW
    assert decision.should_download is True


def test_unchanged_when_top_signal_matches():
    existing = ChangeSignature(sha256="a", etag="e1")
    candidate = ChangeSignature(sha256="a", etag="e2")  # sha256 wins, etag ignored
    decision = ChangeDetector().evaluate(existing, candidate)
    assert decision.change_type is ChangeType.UNCHANGED
    assert decision.should_download is False


def test_changed_when_hash_differs():
    decision = ChangeDetector().evaluate(
        ChangeSignature(sha256="a"), ChangeSignature(sha256="b")
    )
    assert decision.change_type is ChangeType.CHANGED
    assert decision.should_download is True


def test_falls_through_to_weaker_signal():
    existing = ChangeSignature(etag="e1")
    candidate = ChangeSignature(etag="e1")  # only etag comparable, and it matches
    decision = ChangeDetector().evaluate(existing, candidate)
    assert decision.change_type is ChangeType.UNCHANGED


def test_no_comparable_signals_downloads_to_be_safe():
    decision = ChangeDetector().evaluate(
        ChangeSignature(sha256="a"), ChangeSignature(etag="e1")
    )
    assert decision.change_type is ChangeType.CHANGED
    assert decision.should_download is True
