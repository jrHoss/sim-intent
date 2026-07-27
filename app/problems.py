"""HTTP problem-details integration for versioned product APIs."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

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


def validation_problem(request: Request, error: RequestValidationError) -> JSONResponse:
    safe_errors = [
        {"location": list(item["loc"]), "type": item["type"]}
        for item in error.errors()
    ]
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
        500: "Stored data integrity failure",
        507: "Insufficient storage",
    }.items()
}
