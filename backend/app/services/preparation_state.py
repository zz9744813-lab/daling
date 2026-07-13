"""Track whether generated preparation artifacts match the uploaded outline.

The uploaded outline lives in ``Project.extra`` rather than a dedicated table.
This module keeps equally lightweight provenance beside it so replacing the
source never deletes a world bible or storyline, while callers can still tell
that those artifacts were generated from an older source.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Literal, Optional

ArtifactName = Literal["world_bible", "outline"]

OUTLINE_SOURCE_KEY = "_outline_source"
PREPARATION_PROVENANCE_KEY = "_preparation_provenance"
PREPARATION_STALE_KEY = "_preparation_stale"


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def outline_fingerprint(text: str) -> Optional[str]:
    """Return a stable digest for meaningful outline text."""
    normalized = text.strip()
    if not normalized:
        return None
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def outline_source(extra: dict[str, Any]) -> dict[str, Any]:
    """Return normalized current source metadata, including legacy projects."""
    raw = extra.get(OUTLINE_SOURCE_KEY)
    stored = dict(raw) if isinstance(raw, dict) else {}
    text = str(extra.get("outline_text") or "")
    digest = outline_fingerprint(text)
    try:
        revision = max(0, int(stored.get("revision") or (1 if digest else 0)))
    except (TypeError, ValueError):
        revision = 1 if digest else 0
    return {
        "present": digest is not None,
        "revision": revision,
        "sha256": digest,
        "filename": extra.get("outline_filename") or stored.get("filename"),
        "updated_at": stored.get("updated_at"),
    }


def record_outline_change(
    extra: dict[str, Any],
    *,
    text: str,
    filename: Optional[str],
    world_bible_exists: bool,
    outline_exists: bool,
    reason: str = "outline_replaced",
) -> tuple[dict[str, Any], bool]:
    """Store a new source and mark existing derived artifacts stale.

    Existing artifacts are only labelled stale; they are deliberately retained
    so an editor can compare, recover, or regenerate them explicitly.
    """
    updated = dict(extra)
    previous = outline_source(updated)
    new_text = text.strip()
    new_digest = outline_fingerprint(new_text)
    changed = previous["sha256"] != new_digest

    if changed:
        revision = int(previous["revision"]) + 1
        if previous["revision"] == 0:
            revision = 1
    else:
        revision = int(previous["revision"])

    if new_text:
        updated["outline_text"] = new_text
        updated["outline_filename"] = filename
    else:
        updated.pop("outline_text", None)
        updated.pop("outline_filename", None)

    updated[OUTLINE_SOURCE_KEY] = {
        "present": new_digest is not None,
        "revision": revision,
        "sha256": new_digest,
        "filename": filename,
        "updated_at": _utcnow_iso(),
    }

    if changed:
        stale_raw = updated.get(PREPARATION_STALE_KEY)
        stale = dict(stale_raw) if isinstance(stale_raw, dict) else {}
        common = {
            "stale": True,
            "reason": reason,
            "detected_at": _utcnow_iso(),
            "source_revision": revision,
            "previous_source_revision": previous["revision"],
            "source_sha256": new_digest,
            "previous_source_sha256": previous["sha256"],
        }
        if world_bible_exists:
            stale["world_bible"] = dict(common)
        if outline_exists:
            stale["outline"] = dict(common)
        updated[PREPARATION_STALE_KEY] = stale

    return updated, changed


def mark_artifact_fresh(
    extra: dict[str, Any],
    artifact: ArtifactName,
) -> dict[str, Any]:
    """Record the source revision used by a successfully generated artifact."""
    updated = dict(extra)
    source = outline_source(updated)
    raw_provenance = updated.get(PREPARATION_PROVENANCE_KEY)
    provenance = dict(raw_provenance) if isinstance(raw_provenance, dict) else {}
    previous = provenance.get(artifact)
    previous = dict(previous) if isinstance(previous, dict) else {}
    try:
        artifact_revision = max(0, int(previous.get("artifact_revision") or 0)) + 1
    except (TypeError, ValueError):
        artifact_revision = 1
    now = _utcnow_iso()
    provenance[artifact] = {
        "source_revision": source["revision"],
        "source_sha256": source["sha256"],
        "artifact_revision": artifact_revision,
        "generated_at": now,
        "updated_at": now,
        "last_change": "generated",
    }
    updated[PREPARATION_PROVENANCE_KEY] = provenance

    raw_stale = updated.get(PREPARATION_STALE_KEY)
    stale = dict(raw_stale) if isinstance(raw_stale, dict) else {}
    stale.pop(artifact, None)
    updated[PREPARATION_STALE_KEY] = stale
    return updated


def artifact_provenance_state(
    extra: dict[str, Any],
    artifact: ArtifactName,
    *,
    exists: bool,
) -> dict[str, Any]:
    """Return normalized version/provenance metadata for a generated artifact.

    Older projects predate explicit artifact revisions.  They are exposed as
    revision 1 when the artifact exists so clients can still use optimistic
    edit checks without forcing a migration of every ``Project.extra`` value.
    """
    raw_provenance = extra.get(PREPARATION_PROVENANCE_KEY)
    provenance_map = raw_provenance if isinstance(raw_provenance, dict) else {}
    raw = provenance_map.get(artifact)
    stored = dict(raw) if isinstance(raw, dict) else {}
    try:
        revision = max(0, int(stored.get("artifact_revision") or 0))
    except (TypeError, ValueError):
        revision = 0
    if exists and revision == 0:
        revision = 1
    return {
        "artifact_revision": revision,
        "source_revision": stored.get("source_revision"),
        "source_sha256": stored.get("source_sha256"),
        "generated_at": stored.get("generated_at"),
        "updated_at": stored.get("updated_at") or stored.get("generated_at"),
        "last_change": stored.get("last_change") or ("generated" if exists else None),
    }


def record_artifact_edit(
    extra: dict[str, Any],
    artifact: ArtifactName,
    *,
    exists: bool,
    change: str = "manual_edit",
) -> dict[str, Any]:
    """Advance an artifact revision after a guarded manual structure edit."""
    updated = dict(extra)
    source = outline_source(updated)
    current = artifact_provenance_state(updated, artifact, exists=exists)
    raw_provenance = updated.get(PREPARATION_PROVENANCE_KEY)
    provenance = dict(raw_provenance) if isinstance(raw_provenance, dict) else {}
    now = _utcnow_iso()
    provenance[artifact] = {
        **current,
        "artifact_revision": int(current["artifact_revision"]) + 1,
        "source_revision": (
            current.get("source_revision")
            if current.get("source_revision") is not None
            else source["revision"]
        ),
        "source_sha256": (
            current.get("source_sha256")
            if current.get("source_sha256") is not None
            else source["sha256"]
        ),
        "updated_at": now,
        "last_change": change,
    }
    updated[PREPARATION_PROVENANCE_KEY] = provenance
    return updated


def artifact_stale_state(
    extra: dict[str, Any],
    artifact: ArtifactName,
    *,
    exists: bool,
) -> dict[str, Any]:
    """Return explicit and provenance-derived staleness for one artifact."""
    if not exists:
        return {"stale": False, "reason": None}

    raw_stale = extra.get(PREPARATION_STALE_KEY)
    stale_map = raw_stale if isinstance(raw_stale, dict) else {}
    explicit = stale_map.get(artifact)
    if isinstance(explicit, dict) and explicit.get("stale"):
        return dict(explicit)

    source = outline_source(extra)
    raw_provenance = extra.get(PREPARATION_PROVENANCE_KEY)
    provenance_map = raw_provenance if isinstance(raw_provenance, dict) else {}
    provenance = provenance_map.get(artifact)
    if isinstance(provenance, dict) and (
        provenance.get("source_revision") != source["revision"]
        or provenance.get("source_sha256") != source["sha256"]
    ):
        return {
            "stale": True,
            "reason": "outline_source_mismatch",
            "source_revision": source["revision"],
            "artifact_source_revision": provenance.get("source_revision"),
            "source_sha256": source["sha256"],
            "artifact_source_sha256": provenance.get("source_sha256"),
        }

    return {"stale": False, "reason": None}


__all__ = [
    "artifact_provenance_state",
    "artifact_stale_state",
    "mark_artifact_fresh",
    "outline_fingerprint",
    "outline_source",
    "record_artifact_edit",
    "record_outline_change",
]
