import time
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

import requests
from pydantic import BaseModel, Field


JobStatus = Literal[
    "success",
    "cancelled",
    "failed",
    "running",
    "error",
    "error_user",
    "error_app_reloading",
    "error_timeout",
    "expired",
    "stopped",
    "message",
]


class JobCreateRequest(BaseModel):
    method_name: str = Field(..., min_length=1)
    params: dict[str, Any] | None = Field(default_factory=dict)
    poll_result: bool = False
    method_type: str | None = Field(default=None, min_length=1)
    editor_session: UUID | None = None
    events: list[str] = Field(default_factory=list)
    timeout: int = Field(default=86400, ge=1, le=86400)


class JobErrorDetail(BaseModel):
    type: str | None = None
    message: str | None = None
    invalid_fields: dict[str, Any] | None = None


class JobMessage(BaseModel):
    message_type: str | None = None
    message: str | None = None
    timestamp_epoch: int | None = None
    percentage: int | None = None


class DownloadResult(BaseModel):
    url: str


class JobResultPayload(BaseModel):
    model_config = {"extra": "allow"}

    web: dict[str, Any] | None = None
    ifc: dict[str, Any] | None = None
    pdf: dict[str, Any] | None = None
    geojson: dict[str, Any] | None = None
    data: dict[str, Any] | None = None
    image: dict[str, Any] | None = None
    plotly: dict[str, Any] | None = None
    geometry: dict[str, Any] | None = None
    table: dict[str, Any] | None = None
    download: DownloadResult | None = None
    optimization: dict[str, Any] | None = None
    set_params: dict[str, Any] | None = None

    @property
    def download_url(self) -> str | None:
        return self.download.url if self.download else None


class JobCreateResponse(BaseModel):
    uid: int | None = None
    url: str | None = None
    message: str | None = None
    kind: str | None = None
    status: JobStatus | None = None
    error_message: str | None = None
    error_stack_trace: dict[str, Any] | None = None
    invalid_fields: dict[str, Any] | None = None
    content: dict[str, Any] | None = None


class EntityResponse(BaseModel):
    id: int
    name: str
    properties: dict[str, Any] | None = None
    entity_type: int
    entity_type_name: str | None = None
    parent_count: int = 0
    path: list[int] = Field(default_factory=list)


class EditorSessionResponse(BaseModel):
    editor_session: UUID


class JobStatusResponse(BaseModel):
    uid: int
    kind: str
    status: JobStatus
    completed_at: datetime | None = None
    error: JobErrorDetail | None = None
    result: JobResultPayload | None = None
    message: JobMessage | None = None
    log_download_url: str | None = None

    def is_success(self) -> bool:
        return self.status == "success"

    def is_failed(self) -> bool:
        return self.status in {
            "failed",
            "cancelled",
            "error",
            "error_user",
            "error_timeout",
        }

    def get_error_message(self) -> str | None:
        if self.error and self.error.message:
            return self.error.message
        return None

    @property
    def download_url(self) -> str | None:
        return self.result.download_url if self.result else None


class ViktorJobClient:
    def __init__(
        self,
        *,
        api_base: str,
        token: str,
        max_poll_seconds: int,
        connect_timeout: float,
        read_timeout: float,
    ) -> None:
        token = token.strip()
        if not token:
            raise ValueError("Missing VIKTOR token (TOKEN_VK_APP).")

        self.api_base = api_base.rstrip("/")
        self.token = token
        self.max_poll_seconds = max_poll_seconds
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout
        self.auth_headers = {"Authorization": f"Bearer {self.token}"}
        self.json_headers = {**self.auth_headers, "Content-Type": "application/json"}

    @property
    def timeout(self) -> tuple[float, float]:
        return (self.connect_timeout, self.read_timeout)

    def create_job(
        self,
        *,
        workspace_id: int,
        entity_id: int,
        method_name: str,
        params: dict[str, Any],
    ) -> JobStatusResponse:
        payload = JobCreateRequest(
            method_name=method_name,
            params=params,
            poll_result=False,
        ).model_dump(mode="json", exclude_none=True)
        job_url = (
            f"{self.api_base}/workspaces/{workspace_id}/entities/{entity_id}/jobs/"
        )

        response = requests.post(
            job_url,
            headers=self.json_headers,
            json=payload,
            timeout=self.timeout,
        )
        if not response.ok:
            raise RuntimeError(
                f"Job submission failed (status={response.status_code}): "
                f"{response.text[:500]}"
            )

        job_create = JobCreateResponse.model_validate(response.json())
        if job_create.url:
            return self.poll_job(job_create.url)

        if job_create.status == "success":
            result_payload = (
                JobResultPayload.model_validate(job_create.content)
                if job_create.content
                else None
            )
            return JobStatusResponse(
                uid=job_create.uid or 0,
                kind=job_create.kind or "result",
                status="success",
                result=result_payload,
            )

        raise RuntimeError(f"Unexpected job response: {job_create.model_dump()}")

    def get_entity(
        self,
        *,
        workspace_id: int,
        entity_id: int,
        properties: bool = False,
        clean_params: bool = False,
        param_types: bool = False,
    ) -> EntityResponse:
        url = f"{self.api_base}/workspaces/{workspace_id}/entities/{entity_id}/"
        response = requests.get(
            url,
            headers=self.auth_headers,
            params={
                "properties": str(properties).lower(),
                "clean_params": str(clean_params).lower(),
                "param_types": str(param_types).lower(),
            },
            timeout=self.timeout,
        )
        if not response.ok:
            raise RuntimeError(
                f"Get entity failed (status={response.status_code}): "
                f"{response.text[:500]}"
            )
        return EntityResponse.model_validate(response.json())

    def get_parent_entity(
        self,
        *,
        workspace_id: int,
        entity_id: int,
    ) -> EntityResponse | None:
        url = f"{self.api_base}/workspaces/{workspace_id}/entities/{entity_id}/parent/"
        response = requests.get(
            url,
            headers=self.auth_headers,
            timeout=self.timeout,
        )
        if response.status_code == 404:
            return None
        if response.status_code == 403:
            text = response.text
            if "tree" in text.lower():
                return None
        if not response.ok:
            raise RuntimeError(
                f"Get parent entity failed (status={response.status_code}): "
                f"{response.text[:500]}"
            )
        payload = response.json()
        if not payload:
            return None
        return EntityResponse.model_validate(payload)

    def create_entity(
        self,
        *,
        workspace_id: int,
        entity_type: int,
        name: str,
        properties: dict[str, Any],
    ) -> EntityResponse:
        url = f"{self.api_base}/workspaces/{workspace_id}/entities/"
        response = requests.post(
            url,
            headers=self.json_headers,
            json={
                "entity_type": entity_type,
                "name": name,
                "properties": properties,
            },
            timeout=self.timeout,
        )
        if not response.ok:
            raise RuntimeError(
                f"Create entity failed (status={response.status_code}): "
                f"{response.text[:500]}"
            )
        return EntityResponse.model_validate(response.json())

    def create_child_entity(
        self,
        *,
        workspace_id: int,
        parent_entity_id: int,
        entity_type: int,
        name: str,
        properties: dict[str, Any],
    ) -> EntityResponse:
        url = (
            f"{self.api_base}/workspaces/{workspace_id}/entities/"
            f"{parent_entity_id}/entities/"
        )
        response = requests.post(
            url,
            headers=self.json_headers,
            json={
                "entity_type": entity_type,
                "name": name,
                "properties": properties,
            },
            timeout=self.timeout,
        )
        if not response.ok:
            raise RuntimeError(
                f"Create child entity failed (status={response.status_code}): "
                f"{response.text[:500]}"
            )
        payload = response.json()
        if isinstance(payload, list):
            if not payload:
                raise RuntimeError("Create child entity returned an empty response.")
            payload = payload[0]
        return EntityResponse.model_validate(payload)

    def update_entity(
        self,
        *,
        workspace_id: int,
        entity_id: int,
        name: str,
        properties: dict[str, Any],
        message: str,
    ) -> EntityResponse:
        url = f"{self.api_base}/workspaces/{workspace_id}/entities/{entity_id}/"
        response = requests.put(
            url,
            headers=self.json_headers,
            json={
                "name": name,
                "properties": properties,
                "message": message,
            },
            timeout=self.timeout,
        )
        if not response.ok:
            raise RuntimeError(
                f"Update entity failed (status={response.status_code}): "
                f"{response.text[:500]}"
            )
        return EntityResponse.model_validate(response.json())

    def create_editor_session(
        self,
        *,
        workspace_id: int,
        entity_id: int,
    ) -> EditorSessionResponse:
        url = f"{self.api_base}/workspaces/{workspace_id}/entities/{entity_id}/session/"
        response = requests.post(
            url,
            headers=self.auth_headers,
            timeout=self.timeout,
        )
        if not response.ok:
            raise RuntimeError(
                f"Create editor session failed (status={response.status_code}): "
                f"{response.text[:500]}"
            )
        return EditorSessionResponse.model_validate(response.json())

    def build_entity_editor_url(self, *, workspace_id: int, entity_id: int) -> str:
        ui_base = self.api_base[:-4] if self.api_base.endswith("/api") else self.api_base
        return f"{ui_base}/workspaces/{workspace_id}/app/editor/{entity_id}"

    def poll_job(self, job_url: str) -> JobStatusResponse:
        deadline = time.monotonic() + self.max_poll_seconds
        sleep_s = 0.8

        while time.monotonic() < deadline:
            response = requests.get(
                job_url,
                headers=self.auth_headers,
                timeout=self.timeout,
            )
            if not response.ok:
                raise RuntimeError(
                    f"Job polling failed (status={response.status_code}): "
                    f"{response.text[:500]}"
                )

            job = JobStatusResponse.model_validate(response.json())
            if job.is_success():
                return job
            if job.is_failed():
                error = job.get_error_message() or f"status={job.status}"
                raise RuntimeError(f"Job failed: {error}")

            time.sleep(sleep_s)
            sleep_s = min(sleep_s * 1.5, 5.0)

        raise TimeoutError(
            f"Job did not finish within {self.max_poll_seconds} seconds"
        )

    def download_json(self, download_url: str) -> Any:
        response = requests.get(download_url, timeout=self.timeout)
        if not response.ok:
            raise RuntimeError(
                f"Failed to download result (status={response.status_code}): "
                f"{response.text[:500]}"
            )
        return response.json()

    def download_html(self, download_url: str) -> str:
        response = requests.get(download_url, timeout=self.timeout)
        if not response.ok:
            raise RuntimeError(
                f"Failed to download HTML result (status={response.status_code}): "
                f"{response.text[:500]}"
            )
        return response.text
