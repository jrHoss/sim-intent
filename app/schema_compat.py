"""Route-scoped legacy schema-version compatibility (Task 19, decision D-2).

Every authoritative loader in this repository requires an explicitly declared
positive-integer ``schema_version``.  There is exactly one narrow exception:
the frozen legacy route ``PUT /session/{session_id}/intent``.

CLAUDE.md invariant 8 makes the legacy viewer contracts *additive-only*, so
Task 19 may not turn a previously absent field into a mandatory one on that
route.  Absence there is normalised through the route-scoped constant below.

The exception is deliberately narrow:

- it never inspects or guesses the payload shape -- only the presence of the
  ``schema_version`` key is consulted;
- it does not apply to checked-in files, fallback records, evaluation cases,
  new API contracts, or any future route;
- a *declared* version is never rewritten: a malformed, obsolete, or future
  declaration still fails through the normal typed loader path;
- it is temporary compatibility behaviour and is removed when the legacy route
  is retired (route cutover is owned by Task 45).
"""

from __future__ import annotations

from typing import Any, Final, Mapping

from ir.schema_version import SCHEMA_VERSION_FIELD
from ir.versioning import decode_json_object

#: The single route this exception covers.
LEGACY_INTENT_ROUTE: Final[str] = "PUT /session/{session_id}/intent"

#: The version an undeclared body on that route is normalised to.
LEGACY_UNVERSIONED_INTENT_VERSION: Final[int] = 1


def normalize_legacy_intent_payload(
    raw: Mapping[str, Any] | bytes | str,
    *,
    source: str = LEGACY_INTENT_ROUTE,
) -> tuple[dict[str, Any], bool]:
    """Return ``(payload, normalized)`` for the frozen legacy intent route.

    ``normalized`` is True only when the body carried no ``schema_version`` key
    at all and the route-scoped constant was applied.  Any present value --
    valid, malformed, or future -- is passed through untouched so the
    authoritative loader can classify it.
    """

    payload = dict(decode_json_object(raw, family="simulation_intent", source=source))
    if SCHEMA_VERSION_FIELD in payload:
        return payload, False
    payload[SCHEMA_VERSION_FIELD] = LEGACY_UNVERSIONED_INTENT_VERSION
    return payload, True
