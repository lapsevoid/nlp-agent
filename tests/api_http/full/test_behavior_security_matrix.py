"""Full real-HTTP behavior and security checks for every live operation.

The OpenAPI reachability runner proves that a route can be reached.  This
module adds transport, authentication, CSRF, role-boundary, and response
contract assertions around the same live inventory.  Domain workflows with
stateful fixtures remain in the focused API tests; this matrix deliberately
does not treat an arbitrary 401/403 probe as a successful business test.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from typing import Any

import httpx
import pytest

from ..support.auth import session_id_for_client, same_origin_headers
from ..support.full_runner import FullProbeContext, ProbeRequest, build_probe_request
from ..support.inventory import Operation, fetch_openapi
from ..support.resources import create_session


pytestmark = pytest.mark.api_full


_PUBLIC_WEB = {
    ("GET", "/health/live"),
    ("GET", "/health/ready"),
    ("GET", "/api/openapi.json"),
    ("GET", "/api/v1/auth/captcha"),
    ("POST", "/api/v1/auth/login"),
    ("POST", "/api/v1/auth/register"),
    ("POST", "/api/v1/auth/sms/send"),
    ("POST", "/api/v1/auth/guest"),
}
_PUBLIC_MONITOR = {
    ("GET", "/health/live"),
    ("GET", "/health/ready"),
    ("GET", "/api/openapi.json"),
    ("POST", "/api/v1/auth/login"),
    ("POST", "/api/v1/auth/session"),
}
def _request_kwargs(request: ProbeRequest) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "headers": dict(request.headers),
        "params": request.params,
    }
    if request.files is not None:
        kwargs["files"] = request.files
        kwargs["data"] = request.data or {}
    elif request.data is not None:
        kwargs["data"] = request.data
    elif request.json_body is not None:
        kwargs["json"] = request.json_body
    return kwargs


def _operation_request(
    operation: Operation,
    document: dict[str, Any],
    context: FullProbeContext,
) -> ProbeRequest:
    return build_probe_request(operation, document, context=context)


def _all_operations(
    web_client: httpx.Client,
    monitor_client: httpx.Client,
    web_base_url: str,
    monitor_base_url: str,
) -> tuple[dict[str, Any], list[Operation], dict[str, Any], list[Operation]]:
    web_document, web_operations = fetch_openapi(
        web_client,
        service="web",
        base_url=web_base_url,
    )
    monitor_document, monitor_operations = fetch_openapi(
        monitor_client,
        service="monitor",
        base_url=monitor_base_url,
    )
    return web_document, web_operations, monitor_document, monitor_operations


def _requires_auth(operation: Operation) -> bool:
    public = _PUBLIC_WEB if operation.service == "web" else _PUBLIC_MONITOR
    return (operation.method, operation.path) not in public


def _is_write(operation: Operation) -> bool:
    return operation.method in {"POST", "PUT", "PATCH", "DELETE"}


def _is_developer_control_plane(operation: Operation) -> bool:
    # This endpoint is intentionally role-projected and available to any
    # authenticated principal; it is not a developer administration route.
    if operation.path == "/api/v1/system/menus/visible":
        return False
    return operation.path.startswith(
        (
            "/api/v1/developer/",
            "/api/v1/audit/",
            "/api/v1/permissions",
            "/api/v1/roles",
            "/api/v1/system/",
        )
    )


def _role_boundary_request(
    operation: Operation,
    document: dict[str, Any],
    context: FullProbeContext,
) -> ProbeRequest:
    request = _operation_request(operation, document, context)
    if (
        operation.method == "PATCH"
        and operation.path == "/api/v1/developer/feedback/{thread_id}"
    ):
        # FeedbackUpdateBody has an application-level "at least one field"
        # validator, so an empty generic object would fail before auth runs.
        return replace(request, json_body={"status": "in_progress"})
    return request


def _assert_response_envelope(
    operation: Operation,
    response: httpx.Response,
    document: dict[str, Any],
) -> None:
    assert response.status_code < 500, (
        f"{operation.service} {operation.method} {operation.path} returned "
        f"{response.status_code}: {response.text}"
    )
    if response.status_code == 204:
        assert not response.content
        return
    content_type = response.headers.get("content-type", "")
    assert content_type.startswith(("application/json", "application/problem+json")), (
        f"{operation.service} {operation.method} {operation.path} returned "
        f"unexpected content type {content_type!r}: {response.text[:500]}"
    )
    payload = response.json()
    if 400 <= response.status_code:
        assert isinstance(payload, dict), payload
        assert any(key in payload for key in ("code", "detail", "title", "type")), payload
        return
    _assert_required_response_fields(operation, document, response.status_code, payload)


def _assert_required_response_fields(
    operation: Operation,
    document: dict[str, Any],
    status_code: int,
    payload: Any,
) -> None:
    response = _operation_response(operation, document, status_code)
    if not isinstance(response, dict):
        return
    content = response.get("content")
    if not isinstance(content, dict):
        return
    media = content.get("application/json")
    if not isinstance(media, dict):
        return
    schema = _resolve_schema(media.get("schema"), document)
    required = schema.get("required", [])
    if not required or not isinstance(payload, dict):
        return
    missing = [name for name in required if name not in payload]
    assert not missing, (
        f"{operation.service} {operation.method} {operation.path} response "
        f"{status_code} is missing required fields: {missing}; payload={payload}"
    )


def _operation_response(
    operation: Operation,
    document: dict[str, Any],
    status_code: int,
) -> dict[str, Any] | None:
    paths = document.get("paths", {})
    path_item = paths.get(operation.path, {}) if isinstance(paths, dict) else {}
    operation_document = (
        path_item.get(operation.method.lower(), {})
        if isinstance(path_item, dict)
        else {}
    )
    responses = operation_document.get("responses", {})
    if not isinstance(responses, dict):
        return None
    return responses.get(str(status_code)) or responses.get("default")


def _resolve_schema(schema: Any, document: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(schema, dict):
        return {}
    reference = schema.get("$ref")
    if isinstance(reference, str) and reference.startswith("#/components/schemas/"):
        name = reference.rsplit("/", 1)[-1]
        components = document.get("components", {})
        schemas = components.get("schemas", {}) if isinstance(components, dict) else {}
        target = schemas.get(name) if isinstance(schemas, dict) else None
        return _resolve_schema(target, document)
    return schema


def _make_context(
    developer_client: httpx.Client,
    developer_user,
    *,
    monitor_csrf_token: str | None = None,
) -> FullProbeContext:
    session = create_session(
        developer_client,
        workspace_id=developer_user.workspace_id,
    )
    return FullProbeContext(
        workspace_id=developer_user.workspace_id,
        user_id=developer_user.user_id,
        session_id=session["session_id"],
        nonce="behavior-matrix",
        username=developer_user.username,
        password=developer_user.password,
        csrf_token=developer_client.headers["X-CSRF-Token"],
        monitor_csrf_token=monitor_csrf_token,
    )


def _replace_csrf(request: ProbeRequest, token: str) -> dict[str, str]:
    headers = dict(request.headers)
    csrf_header = next(
        (name for name in headers if name.lower() == "x-csrf-token"),
        "X-CSRF-Token",
    )
    headers[csrf_header] = token
    return headers


def _format_failures(failures: Iterable[str]) -> str:
    return "\n".join(failures)


def test_every_live_operation_has_http_behavior_contract(
    api_http_client_factory,
    api_http_environment,
    developer_user,
) -> None:
    """Run a real HTTP request and validate its success/error envelope per op."""

    web_client = api_http_client_factory(base_url=api_http_environment.web_base_url)
    monitor_client = api_http_client_factory(
        base_url=api_http_environment.monitor_base_url
    )
    from ..support.auth import login, refresh_session

    web_login = login(
        web_client,
        origin=api_http_environment.web_origin,
        username=developer_user.username,
        password=developer_user.password,
    )
    web_session = refresh_session(
        web_client,
        origin=api_http_environment.web_origin,
        login_result=web_login,
    )
    web_client.headers.update(
        same_origin_headers(
            api_http_environment.web_origin,
            csrf_token=web_session.csrf_token,
        )
    )
    monitor_login = login(
        monitor_client,
        origin=api_http_environment.monitor_base_url,
        username=developer_user.username,
        password=developer_user.password,
        cookie_name="nlp_monitor_session",
    )
    monitor_session = refresh_session(
        monitor_client,
        origin=api_http_environment.monitor_base_url,
        login_result=monitor_login,
    )
    monitor_client.headers.update(
        same_origin_headers(
            api_http_environment.monitor_base_url,
            csrf_token=monitor_session.csrf_token,
        )
    )
    context = _make_context(
        web_client,
        developer_user,
        monitor_csrf_token=monitor_session.csrf_token,
    )
    web_document, web_operations, monitor_document, monitor_operations = _all_operations(
        web_client,
        monitor_client,
        api_http_environment.web_base_url,
        api_http_environment.monitor_base_url,
    )
    failures: list[str] = []
    for operation, client, document in (
        *[(item, web_client, web_document) for item in web_operations],
        *[(item, monitor_client, monitor_document) for item in monitor_operations],
    ):
        request = _operation_request(operation, document, context)
        if operation.service == "monitor":
            request = ProbeRequest(
                method=request.method,
                path=request.path,
                params=request.params,
                headers={**request.headers, "Origin": api_http_environment.monitor_base_url},
                json_body=request.json_body,
                data=request.data,
                files=request.files,
            )
        try:
            response = client.request(request.method, request.path, **_request_kwargs(request))
            _assert_response_envelope(operation, response, document)
        except (AssertionError, httpx.HTTPError, ValueError) as error:
            failures.append(
                f"{operation.service} {operation.method} {operation.path}: {error}"
            )
    assert not failures, _format_failures(failures)


def test_every_protected_operation_rejects_missing_cookie(
    api_http_client_factory,
    api_http_environment,
    developer_user,
) -> None:
    web_client = api_http_client_factory(base_url=api_http_environment.web_base_url)
    monitor_client = api_http_client_factory(
        base_url=api_http_environment.monitor_base_url
    )
    context = FullProbeContext(
        workspace_id=developer_user.workspace_id,
        user_id=developer_user.user_id,
        session_id="00000000-0000-0000-0000-000000000000",
        nonce="missing-cookie",
    )
    web_document, web_operations, monitor_document, monitor_operations = _all_operations(
        web_client,
        monitor_client,
        api_http_environment.web_base_url,
        api_http_environment.monitor_base_url,
    )
    failures: list[str] = []
    for operation, client, document in (
        *[(item, web_client, web_document) for item in web_operations],
        *[(item, monitor_client, monitor_document) for item in monitor_operations],
    ):
        if not _requires_auth(operation):
            continue
        request = _operation_request(operation, document, context)
        headers = dict(request.headers)
        headers.pop("X-CSRF-Token", None)
        headers["Origin"] = (
            api_http_environment.web_origin
            if operation.service == "web"
            else api_http_environment.monitor_base_url
        )
        try:
            response = client.request(
                request.method,
                request.path,
                **{**_request_kwargs(request), "headers": headers},
            )
            if response.status_code not in {401, 403, 404}:
                failures.append(
                    f"{operation.service} {operation.method} {operation.path}: "
                    f"missing-cookie status={response.status_code} body={response.text[:300]}"
                )
        except (httpx.HTTPError, ValueError) as error:
            failures.append(f"{operation.service} {operation.method} {operation.path}: {error}")
    assert not failures, _format_failures(failures)


def test_every_protected_write_rejects_wrong_csrf(
    api_http_client_factory,
    api_http_environment,
    developer_user,
    mysql_probe,
    authenticated_client_for,
) -> None:
    web_client = authenticated_client_for(developer_user)
    monitor_client = api_http_client_factory(
        base_url=api_http_environment.monitor_base_url
    )
    from ..coverage.test_monitor_runtime_core import _authenticate_monitor

    _authenticate_monitor(
        monitor_client,
        developer_user,
        api_http_environment.monitor_base_url,
    )
    context = FullProbeContext(
        workspace_id=developer_user.workspace_id,
        user_id=developer_user.user_id,
        session_id=session_id_for_client(web_client, mysql_probe),
        nonce="wrong-csrf",
        username=developer_user.username,
        password=developer_user.password,
        csrf_token="wrong-csrf-token",
        monitor_csrf_token="wrong-monitor-csrf-token",
    )
    web_document, web_operations, monitor_document, monitor_operations = _all_operations(
        web_client,
        monitor_client,
        api_http_environment.web_base_url,
        api_http_environment.monitor_base_url,
    )
    failures: list[str] = []
    for operation, client, document in (
        *[(item, web_client, web_document) for item in web_operations],
        *[(item, monitor_client, monitor_document) for item in monitor_operations],
    ):
        if not _requires_auth(operation) or not _is_write(operation):
            continue
        request = _operation_request(operation, document, context)
        headers = _replace_csrf(request, "wrong-csrf-token")
        headers["Origin"] = (
            api_http_environment.web_origin
            if operation.service == "web"
            else api_http_environment.monitor_base_url
        )
        try:
            response = client.request(
                request.method,
                request.path,
                **{**_request_kwargs(request), "headers": headers},
            )
            if response.status_code != 403:
                failures.append(
                    f"{operation.service} {operation.method} {operation.path}: "
                    f"wrong-csrf status={response.status_code} body={response.text[:300]}"
                )
        except (httpx.HTTPError, ValueError) as error:
            failures.append(f"{operation.service} {operation.method} {operation.path}: {error}")
    assert not failures, _format_failures(failures)


def test_developer_operations_reject_lower_roles(
    api_http_client_factory,
    api_http_environment,
    guest_user,
    student_user,
    teacher_user,
    developer_user,
    mysql_probe,
    authenticated_client_for,
) -> None:
    developer_client = authenticated_client_for(developer_user)
    context = FullProbeContext(
        workspace_id=developer_user.workspace_id,
        user_id=developer_user.user_id,
        session_id=session_id_for_client(developer_client, mysql_probe),
        nonce="role-boundary",
        username=developer_user.username,
        password=developer_user.password,
        csrf_token=developer_client.headers["X-CSRF-Token"],
    )
    web_document, web_operations = fetch_openapi(
        developer_client,
        service="web",
        base_url=api_http_environment.web_base_url,
    )
    operations = [item for item in web_operations if _is_developer_control_plane(item)]
    failures: list[str] = []
    for role, user in (
        ("guest", guest_user),
        ("student", student_user),
        ("teacher", teacher_user),
    ):
        client = authenticated_client_for(user)
        for operation in operations:
            request = _role_boundary_request(operation, web_document, context)
            headers = _replace_csrf(request, client.headers.get("X-CSRF-Token", ""))
            headers["Origin"] = api_http_environment.web_origin
            try:
                response = client.request(
                    operation.method,
                    request.path,
                    **{**_request_kwargs(request), "headers": headers},
                )
                if response.status_code not in {403, 404}:
                    failures.append(
                        f"{role} {operation.method} {operation.path}: "
                        f"status={response.status_code} body={response.text[:300]}"
                    )
            except (httpx.HTTPError, ValueError) as error:
                failures.append(f"{role} {operation.method} {operation.path}: {error}")
    assert not failures, _format_failures(failures)
