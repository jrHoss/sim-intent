"""Authoritative schema-version constants for every versioned payload family.

Task 19 (ADR-004, ``docs/architecture/technical-preview/migration-rules.md``).

This module is the single central version table for the repository.  It lives
in :mod:`ir` because ``ir`` is the lowest-level package here and has no
internal dependencies, so every other package can import these constants
without creating an import cycle.  It deliberately contains constants and
family metadata only: no models, no registries, and no I/O.

Every versioned payload family declares a *positive integer* version.  A
family whose ``minimum_supported`` equals its ``current`` version legitimately
has an empty migration registry: the pre-Task-19 shape of this repository is
explicitly version 1, and no earlier shape has ever existed.
"""

from __future__ import annotations

from typing import Final


# The JSON/dict key that carries a declared version in every versioned family.
SCHEMA_VERSION_FIELD: Final[str] = "schema_version"


# --------------------------------------------------------------------------
# SimulationIntent (ir/schema.py) -- the setup-bearing engineering aggregate
# --------------------------------------------------------------------------

# Version 2 (R3.1) makes the analysis dimensionality, coordinate system,
# solver target, meshing profile and solver profile explicit engineering
# decisions.  Version 1 remains supported and loadable: the registered
# ``1 -> 2`` migration marks each new decision as explicitly missing instead of
# granting it a default, so a legacy payload can never become ready or
# export-eligible without a deliberate edit.
SIMULATION_INTENT_SCHEMA_VERSION: Final[int] = 2
SIMULATION_INTENT_MINIMUM_SUPPORTED_VERSION: Final[int] = 1


# --------------------------------------------------------------------------
# Frozen evaluation corpus records (eval/)
# --------------------------------------------------------------------------

EVALUATION_CASE_SCHEMA_VERSION: Final[int] = 1
EVALUATION_CASE_MINIMUM_SUPPORTED_VERSION: Final[int] = 1

# The REPLAY fallback envelope in eval/fallback/*.json.  It embeds a complete
# SimulationIntent at "proposed_ir"; the envelope migration never rewrites that
# nested body, it delegates to the SimulationIntent registry.
FALLBACK_RECORD_SCHEMA_VERSION: Final[int] = 1
FALLBACK_RECORD_MINIMUM_SUPPORTED_VERSION: Final[int] = 1

# Replay bodies in eval/replay/*.json are the strict Interpretation LLM wire
# contract and are deliberately *not* stamped in band (Task 19 decision D-1).
# Their version is declared once by the sidecar manifest eval/replay/manifest.json.
REPLAY_RECORD_SCHEMA_VERSION: Final[int] = 1
REPLAY_RECORD_MINIMUM_SUPPORTED_VERSION: Final[int] = 1


# --------------------------------------------------------------------------
# Reserved constants
# --------------------------------------------------------------------------

# Export artifact metadata (export/common.py ExportResult).  Reserved only: no
# artifact manifest file format exists yet, so Task 19 deliberately does not
# create an artifact migration loader.  Task 36 owns the manifest contract.
ARTIFACT_METADATA_SCHEMA_VERSION: Final[int] = 1

# API contract version.  ADR-004 places new product APIs under /api/v1.
# Task 19 adds no runtime endpoint (decision D-9); this constant is the sole
# authority for the OpenAPI ``info.version`` field, the checked-in OpenAPI
# snapshot, and the generated TypeScript output.
API_CONTRACT_VERSION: Final[int] = 1
API_VERSION_PREFIX: Final[str] = "/api/v1"


# --------------------------------------------------------------------------
# Family metadata
# --------------------------------------------------------------------------

#: family name -> (current version, minimum supported version)
VERSIONED_FAMILIES: Final[dict[str, tuple[int, int]]] = {
    "simulation_intent": (
        SIMULATION_INTENT_SCHEMA_VERSION,
        SIMULATION_INTENT_MINIMUM_SUPPORTED_VERSION,
    ),
    "evaluation_case": (
        EVALUATION_CASE_SCHEMA_VERSION,
        EVALUATION_CASE_MINIMUM_SUPPORTED_VERSION,
    ),
    "fallback_record": (
        FALLBACK_RECORD_SCHEMA_VERSION,
        FALLBACK_RECORD_MINIMUM_SUPPORTED_VERSION,
    ),
    "replay_record": (
        REPLAY_RECORD_SCHEMA_VERSION,
        REPLAY_RECORD_MINIMUM_SUPPORTED_VERSION,
    ),
}
