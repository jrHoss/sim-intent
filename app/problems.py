"""HTTP problem-details integration for versioned product APIs."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from ir.schema import EngineeringConsistencyError
from ir.versioning import ProblemDetailsError


class ProblemDetails(BaseModel):
    model_config = ConfigDict(extra="allow")
    type: str = "about:blank"
    title: str
    status: int
    detail: str
    code: str
    trace_id: str
    retryable: bool


class ApiProblem(ProblemDetailsError):
    def __init__(
        self,
        *,
        status: int,
        code: str,
        title: str,
        detail: str,
        retryable: bool = False,
        trace_id: str | None = None,
        **details: Any,
    ) -> None:
        self.http_status = status
        self.code = code
        self.title = title
        self.retryable = retryable
        self.trace_id = trace_id
        super().__init__(detail, **details)

    def problem_details(self) -> dict[str, Any]:
        payload = super().problem_details()
        payload["title"] = self.title
        return payload


def problem_response(request: Request, error: ProblemDetailsError) -> JSONResponse:
    trace_id = (
        getattr(error, "trace_id", None)
        or getattr(request.state, "correlation_id", None)
        or str(uuid.uuid4())
    )
    payload = {
        "type": "about:blank",
        **error.problem_details(),
        "trace_id": trace_id,
    }
    return JSONResponse(
        payload,
        status_code=error.http_status,
        media_type="application/problem+json",
    )


def engineering_consistency_code(item: dict[str, Any]) -> str | None:
    """Return the stable engineering code behind one pydantic error, if any.

    ``EngineeringConsistencyError.code`` is drawn from a fixed server-side
    vocabulary and carries no request content, so it is safe to publish while
    the free-text pydantic message is still withheld.  Without it a client that
    submits a contradictory quantity or an unsupported unit would only learn
    *which field* failed, not why.

    Only :class:`EngineeringConsistencyError` qualifies.  Narrowing to the
    server's own type -- rather than to any ``ValueError`` that happens to
    carry a ``code`` attribute -- keeps the published vocabulary closed, so a
    client can never route a value of its own choosing into this field.
    """

    candidate = (item.get("ctx") or {}).get("error")
    if not isinstance(candidate, EngineeringConsistencyError):
        return None
    return candidate.code if isinstance(candidate.code, str) else None


def validation_problem(request: Request, error: RequestValidationError) -> JSONResponse:
    safe_errors = []
    for item in error.errors():
        safe_item: dict[str, Any] = {
            "location": list(item["loc"]),
            "type": item["type"],
        }
        code = engineering_consistency_code(item)
        if code is not None:
            safe_item["code"] = code
        safe_errors.append(safe_item)
    return problem_response(
        request,
        ApiProblem(
            status=422,
            code="request_validation_failed",
            title="Request validation failed",
            detail="The request does not match the API contract.",
            errors=safe_errors,
        ),
    )


PROBLEM_RESPONSES = {
    status: {
        "description": description,
        "content": {
            "application/problem+json": {
                "schema": {"$ref": "#/components/schemas/ProblemDetails"}
            }
        },
    }
    for status, description in {
        400: "Invalid request",
        404: "Resource not found",
        409: "Conflict",
        413: "Upload too large",
        415: "Unsupported media type",
        422: "Invalid request",
        500: "Persistence or stored-data integrity failure",
        507: "Insufficient storage",
    }.items()
}
