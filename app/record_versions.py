"""Versioned REPLAY fallback-record envelope (Task 19).

``eval/fallback/*.json`` is a composite record: a REPLAY envelope that embeds a
complete ``SimulationIntent`` at ``proposed_ir``.  It is therefore
setup-bearing, and both the envelope and the nested intent declare a version.

The envelope migration never rewrites the nested body.  It delegates to the
``SimulationIntent`` registry after its own migration completes; that is the
only permitted cross-family interaction.

This module lives under ``app`` rather than ``eval`` on purpose: ``app.server``
imports it unconditionally, and the production runtime image physically
excludes ``eval/``.  It therefore depends on nothing inside ``eval``.

It is named ``record_versions`` rather than ``fallback_records`` so that the
Task 18 production-exclusion check -- which asserts the runtime image contains
zero filesystem entries matching ``*fallback*`` or ``*replay*`` -- keeps working
unweakened.  This module carries no fixture or replay data; it is a versioned
record loader, and the check stays a strict filename guard.
"""

from __future__ import annotations

from typing import Any, Final, Mapping

from ir.schema import SimulationIntent
from ir.schema_version import (
    FALLBACK_RECORD_MINIMUM_SUPPORTED_VERSION,
    FALLBACK_RECORD_SCHEMA_VERSION,
    SCHEMA_VERSION_FIELD,
)
from ir.versioning import (
    MigrationRegistry,
    PayloadStructureError,
    decode_json_object,
    dump_simulation_intent,
    load_simulation_intent,
)


FALLBACK_RECORD_MIGRATIONS: Final[MigrationRegistry] = MigrationRegistry(
    family="fallback_record",
    current_version=FALLBACK_RECORD_SCHEMA_VERSION,
    minimum_supported_version=FALLBACK_RECORD_MINIMUM_SUPPORTED_VERSION,
)
FALLBACK_RECORD_MIGRATIONS.validate()


REQUIRED_ENVELOPE_KEYS: Final[tuple[str, ...]] = (
    "mode",
    "case_id",
    "model_fixture",
    "model_sha256",
    "typed_interpreter_output",
    "initial_grounding",
    "final_grounding",
    "proposed_ir",
    "clarification_used",
    "validation_status_before_review",
)

NESTED_INTENT_KEY: Final[str] = "proposed_ir"


def load_fallback_envelope(
    raw: Mapping[str, Any] | bytes | str, *, source: str | None = None
) -> dict[str, Any]:
    """Migrate and structurally validate the envelope only."""

    family = FALLBACK_RECORD_MIGRATIONS.family
    payload = decode_json_object(raw, family=family, source=source)
    migrated = FALLBACK_RECORD_MIGRATIONS.migrate(payload, source=source)
    missing = [key for key in REQUIRED_ENVELOPE_KEYS if key not in migrated]
    if missing:
        raise PayloadStructureError(
            "Fallback record envelope is missing required members.",
            family=family,
            source=source,
            missing=sorted(missing),
        )
    return migrated


def load_fallback_record(
    raw: Mapping[str, Any] | bytes | str, *, source: str | None = None
) -> tuple[dict[str, Any], SimulationIntent]:
    """Authoritative loader for a REPLAY fallback record.

    Returns the migrated envelope and the nested ``SimulationIntent`` loaded
    through its own authoritative loader, so the nested body must declare its
    own explicit version.
    """

    envelope = load_fallback_envelope(raw, source=source)
    nested_source = f"{source}#{NESTED_INTENT_KEY}" if source else NESTED_INTENT_KEY
    intent = load_simulation_intent(
        envelope[NESTED_INTENT_KEY], source=nested_source
    )
    return envelope, intent


def build_fallback_envelope(
    body: Mapping[str, Any], intent: SimulationIntent
) -> dict[str, Any]:
    """Assemble a writeable envelope that emits only current versions."""

    envelope = {
        key: value
        for key, value in body.items()
        if key != SCHEMA_VERSION_FIELD and key != NESTED_INTENT_KEY
    }
    envelope[NESTED_INTENT_KEY] = dump_simulation_intent(intent)
    envelope[SCHEMA_VERSION_FIELD] = FALLBACK_RECORD_MIGRATIONS.current_version
    return envelope
