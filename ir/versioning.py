"""Versioned payload loading and sequential migration (Task 19).

ADR-004 and ``docs/architecture/technical-preview/migration-rules.md`` require:

- every versioned product/persisted JSON payload declares a positive integer
  ``schema_version``;
- reads validate the declared version **before** migration;
- migrations are explicit ``n -> n + 1`` backend transforms;
- writes emit only the current schema version;
- unsupported future versions return a typed error **without partial parsing**;
- migration is idempotent at the current version;
- unsafe missing information never becomes an approved/confirmed state.

The registry here is generic so that every family (SimulationIntent, the
evaluation case record, the REPLAY fallback envelope) shares one audited
implementation instead of repeating version handling per call site.

A migration is a *pure* content transform.  It receives a version-free body and
must not set ``schema_version`` itself; the registry owns the version field end
to end::

    @REGISTRY.register(1)
    def _one_to_two(payload: Mapping[str, Any]) -> dict[str, Any]:
        ...

``register`` accepts only ``from_version``; the target is always
``from_version + 1``.  A skipping or non-sequential edge is therefore not
representable, and the registry validates that the registered edge set exactly
covers ``minimum_supported_version .. current_version - 1``.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Final, Iterable, Iterator, Mapping

from ir.schema import SimulationIntent
from ir.schema_version import (
    SCHEMA_VERSION_FIELD,
    SIMULATION_INTENT_MINIMUM_SUPPORTED_VERSION,
    SIMULATION_INTENT_SCHEMA_VERSION,
)


Migration = Callable[[Mapping[str, Any]], dict[str, Any]]

_MAX_DECLARED_REPR = 40


class _Absent:
    """Sentinel for "this path is not present at all"."""

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "<absent>"

    def __bool__(self) -> bool:
        return False


ABSENT: Final[_Absent] = _Absent()


# --------------------------------------------------------------------------
# Typed failures
# --------------------------------------------------------------------------


class ProblemDetailsError(ValueError):
    """Base for typed failures that expose the repository RFC 9457 shape."""

    code: str = "problem"
    http_status: int = 422
    retryable: bool = False

    def __init__(
        self,
        message: str,
        **details: Any,
    ) -> None:
        super().__init__(message)
        self.safe_message = message
        self.details = details

    def problem_details(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "title": self.__class__.__name__,
            "detail": self.safe_message,
            "status": self.http_status,
            "retryable": self.retryable,
            **self.details,
        }


class SchemaVersionError(ProblemDetailsError):
    """Base class for every typed versioned-payload failure."""

    code: str = "schema_version_error"

    def __init__(
        self,
        message: str,
        *,
        family: str,
        source: str | None = None,
        **details: Any,
    ) -> None:
        self.family = family
        self.source = _safe_source(source)
        super().__init__(message, family=family, **details)

    def problem_details(self) -> dict[str, Any]:
        payload = super().problem_details()
        if self.source is not None:
            payload["source"] = self.source
        return payload


class PayloadStructureError(SchemaVersionError):
    """The payload is not a JSON object, or its body is malformed/partial."""

    code = "payload_structure_invalid"


class MissingSchemaVersionError(SchemaVersionError):
    """The payload declares no ``schema_version`` at all."""

    code = "schema_version_missing"


class MalformedSchemaVersionError(SchemaVersionError):
    """``schema_version`` is present but is not a positive integer."""

    code = "schema_version_malformed"


class UnsupportedFutureVersionError(SchemaVersionError):
    """The declared version is newer than this build understands."""

    code = "schema_version_unsupported_future"


class ObsoleteSchemaVersionError(SchemaVersionError):
    """The declared version is older than the minimum supported version."""

    code = "schema_version_obsolete"


class MigrationPathError(SchemaVersionError):
    """No registered migration reaches the current version.

    This is a server-side registry defect rather than bad client input, so it
    maps to 5xx.
    """

    code = "schema_migration_path_missing"
    http_status = 500


class MigrationRegistryError(RuntimeError):
    """A registry was declared with a duplicate, skipping, or missing edge.

    Raised at registration/validation time (import time for the production
    registries), never in response to payload content.
    """


def _safe_source(source: str | None) -> str | None:
    """Reduce an accidental absolute host path to a short relative label."""

    if source is None:
        return None
    text = str(source).replace("\\", "/")
    looks_absolute = text.startswith("/") or (len(text) > 1 and text[1] == ":")
    if looks_absolute:
        parts = [part for part in text.split("/") if part and part != ".."]
        text = "/".join(parts[-2:]) if parts else "payload"
    return text[:120]


# --------------------------------------------------------------------------
# Version scalar validation
# --------------------------------------------------------------------------


def is_positive_integer_version(value: Any) -> bool:
    """True only for a real positive ``int``.

    ``bool`` is rejected explicitly because ``isinstance(True, int)`` is true
    in Python.  ``float`` is rejected even when integral: the contract says
    positive *integer*, and accepting ``1.0`` would make ``1.5`` a judgement
    call.
    """

    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def _declared_repr(value: Any) -> str:
    text = repr(value)
    if len(text) > _MAX_DECLARED_REPR:
        text = text[:_MAX_DECLARED_REPR] + "..."
    return text


# --------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------


class MigrationRegistry:
    """Sequential ``n -> n + 1`` migration registry for one payload family."""

    def __init__(
        self,
        *,
        family: str,
        current_version: int,
        minimum_supported_version: int = 1,
    ) -> None:
        if not is_positive_integer_version(current_version):
            raise MigrationRegistryError(
                f"{family}: current_version must be a positive integer"
            )
        if not is_positive_integer_version(minimum_supported_version):
            raise MigrationRegistryError(
                f"{family}: minimum_supported_version must be a positive integer"
            )
        if minimum_supported_version > current_version:
            raise MigrationRegistryError(
                f"{family}: minimum_supported_version "
                f"{minimum_supported_version} exceeds current_version "
                f"{current_version}"
            )
        self.family = family
        self.current_version = current_version
        self.minimum_supported_version = minimum_supported_version
        self._migrations: dict[int, Migration] = {}

    # -- registration ------------------------------------------------------

    def register(self, from_version: int) -> Callable[[Migration], Migration]:
        """Register the ``from_version -> from_version + 1`` transform.

        There is no ``to_version`` parameter, so a skipping edge cannot be
        expressed.  A second registration for the same ``from_version`` raises.
        """

        if not is_positive_integer_version(from_version):
            raise MigrationRegistryError(
                f"{self.family}: from_version must be a positive integer"
            )
        if from_version < self.minimum_supported_version:
            raise MigrationRegistryError(
                f"{self.family}: from_version {from_version} is below the "
                f"minimum supported version {self.minimum_supported_version}"
            )
        if from_version >= self.current_version:
            raise MigrationRegistryError(
                f"{self.family}: from_version {from_version} is not below the "
                f"current version {self.current_version}"
            )

        def decorator(migration: Migration) -> Migration:
            if from_version in self._migrations:
                raise MigrationRegistryError(
                    f"{self.family}: duplicate migration edge "
                    f"{from_version} -> {from_version + 1}"
                )
            self._migrations[from_version] = migration
            return migration

        return decorator

    @property
    def registered_edges(self) -> tuple[int, ...]:
        return tuple(sorted(self._migrations))

    def validate(self) -> None:
        """Assert the edge set exactly covers ``minimum .. current - 1``."""

        expected = set(range(self.minimum_supported_version, self.current_version))
        actual = set(self._migrations)
        missing = sorted(expected - actual)
        unexpected = sorted(actual - expected)
        if missing or unexpected:
            raise MigrationRegistryError(
                f"{self.family}: migration registry is incomplete; "
                f"missing edges {missing}, unexpected edges {unexpected}"
            )

    # -- reading -----------------------------------------------------------

    def declared_version(
        self, payload: Mapping[str, Any], *, source: str | None = None
    ) -> int:
        """Return the declared version, or raise a typed failure.

        The payload body is never inspected to infer a version: only the
        explicit ``schema_version`` key is consulted.
        """

        if not isinstance(payload, Mapping):
            raise PayloadStructureError(
                "Versioned payload must be a JSON object.",
                family=self.family,
                source=source,
                observed_type=type(payload).__name__,
            )
        if SCHEMA_VERSION_FIELD not in payload:
            raise MissingSchemaVersionError(
                f"Payload does not declare '{SCHEMA_VERSION_FIELD}'.",
                family=self.family,
                source=source,
                supported_min=self.minimum_supported_version,
                supported_max=self.current_version,
            )
        declared = payload[SCHEMA_VERSION_FIELD]
        if not is_positive_integer_version(declared):
            raise MalformedSchemaVersionError(
                f"'{SCHEMA_VERSION_FIELD}' must be a positive integer.",
                family=self.family,
                source=source,
                declared_type=type(declared).__name__,
                declared_repr=_declared_repr(declared),
            )
        return declared

    def check_version(
        self, payload: Mapping[str, Any], *, source: str | None = None
    ) -> int:
        """Validate the declared version and its bounds before any migration."""

        declared = self.declared_version(payload, source=source)
        if declared > self.current_version:
            raise UnsupportedFutureVersionError(
                f"Declared schema version {declared} is newer than the "
                f"supported maximum {self.current_version}; the payload body "
                "was not parsed.",
                family=self.family,
                source=source,
                declared=declared,
                supported_max=self.current_version,
            )
        if declared < self.minimum_supported_version:
            raise ObsoleteSchemaVersionError(
                f"Declared schema version {declared} is older than the "
                f"minimum supported version {self.minimum_supported_version}.",
                family=self.family,
                source=source,
                declared=declared,
                minimum_supported=self.minimum_supported_version,
            )
        return declared

    def migrate(
        self, payload: Mapping[str, Any], *, source: str | None = None
    ) -> dict[str, Any]:
        """Return the payload migrated to exactly ``current_version``.

        Idempotent at the current version: a current payload runs through zero
        migration functions.
        """

        declared = self.check_version(payload, source=source)
        # The registry owns the version field end to end.  Migrations receive a
        # version-free body and must not manage versions themselves, so an
        # ``n -> n + 1`` transform cannot accidentally claim a different target.
        body: dict[str, Any] = {
            key: value
            for key, value in payload.items()
            if key != SCHEMA_VERSION_FIELD
        }
        for version in range(declared, self.current_version):
            migration = self._migrations.get(version)
            if migration is None:
                raise MigrationPathError(
                    f"No registered migration for {self.family} "
                    f"{version} -> {version + 1}.",
                    family=self.family,
                    source=source,
                    from_version=version,
                    to_version=version + 1,
                )
            result = migration(body)
            if not isinstance(result, Mapping):
                raise MigrationPathError(
                    f"Migration {self.family} {version} -> {version + 1} "
                    "did not return a JSON object.",
                    family=self.family,
                    source=source,
                    from_version=version,
                    to_version=version + 1,
                )
            result = dict(result)
            if SCHEMA_VERSION_FIELD in result:
                raise MigrationPathError(
                    f"Migration {self.family} {version} -> {version + 1} "
                    f"must not set '{SCHEMA_VERSION_FIELD}'; the registry owns "
                    "the version field.",
                    family=self.family,
                    source=source,
                    from_version=version,
                    to_version=version + 1,
                )
            body = result
        migrated = dict(body)
        migrated[SCHEMA_VERSION_FIELD] = self.current_version
        return migrated


# --------------------------------------------------------------------------
# Safety-critical semantics
# --------------------------------------------------------------------------

#: Paths a migration may never synthesise from absence.  Where a future
#: migration genuinely lacks one of these, migration-rules.md requires the
#: explicit unapproved/blocked state instead (proposed / pending / unvalidated).
SAFETY_CRITICAL_PATHS: Final[tuple[str, ...]] = (
    "analysis.type",
    "analysis.units.length",
    "analysis.units.force",
    "analysis.units.stress",
    "materials[].model",
    "materials[].E_MPa",
    "materials[].nu",
    "materials[].density_tonne_per_mm3",
    "regions[].entity_type",
    "regions[].entity_ids",
    "regions[].selection_method",
    "regions[].confidence",
    "regions[].source_instruction",
    "regions[].status",
    "regions[].cad_face_target",
    "bcs[].region_ref",
    "bcs[].components",
    "loads[].region_ref",
    "loads[].vector",
    "loads[].magnitude",
    "assumptions[].text",
    "assumptions[].criticality",
    "assumptions[].status",
    "validation_status",
)

#: Transitions that would make a payload *more* approved than it was.
_APPROVAL_UPGRADES: Final[dict[str, tuple[str, ...]]] = {
    "regions[].status": ("confirmed",),
    "assumptions[].status": ("accepted",),
    "validation_status": ("valid",),
}


def _walk(node: Any, segments: tuple[str, ...]) -> Iterator[Any]:
    if not segments:
        yield node
        return
    head, rest = segments[0], segments[1:]
    if head.endswith("[]"):
        key = head[:-2]
        child = node.get(key, ABSENT) if isinstance(node, Mapping) else ABSENT
        if not isinstance(child, list):
            yield ABSENT
            return
        for item in child:
            yield from _walk(item, rest)
        return
    child = node.get(head, ABSENT) if isinstance(node, Mapping) else ABSENT
    if child is ABSENT:
        yield ABSENT
        return
    yield from _walk(child, rest)


def project_paths(
    payload: Mapping[str, Any], paths: Iterable[str] = SAFETY_CRITICAL_PATHS
) -> dict[str, tuple[Any, ...]]:
    """Project a payload onto the given dotted paths.

    ``ABSENT`` marks a path that is not present, so a migration that *invents*
    a value is distinguishable from one that leaves it alone.
    """

    return {
        path: tuple(_walk(payload, tuple(path.split("."))))
        for path in paths
    }


def safety_critical_differences(
    before: Mapping[str, Any], after: Mapping[str, Any]
) -> dict[str, tuple[Any, Any]]:
    """Return every safety-critical path whose projected value changed."""

    projected_before = project_paths(before)
    projected_after = project_paths(after)
    return {
        path: (projected_before[path], projected_after[path])
        for path in SAFETY_CRITICAL_PATHS
        if projected_before[path] != projected_after[path]
    }


def approval_upgrades(
    before: Mapping[str, Any], after: Mapping[str, Any]
) -> dict[str, tuple[Any, Any]]:
    """Return any transition toward a more-approved state.

    A migration must never move a region to ``confirmed``, an assumption to
    ``accepted``, or the validation status to ``valid``.
    """

    found: dict[str, tuple[Any, Any]] = {}
    for path, approved_values in _APPROVAL_UPGRADES.items():
        old = project_paths(before, [path])[path]
        new = project_paths(after, [path])[path]
        for index, new_value in enumerate(new):
            old_value = old[index] if index < len(old) else ABSENT
            if new_value in approved_values and old_value != new_value:
                found[f"{path}[{index}]"] = (old_value, new_value)
    return found


# --------------------------------------------------------------------------
# SimulationIntent registry and authoritative loader
# --------------------------------------------------------------------------

SIMULATION_INTENT_MIGRATIONS: Final[MigrationRegistry] = MigrationRegistry(
    family="simulation_intent",
    current_version=SIMULATION_INTENT_SCHEMA_VERSION,
    minimum_supported_version=SIMULATION_INTENT_MINIMUM_SUPPORTED_VERSION,
)

#: The version-2 engineering decisions that a version-1 payload never made.
#: The migration writes an explicit ``null`` for each rather than a value: an
#: absent decision must stay visibly absent so ``ir.validate`` reports the setup
#: as ``structurally_incomplete`` until an engineer states it.
V1_TO_V2_MISSING_ANALYSIS_DECISIONS: Final[tuple[str, ...]] = (
    "dimensionality",
    "solver_target",
    "coordinate_system",
)
V1_TO_V2_MISSING_SETUP_DECISIONS: Final[tuple[str, ...]] = (
    "mesh_settings",
    "solver_settings",
)


@SIMULATION_INTENT_MIGRATIONS.register(1)
def _simulation_intent_one_to_two(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Carry a version-1 setup forward as *legacy-incomplete*.

    Version 1 predates the analysis dimensionality, coordinate system, solver
    target, meshing profile and solver profile.  Inventing any of them here
    would hand an old setup a 3D-solid approval, a global-coordinate approval, a
    CalculiX target, a 1 mm Gmsh mesh, a solver profile and a set of requested
    results that no engineer ever chose.  Nothing else is reinterpreted: unit,
    load, region, assumption and validation semantics are copied untouched.
    """

    body = dict(payload)
    analysis = body.get("analysis")
    if isinstance(analysis, Mapping):
        migrated_analysis = dict(analysis)
        for key in V1_TO_V2_MISSING_ANALYSIS_DECISIONS:
            migrated_analysis.setdefault(key, None)
        body["analysis"] = migrated_analysis
    for key in V1_TO_V2_MISSING_SETUP_DECISIONS:
        body.setdefault(key, None)
    return body


@SIMULATION_INTENT_MIGRATIONS.register(2)
def _simulation_intent_two_to_three(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Preserve v2 CAD evidence without inventing stable identity.

    Positive unique integer sequences retain the established
    ``legacy_local_only`` policy. Integer sequences that violate the v3
    positive/unique constraints use an explicit blocked representation whose
    evidence is copied byte-for-value and in its original order.
    """

    body = dict(payload)
    regions = body.get("regions")
    if not isinstance(regions, list):
        return body
    migrated_regions: list[Any] = []
    for raw_region in regions:
        if not isinstance(raw_region, Mapping):
            migrated_regions.append(raw_region)
            continue
        migrated_regions.append(migrate_legacy_cad_region(raw_region))
    body["regions"] = migrated_regions
    return body


def migrate_legacy_cad_region(raw_region: Mapping[str, Any]) -> dict[str, Any]:
    """Migrate one numeric CAD Region without fabricating stable identity.

    Fallback grounding records embed Region-shaped legacy data outside their
    nested SimulationIntent.  They call this same production migration helper
    so legacy numeric evidence is never deserialized as current v3
    ``entity_ids``.
    """

    region = dict(raw_region)
    if region.get("entity_type") != "cad_face":
        return region
    if "cad_face_target" in region and "entity_ids" not in region:
        return region
    old_status = region.get("status")
    raw_ids = region.get("entity_ids")
    integer_evidence = (
        isinstance(raw_ids, list)
        and all(
            isinstance(value, int) and not isinstance(value, bool)
            for value in raw_ids
        )
    )
    source_tags = list(raw_ids) if integer_evidence else []
    valid_v3_tags = (
        bool(source_tags)
        and all(value > 0 for value in source_tags)
        and len(source_tags) == len(set(source_tags))
    )
    if valid_v3_tags:
        region["cad_face_target"] = {
            "resolution": "legacy_local_only",
            "source_face_tags": source_tags,
            "legacy_status": old_status,
        }
    elif integer_evidence:
        region["cad_face_target"] = {
            "resolution": "invalid_legacy_evidence",
            "source_face_tags": source_tags,
            "legacy_status": old_status,
            "legacy_reason": "invalid_numeric_tags",
        }
    else:
        # Non-numeric/mixed legacy entity_ids remain invalid.  Do not coerce
        # or discard them into seemingly numeric evidence.
        return region
    region.pop("entity_ids", None)
    if old_status == "confirmed":
        region["status"] = "proposed"
    return region


SIMULATION_INTENT_MIGRATIONS.validate()


def decode_json_object(
    raw: Mapping[str, Any] | bytes | bytearray | str,
    *,
    family: str,
    source: str | None = None,
) -> Mapping[str, Any]:
    """Decode ``raw`` to a JSON object without inspecting its shape."""

    if isinstance(raw, Mapping):
        return raw
    if isinstance(raw, (bytes, bytearray, str)):
        try:
            decoded = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise PayloadStructureError(
                "Payload is not valid JSON.",
                family=family,
                source=source,
            ) from exc
        if not isinstance(decoded, Mapping):
            raise PayloadStructureError(
                "Versioned payload must be a JSON object.",
                family=family,
                source=source,
                observed_type=type(decoded).__name__,
            )
        return decoded
    raise PayloadStructureError(
        "Versioned payload must be a JSON object.",
        family=family,
        source=source,
        observed_type=type(raw).__name__,
    )


def load_simulation_intent(
    raw: Mapping[str, Any] | bytes | bytearray | str,
    *,
    source: str | None = None,
) -> SimulationIntent:
    """Authoritative external ingestion path for a ``SimulationIntent``.

    Order is fixed and must not be reordered:

    1. structural gate (must be a JSON object);
    2. explicit version declaration (missing/malformed are typed failures);
    3. version bounds (future/obsolete are typed failures, no body parsing);
    4. sequential ``n -> n + 1`` migration;
    5. strict model validation, which rejects malformed or partial historical
       payloads without defaulting any field;
    6. post-assertion that the result carries exactly the current version.

    The ``schema_version`` model field is required. Historical documents still
    reach it only after this loader has validated and migrated their explicit
    declaration.
    """

    family = SIMULATION_INTENT_MIGRATIONS.family
    payload = decode_json_object(raw, family=family, source=source)
    migrated = SIMULATION_INTENT_MIGRATIONS.migrate(payload, source=source)
    try:
        intent = SimulationIntent.model_validate(migrated, strict=True)
    except Exception as exc:  # pydantic ValidationError and value errors
        raise PayloadStructureError(
            "Payload does not satisfy the SimulationIntent contract.",
            family=family,
            source=source,
            error_count=getattr(exc, "error_count", lambda: None)(),
        ) from exc
    if intent.schema_version != SIMULATION_INTENT_MIGRATIONS.current_version:
        raise MigrationPathError(
            "Migrated payload did not reach the current schema version.",
            family=family,
            source=source,
            from_version=intent.schema_version,
            to_version=SIMULATION_INTENT_MIGRATIONS.current_version,
        )
    return intent


def dump_simulation_intent(intent: SimulationIntent) -> dict[str, Any]:
    """Serialise a ``SimulationIntent``, always emitting the current version."""

    payload = intent.model_dump(mode="json")
    payload[SCHEMA_VERSION_FIELD] = SIMULATION_INTENT_MIGRATIONS.current_version
    return payload
