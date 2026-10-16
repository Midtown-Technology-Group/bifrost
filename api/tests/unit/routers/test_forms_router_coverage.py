import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.models import FileUploadRequest
from src.routers import forms


class _Result:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


async def _request_chunks(*chunks: bytes):
    for chunk in chunks:
        yield chunk


def _upload_request(headers: dict[str, str], *chunks: bytes):
    return SimpleNamespace(
        headers=headers,
        stream=lambda: _request_chunks(*chunks),
    )


def _upload_payload(form_id, **overrides):
    payload = {
        "form_id": str(form_id),
        "file_size": 3,
        "jti": str(uuid4()),
        "exp": 2_000_000_000,
        "session_jti": str(uuid4()),
        "session_exp": 2_000_000_000,
        "path": f"{form_id}/session/file/report.pdf",
        "storage_key": f"uploads/org/{form_id}/session/file/report.pdf",
        "field_name": "attachment",
        "content_type": "application/pdf",
    }
    payload.update(overrides)
    return payload


def test_form_schema_to_fields_preserves_file_and_data_provider_options():
    form_id = uuid4()
    provider_id = str(uuid4())

    fields = forms._form_schema_to_fields(
        {
            "fields": [
                {
                    "name": "attachment",
                    "label": "Attachment",
                    "type": "file",
                    "required": True,
                    "allowed_types": [".pdf", "image/*"],
                    "max_size_mb": 5,
                    "multiple": True,
                    "allow_as_query_param": True,
                },
                {
                    "name": "choice",
                    "label": "Choice",
                    "type": "select",
                    "data_provider_id": provider_id,
                    "data_provider_inputs": {
                        "tenant": {"mode": "fieldRef", "field_name": "tenant_id"}
                    },
                },
            ]
        },
        form_id,
    )

    assert [field.position for field in fields] == [0, 1]
    assert fields[0].form_id == form_id
    assert fields[0].type == "file"
    assert fields[0].allowed_types == [".pdf", "image/*"]
    assert fields[0].max_size_mb == 5
    assert fields[0].multiple is True
    assert fields[0].allow_as_query_param is True
    assert str(fields[1].data_provider_id) == provider_id
    assert fields[1].data_provider_inputs == {
        "tenant": {
            "mode": "fieldRef",
            "value": None,
            "field_name": "tenant_id",
            "expression": None,
        }
    }


@pytest.mark.asyncio
async def test_validate_form_references_aggregates_invalid_references():
    db = AsyncMock()

    with pytest.raises(HTTPException) as exc:
        await forms._validate_form_references(
            db,
            workflow_id="not-a-uuid",
            launch_workflow_id="also-bad",
            form_schema={
                "fields": [
                    {"name": "tenant", "data_provider_id": "bad-provider-id"}
                ]
            },
        )

    assert exc.value.status_code == 422
    assert exc.value.detail["message"] == "Invalid form references"
    assert len(exc.value.detail["errors"]) == 3
    db.execute.assert_not_called()


@pytest.mark.asyncio
async def test_validate_form_references_rejects_wrong_active_types():
    workflow_id = str(uuid4())
    launch_id = str(uuid4())
    provider_id = str(uuid4())
    db = AsyncMock()
    db.execute = AsyncMock(
        side_effect=[
            _Result(SimpleNamespace(type="data_provider")),
            _Result(SimpleNamespace(type="data_provider")),
            _Result(SimpleNamespace(type="workflow")),
        ]
    )

    with pytest.raises(HTTPException) as exc:
        await forms._validate_form_references(
            db,
            workflow_id=workflow_id,
            launch_workflow_id=launch_id,
            form_schema={
                "fields": [
                    {"name": "customer", "data_provider_id": provider_id}
                ]
            },
        )

    assert exc.value.status_code == 422
    assert f"workflow_id '{workflow_id}' references a data_provider" in exc.value.detail["errors"][0]
    assert f"launch_workflow_id '{launch_id}' references a data_provider" in exc.value.detail["errors"][1]
    assert "references a workflow, not a data_provider" in exc.value.detail["errors"][2]


def test_sanitize_filename_and_mime_allow_list_branches():
    assert forms._sanitize_filename("../bad\\name:.pdf\x00") == "badname.pdf"
    assert forms._sanitize_filename("...   ") == "unnamed_file"

    assert forms._check_mime_type_allowed("image/png", ["image/*"])
    assert forms._check_mime_type_allowed("application/pdf", [".PDF"])
    assert forms._check_mime_type_allowed("text/csv", ["text/csv"])
    assert not forms._check_mime_type_allowed("application/x-msdownload", [".pdf", "image/*"])


@pytest.mark.asyncio
async def test_generate_upload_url_validates_field_constraints_and_returns_metadata():
    form_id = uuid4()
    org_id = uuid4()
    form = SimpleNamespace(
        id=form_id,
        is_active=True,
        fields=[
            SimpleNamespace(
                name="attachment",
                type="file",
                allowed_types=[".pdf"],
                max_size_mb=1,
            )
        ],
    )
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_Result(form))
    ctx = SimpleNamespace(
        org_id=org_id,
        user=SimpleNamespace(
            embed=False,
            user_id=uuid4(),
            is_superuser=False,
            is_external=False,
        ),
    )
    storage = MagicMock()
    storage.generate_presigned_upload_url = AsyncMock(return_value="https://upload")
    storage.presigned_upload_headers.return_value = {"Content-Type": "application/pdf"}

    with (
        patch.object(forms, "_authorize_form_runtime", AsyncMock()),
        patch.object(forms, "_limit_embed_action", AsyncMock()),
        patch.object(forms, "uuid4", return_value=uuid4()),
        patch("src.services.file_storage.FileStorageService", return_value=storage),
    ):
        response = await forms.generate_upload_url(
            form_id,
            SimpleNamespace(),
            FileUploadRequest(
                file_name="../Quarterly Report.pdf",
                content_type="application/pdf",
                file_size=1024,
                field_name="attachment",
            ),
            ctx,
            ctx.user,
            db,
        )

    assert response.upload_url == "https://upload"
    assert response.upload_headers == {"Content-Type": "application/pdf"}
    assert response.blob_uri.endswith("/Quarterly Report.pdf")
    assert response.file_metadata.container == "uploads"
    storage.generate_presigned_upload_url.assert_awaited_once()
    assert f"uploads/{org_id}/" in storage.generate_presigned_upload_url.await_args.kwargs["path"]


@pytest.mark.asyncio
async def test_generate_embed_upload_uses_server_bounded_capability():
    form_id = uuid4()
    org_id = uuid4()
    form = SimpleNamespace(
        id=form_id,
        is_active=True,
        fields=[
            SimpleNamespace(
                name="attachment",
                type="file",
                allowed_types=["application/pdf"],
                max_size_mb=None,
            )
        ],
    )
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_Result(form))
    ctx = SimpleNamespace(
        org_id=org_id,
        user=SimpleNamespace(
            embed=True,
            embed_kind="form",
            form_id=str(form_id),
            jti=str(uuid4()),
            token_exp=2_000_000_000,
            user_id=uuid4(),
            is_superuser=False,
            is_external=True,
        ),
    )
    storage = MagicMock()
    storage.generate_presigned_upload_url = AsyncMock(return_value="https://storage")

    with (
        patch.object(forms, "_authorize_form_runtime", AsyncMock()),
        patch.object(forms, "_limit_embed_action", AsyncMock()),
        patch("src.services.file_storage.FileStorageService", return_value=storage),
    ):
        response = await forms.generate_upload_url(
            form_id,
            SimpleNamespace(),
            FileUploadRequest(
                file_name="report.pdf",
                content_type="application/pdf",
                file_size=1024,
                field_name="attachment",
            ),
            ctx,
            ctx.user,
            db,
        )

    assert response.upload_url == f"/api/forms/{form_id}/upload"
    assert response.upload_headers["Content-Type"] == "application/pdf"
    assert response.upload_headers["Authorization"].startswith("Bearer ")
    storage.generate_presigned_upload_url.assert_not_awaited()

    with (
        patch.object(forms, "_authorize_form_runtime", AsyncMock()),
        patch.object(forms, "_limit_embed_action", AsyncMock()),
        pytest.raises(HTTPException) as exc,
    ):
        await forms.generate_upload_url(
            form_id,
            SimpleNamespace(),
            FileUploadRequest(
                file_name="too-large.pdf",
                content_type="application/pdf",
                file_size=forms.MAX_EMBED_UPLOAD_BYTES + 1,
                field_name="attachment",
            ),
            ctx,
            ctx.user,
            db,
        )
    assert exc.value.status_code == 400
    assert "public upload maximum" in exc.value.detail


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("authorization", "decoded_payload", "expected_status"),
    [
        ("", None, 401),
        ("Bearer invalid", None, 401),
        ("Bearer valid", {"form_id": "another-form"}, 403),
    ],
)
async def test_bounded_upload_rejects_missing_invalid_and_wrong_form_capabilities(
    authorization,
    decoded_payload,
    expected_status,
):
    form_id = uuid4()
    request = _upload_request({"authorization": authorization})

    with (
        patch.object(forms, "decode_token", return_value=decoded_payload) as decode,
        pytest.raises(HTTPException) as exc,
    ):
        await forms.upload_embed_form_content(form_id, request, AsyncMock())

    assert exc.value.status_code == expected_status
    if not authorization:
        decode.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload_overrides", "content_length", "expected_status"),
    [
        ({"file_size": -1}, None, 401),
        ({}, "not-an-integer", 400),
        ({}, "4", 413),
        ({}, "2", 400),
    ],
)
async def test_bounded_upload_rejects_invalid_claims_and_declared_lengths(
    payload_overrides,
    content_length,
    expected_status,
):
    form_id = uuid4()
    headers = {"authorization": "Bearer valid"}
    if content_length is not None:
        headers["content-length"] = content_length
    payload = _upload_payload(form_id, **payload_overrides)

    with (
        patch.object(forms, "decode_token", return_value=payload),
        pytest.raises(HTTPException) as exc,
    ):
        await forms.upload_embed_form_content(
            form_id,
            _upload_request(headers, b"pdf"),
            AsyncMock(),
        )

    assert exc.value.status_code == expected_status


@pytest.mark.asyncio
async def test_bounded_upload_registers_exact_body_and_refuses_replay():
    form_id = uuid4()
    payload = _upload_payload(form_id)
    storage = SimpleNamespace(
        write_raw_chunks_to_s3=AsyncMock(return_value=("etag", 3)),
        delete_raw_from_s3=AsyncMock(),
    )
    reserve = AsyncMock(side_effect=[True, False])
    register = AsyncMock()
    request = _upload_request({"authorization": "Bearer valid"}, b"pdf")

    with (
        patch.object(forms, "decode_token", return_value=payload),
        patch.object(forms, "reserve_form_upload_capability", reserve),
        patch.object(forms, "register_embed_upload_for_session", register),
        patch("src.services.file_storage.FileStorageService", return_value=storage),
    ):
        response = await forms.upload_embed_form_content(
            form_id,
            request,
            AsyncMock(),
        )
        with pytest.raises(HTTPException) as replay:
            await forms.upload_embed_form_content(form_id, request, AsyncMock())

    assert response.status_code == 204
    assert replay.value.status_code == 409
    register.assert_awaited_once_with(
        session_jti=payload["session_jti"],
        session_exp=payload["session_exp"],
        path=payload["path"],
        field_name=payload["field_name"],
        content_type=payload["content_type"],
        file_size=payload["file_size"],
    )
    storage.delete_raw_from_s3.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("write_result", "write_error", "expected_status"),
    [
        (("etag", 2), None, 400),
        (None, forms.FormUploadSizeError(expected=3, actual=4), 413),
        (None, RuntimeError("storage unavailable"), 500),
    ],
)
async def test_bounded_upload_cleans_storage_and_releases_failed_capability(
    write_result,
    write_error,
    expected_status,
):
    form_id = uuid4()
    payload = _upload_payload(form_id)
    write = AsyncMock(return_value=write_result, side_effect=write_error)
    storage = SimpleNamespace(
        write_raw_chunks_to_s3=write,
        delete_raw_from_s3=AsyncMock(),
    )
    release = AsyncMock()

    with (
        patch.object(forms, "decode_token", return_value=payload),
        patch.object(
            forms,
            "reserve_form_upload_capability",
            AsyncMock(return_value=True),
        ),
        patch.object(forms, "release_form_upload_capability", release),
        patch("src.services.file_storage.FileStorageService", return_value=storage),
        pytest.raises(HTTPException) as exc,
    ):
        await forms.upload_embed_form_content(
            form_id,
            _upload_request({"authorization": "Bearer valid"}, b"pdf"),
            AsyncMock(),
        )

    assert exc.value.status_code == expected_status
    storage.delete_raw_from_s3.assert_awaited_once_with(payload["storage_key"])
    release.assert_awaited_once_with(payload["jti"])


@pytest.mark.asyncio
async def test_bounded_upload_cancellation_cleans_storage_and_remains_cancelled():
    form_id = uuid4()
    payload = _upload_payload(form_id)
    storage = SimpleNamespace(
        write_raw_chunks_to_s3=AsyncMock(side_effect=asyncio.CancelledError),
        delete_raw_from_s3=AsyncMock(),
    )
    release = AsyncMock()

    with (
        patch.object(forms, "decode_token", return_value=payload),
        patch.object(
            forms,
            "reserve_form_upload_capability",
            AsyncMock(return_value=True),
        ),
        patch.object(forms, "release_form_upload_capability", release),
        patch("src.services.file_storage.FileStorageService", return_value=storage),
        pytest.raises(asyncio.CancelledError),
    ):
        await forms.upload_embed_form_content(
            form_id,
            _upload_request({"authorization": "Bearer valid"}, b"pdf"),
            AsyncMock(),
        )

    storage.delete_raw_from_s3.assert_awaited_once_with(payload["storage_key"])
    release.assert_awaited_once_with(payload["jti"])


@pytest.mark.asyncio
async def test_generate_upload_url_rejects_disallowed_type_and_oversize():
    form_id = uuid4()
    form = SimpleNamespace(
        id=form_id,
        is_active=True,
        fields=[
            SimpleNamespace(
                name="attachment",
                type="file",
                allowed_types=[".pdf"],
                max_size_mb=1,
            )
        ],
    )
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_Result(form))
    ctx = SimpleNamespace(
        org_id=uuid4(),
        user=SimpleNamespace(
            embed=False,
            user_id=uuid4(),
            is_superuser=False,
            is_external=False,
        ),
    )

    with (
        patch.object(forms, "_authorize_form_runtime", AsyncMock()),
        patch.object(forms, "_limit_embed_action", AsyncMock()),
    ):
        with pytest.raises(HTTPException) as exc:
            await forms.generate_upload_url(
                form_id,
                SimpleNamespace(),
                FileUploadRequest(
                    file_name="bad.exe",
                    content_type="application/x-msdownload",
                    file_size=1,
                    field_name="attachment",
                ),
                ctx,
                ctx.user,
                db,
            )
    assert exc.value.status_code == 400
    assert "not allowed" in exc.value.detail

    with (
        patch.object(forms, "_authorize_form_runtime", AsyncMock()),
        patch.object(forms, "_limit_embed_action", AsyncMock()),
    ):
        with pytest.raises(HTTPException) as exc:
            await forms.generate_upload_url(
                form_id,
                SimpleNamespace(),
                FileUploadRequest(
                    file_name="big.pdf",
                    content_type="application/pdf",
                    file_size=2 * 1024 * 1024,
                    field_name="attachment",
                ),
                ctx,
                ctx.user,
                db,
            )
    assert exc.value.status_code == 400
    assert "exceeds maximum" in exc.value.detail
