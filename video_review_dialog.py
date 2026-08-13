"""video_review_dialog.py
=========================
In-app annotated video review for pendulastic_app.py's PostProcessingPanel.
See docs/superpowers/specs/2026-08-12-annotated-video-review-design.md for
the full design.
"""
from __future__ import annotations


def _splice_from(old: list, start_idx: int, new: list, pad_value) -> list:
    """Return old[:start_idx] + new, with new padded (using pad_value) or
    truncated so the result is always exactly len(old) items long. Never
    mutates old or new. This guards against a retrack returning a short or
    long suffix silently desyncing frame-index-to-array-index alignment --
    see design spec S4 point 1."""
    target_len = len(old) - start_idx
    adjusted = list(new[:target_len])
    if len(adjusted) < target_len:
        adjusted.extend([pad_value] * (target_len - len(adjusted)))
    return list(old[:start_idx]) + adjusted
