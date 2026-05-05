from __future__ import annotations

import argparse
from copy import deepcopy
import json
import os
import re
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv
from pydantic import BaseModel, Field

from .models import (
    AppTarget,
    BenchAppReport,
    CaptureSummary,
    EditorSessionResponse,
    EntityResponse,
    EntityTypeResponse,
    JobCreateResponse,
    JobResultPayload,
    JobStatusResponse,
    MethodSpec,
    MethodProbeResult,
    ParametrizationDiffEntry,
    ParametrizationIterationResult,
    ParametrizationLoopReport,
    ParametrizationBenchReport,
    ParamNodeRaw,
    ParamNodeSpec,
    ParametrizationScenario,
    ParamTypeRaw,
    ParametrizationCapture,
    PayloadCandidate,
    PayloadValidationResult,
    ParametrizationResponse,
    PayloadNodeSpec,
    SummaryDelta,
)


FIELD_TYPE_MAP = {
    "text": "str",
    "textarea": "str",
    "number": "float",
    "integer": "int",
    "toggle": "bool",
    "boolean": "bool",
    "date": "str",
    "autocomplete": "str",
    "select": "str",
    "optionfield": "str",
    "color": "dict[str, int]",
}

CONTAINER_NODE_KINDS = {"page", "step", "tab", "section"}
_MISSING = object()
APP_URL_PATTERN = re.compile(
    r"^https?://(?P<host>[^/]+)/workspaces/(?P<workspace_id>\d+)/app/editor/(?P<entity_id>\d+)(?:[/?#].*)?$"
)


class SampleAppConfig(BaseModel):
    app_name: str = Field(default="sample_app")
    api_base: str = Field(default="https://demo.viktor.ai/api")
    workspace_id: int = Field(default=2366)
    entity_id: int = Field(default=11838)
    token: str = Field(default="")
    max_poll_seconds: int = Field(default=120)
    connect_timeout: float = Field(default=10.0)
    read_timeout: float = Field(default=120.0)

    @property
    def timeout(self) -> tuple[float, float]:
        return (self.connect_timeout, self.read_timeout)


class ViktorParametrizationClient:
    def __init__(self, config: SampleAppConfig) -> None:
        token = config.token.strip()
        if not token:
            raise ValueError("Missing VIKTOR token. Set TOKEN_VK_APP in the repo .env file.")

        self.config = config
        self.auth_headers = {"Authorization": f"Bearer {token}"}
        self.json_headers = {**self.auth_headers, "Content-Type": "application/json"}

    def get_entity(self) -> EntityResponse:
        response = requests.get(
            f"{self.config.api_base.rstrip('/')}/workspaces/{self.config.workspace_id}/entities/{self.config.entity_id}/",
            headers=self.auth_headers,
            params={
                "properties": "true",
                "clean_params": "true",
                "param_types": "true",
            },
            timeout=self.config.timeout,
        )
        response.raise_for_status()
        return EntityResponse.model_validate(response.json())

    def get_entity_type(self, entity_type_id: int) -> EntityTypeResponse:
        response = requests.get(
            f"{self.config.api_base.rstrip('/')}/workspaces/{self.config.workspace_id}/entity_types/{entity_type_id}/",
            headers=self.auth_headers,
            timeout=self.config.timeout,
        )
        response.raise_for_status()
        return EntityTypeResponse.model_validate(response.json())

    def create_editor_session(self) -> EditorSessionResponse:
        response = requests.post(
            f"{self.config.api_base.rstrip('/')}/workspaces/{self.config.workspace_id}/entities/{self.config.entity_id}/session/",
            headers=self.auth_headers,
            timeout=self.config.timeout,
        )
        response.raise_for_status()
        return EditorSessionResponse.model_validate(response.json())

    def get_parametrization(
        self,
        editor_session: str,
        params: dict[str, Any] | None = None,
    ) -> ParametrizationResponse:
        response = requests.post(
            f"{self.config.api_base.rstrip('/')}/workspaces/{self.config.workspace_id}/entities/{self.config.entity_id}/parametrization/",
            headers=self.json_headers,
            json={
                "editor_session": editor_session,
                "params": params or {},
            },
            timeout=self.config.timeout,
        )
        response.raise_for_status()
        return ParametrizationResponse.model_validate(response.json())

    def create_job(
        self,
        *,
        method_name: str,
        params: dict[str, Any],
    ) -> JobStatusResponse:
        response = requests.post(
            f"{self.config.api_base.rstrip('/')}/workspaces/{self.config.workspace_id}/entities/{self.config.entity_id}/jobs/",
            headers=self.json_headers,
            json={
                "method_name": method_name,
                "params": params,
                "poll_result": False,
            },
            timeout=self.config.timeout,
        )
        response.raise_for_status()
        job_create = JobCreateResponse.model_validate(response.json())

        if job_create.url:
            return self.poll_job(job_create.url)

        if job_create.status == "success":
            return JobStatusResponse(
                uid=job_create.uid,
                kind=job_create.kind,
                status="success",
                result=(
                    JobResultPayload.model_validate(job_create.content)
                    if job_create.content
                    else None
                ),
            )

        if job_create.status:
            return JobStatusResponse(
                uid=job_create.uid,
                kind=job_create.kind,
                status=job_create.status,
                error={"message": job_create.error_message} if job_create.error_message else None,
            )

        raise RuntimeError(f"Unexpected job creation response: {job_create.model_dump(mode='json')}")

    def poll_job(self, job_url: str) -> JobStatusResponse:
        started = time.monotonic()
        while True:
            response = requests.get(
                job_url,
                headers=self.auth_headers,
                timeout=self.config.timeout,
            )
            response.raise_for_status()
            payload = response.json()
            if payload.get("result") is not None and isinstance(payload.get("result"), dict):
                payload["result"] = JobResultPayload.model_validate(payload["result"])
            job = JobStatusResponse.model_validate(payload)

            if job.status == "success":
                return job
            if job.status in {
                "failed",
                "cancelled",
                "error",
                "error_user",
                "error_timeout",
                "expired",
                "stopped",
            }:
                return job

            if time.monotonic() - started > self.config.max_poll_seconds:
                raise TimeoutError(
                    f"Timed out waiting for job {job_url} after {self.config.max_poll_seconds} seconds."
                )

            time.sleep(1.0)


def load_repo_env() -> None:
    for parent in Path(__file__).resolve().parents:
        env_path = parent / ".env"
        if env_path.exists():
            load_dotenv(env_path)
            return


def build_sample_app_config() -> SampleAppConfig:
    load_repo_env()
    return SampleAppConfig(
        app_name="sample_app_2366_11838",
        api_base=(os.getenv("VIKTOR_API_BASE") or "https://demo.viktor.ai/api").strip(),
        workspace_id=2366,
        entity_id=11838,
        token=(os.getenv("TOKEN_VK_APP") or "").strip(),
        max_poll_seconds=int(os.getenv("VIKTOR_MAX_POLL_SECONDS") or "120"),
    )


def build_app_config(
    *,
    workspace_id: int,
    entity_id: int,
    app_name: str,
    api_base: str | None = None,
) -> SampleAppConfig:
    load_repo_env()
    return SampleAppConfig(
        app_name=app_name,
        api_base=(api_base or os.getenv("VIKTOR_API_BASE") or "https://demo.viktor.ai/api").strip(),
        workspace_id=workspace_id,
        entity_id=entity_id,
        token=(os.getenv("TOKEN_VK_APP") or "").strip(),
        max_poll_seconds=int(os.getenv("VIKTOR_MAX_POLL_SECONDS") or "120"),
    )


def build_config_for_target(target: AppTarget) -> SampleAppConfig:
    return build_app_config(
        workspace_id=target.workspace_id,
        entity_id=target.entity_id,
        app_name=target.name,
        api_base=target.api_base,
    )


def build_default_bench_targets() -> list[AppTarget]:
    load_repo_env()
    default_api_base = (os.getenv("VIKTOR_API_BASE") or "https://demo.viktor.ai/api").strip()
    return [
        AppTarget(
            name="viktor_demo_2232_11640",
            workspace_id=2232,
            entity_id=11640,
            api_base=default_api_base,
            description="User-provided VIKTOR demo app at workspace 2232 / entity 11640.",
        ),
        AppTarget(
            name="viktor_demo_2141_11536",
            workspace_id=2141,
            entity_id=11536,
            api_base=default_api_base,
            description="User-provided VIKTOR demo app at workspace 2141 / entity 11536.",
        ),
    ]


def parse_app_url(app_url: str) -> dict[str, Any]:
    match = APP_URL_PATTERN.match(app_url.strip())
    if not match:
        raise ValueError(f"Unsupported VIKTOR app URL: {app_url}")

    host = match.group("host")
    return {
        "host": host,
        "api_base": f"https://{host}/api",
        "workspace_id": int(match.group("workspace_id")),
        "entity_id": int(match.group("entity_id")),
    }


def build_target_from_app_url(
    app_url: str,
    *,
    name: str | None = None,
    description: str | None = None,
) -> AppTarget:
    parsed = parse_app_url(app_url)
    workspace_id = parsed["workspace_id"]
    entity_id = parsed["entity_id"]
    target_name = name or f"app_{workspace_id}_{entity_id}"
    return AppTarget(
        name=target_name,
        workspace_id=workspace_id,
        entity_id=entity_id,
        api_base=parsed["api_base"],
        description=description,
    )


def build_target_slug(target: AppTarget) -> str:
    return f"{target.workspace_id}_{target.entity_id}_{target.name}".replace("/", "_").replace(" ", "_")


def split_param_path(name: str | None) -> list[str]:
    if not name:
        return []
    return [part for part in name.split(".") if part]


def classify_node_kind(raw_type: str) -> str:
    lowered = raw_type.strip().lower()
    if lowered in CONTAINER_NODE_KINDS:
        return lowered
    if lowered == "dynamicarray":
        return "dynamic_array"
    if lowered == "table":
        return "table"
    if "button" in lowered:
        return "action"
    if lowered:
        return "field"
    return "unknown"


def classify_method_spec(
    *,
    kind: str,
    view_type: str | None,
    raw_node_type: str | None,
) -> tuple[str, str | None]:
    lowered_view_type = (view_type or "").strip().lower()
    lowered_node_type = (raw_node_type or "").strip().lower()

    if kind == "view":
        if lowered_view_type == "web":
            return ("webview", "web")
        if lowered_view_type == "data":
            return ("dataview", "data")
        if lowered_view_type == "table":
            return ("tableview", "table")
        if lowered_view_type == "plotly":
            return ("plotlyview", "plotly")
        if lowered_view_type == "pdf":
            return ("pdfview", "pdf")
        if lowered_view_type:
            return (f"{lowered_view_type}_view", lowered_view_type)
        return ("view", None)

    if kind == "button":
        if lowered_node_type == "download-button":
            return ("download_button", "download")
        return ("button", None)

    if kind == "preprocess":
        return ("preprocess", None)

    return (kind, None)


def extract_option_values(options: Any) -> list[str]:
    if options is None:
        return []

    if isinstance(options, list):
        values: list[str] = []
        for item in options:
            if isinstance(item, dict):
                label = item.get("label") or item.get("value") or item.get("name")
                if label is not None:
                    values.append(str(label))
            else:
                values.append(str(item))
        return values

    if isinstance(options, dict):
        if isinstance(options.get("options"), list):
            return extract_option_values(options["options"])
        return [f"{key}={value}" for key, value in sorted(options.items())]

    return [str(options)]


def infer_python_type_hint(
    node: ParamNodeRaw,
    param_type: ParamTypeRaw | None,
) -> str | None:
    node_kind = classify_node_kind(node.type)
    base_type = (param_type.type or node.type).strip().lower() if param_type else node.type.strip().lower()

    if node_kind == "dynamic_array":
        return "list[dict[str, Any]]"
    if node_kind == "table":
        return "list[dict[str, Any]]"
    if node_kind == "action":
        return None
    if node_kind in CONTAINER_NODE_KINDS:
        return "dict[str, Any]"

    if node.multiple or isinstance(node.default, list):
        if base_type in {"select", "optionfield", "text"}:
            return "list[str]"
        return "list[Any]"

    return FIELD_TYPE_MAP.get(base_type, "Any")


def build_metadata(node: ParamNodeRaw, param_type: ParamTypeRaw | None) -> list[str]:
    metadata: list[str] = []

    if node.ui_name:
        metadata.append(f"label: {node.ui_name}")
    if node.title:
        metadata.append(f"title: {node.title}")
    if node.description:
        metadata.append(f"description: {node.description}")
    if node.default is not None:
        metadata.append(f"default: {node.default!r}")
    if node.suffix:
        metadata.append(f"suffix: {node.suffix}")
    if node.variant:
        metadata.append(f"variant: {node.variant}")
    if node.views:
        metadata.append(f"views: {', '.join(node.views)}")
    if node.method:
        metadata.append(f"method: {node.method}")

    option_values = extract_option_values(node.options)
    if option_values:
        metadata.append(f"options: {', '.join(option_values)}")

    if param_type and param_type.type:
        metadata.append(f"param_type: {param_type.type}")
    if param_type and param_type.sub_types:
        metadata.append(f"sub_types: {', '.join(sorted(param_type.sub_types))}")

    return metadata


def extract_node_extra(node: ParamNodeRaw) -> dict[str, Any]:
    known_fields = set(ParamNodeRaw.model_fields)
    payload = node.model_dump(mode="json")
    extra: dict[str, Any] = {}
    for key, value in payload.items():
        if key in known_fields:
            continue
        if value in (None, "", [], {}):
            continue
        extra[key] = value
    return extra


def node_has_explicit_default(node: ParamNodeRaw) -> bool:
    return "default" in node.model_fields_set


def collect_leaf_field_paths(nodes: Iterable[ParamNodeRaw]) -> list[str]:
    field_paths: list[str] = []
    for node in nodes:
        node_kind = classify_node_kind(node.type)
        if node_kind in {"field", "table", "dynamic_array"} and node.name:
            field_paths.append(node.name)
        field_paths.extend(collect_leaf_field_paths(node.content))
    return field_paths


def extract_default_payload_from_node(node: ParamNodeRaw) -> Any:
    node_kind = classify_node_kind(node.type)
    child_payload = extract_default_payload_from_nodes(node.content)

    if node_kind in CONTAINER_NODE_KINDS:
        return child_payload if child_payload else _MISSING

    if node_kind == "action":
        return _MISSING

    if node.name and node_has_explicit_default(node):
        return build_nested_override(split_param_path(node.name), deepcopy(node.default))

    if child_payload:
        return child_payload

    return _MISSING


def extract_default_payload_from_nodes(nodes: Iterable[ParamNodeRaw]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for node in nodes:
        node_payload = extract_default_payload_from_node(node)
        if node_payload is _MISSING:
            continue
        merged = deep_merge(merged, node_payload)
    return merged


def collect_explicit_default_paths(nodes: Iterable[ParamNodeRaw]) -> list[str]:
    paths: list[str] = []
    for node in nodes:
        if node.name and classify_node_kind(node.type) in {"field", "table", "dynamic_array"} and node_has_explicit_default(node):
            paths.append(node.name)
        paths.extend(collect_explicit_default_paths(node.content))
    return paths


def path_exists_in_payload(payload: Any, path: list[str]) -> bool:
    current = payload
    for part in path:
        if not isinstance(current, dict) or part not in current:
            return False
        current = current[part]
    return True


def merge_missing_with_fallback(primary: Any, fallback: Any) -> Any:
    if isinstance(primary, dict) and isinstance(fallback, dict):
        merged = deepcopy(primary)
        for key, fallback_value in fallback.items():
            if key not in merged:
                merged[key] = deepcopy(fallback_value)
                continue
            merged[key] = merge_missing_with_fallback(merged[key], fallback_value)
        return merged

    return deepcopy(primary)


def build_default_payload_candidate(
    parametrization: ParametrizationResponse,
    saved_params: dict[str, Any],
    *,
    name: str = "defaults_only",
    backfill_missing: bool = False,
) -> PayloadCandidate:
    parametrization_nodes = parametrization.content.parametrization
    explicit_default_paths = sorted(set(collect_explicit_default_paths(parametrization_nodes)))
    all_leaf_paths = sorted(set(collect_leaf_field_paths(parametrization_nodes)))
    default_params = extract_default_payload_from_nodes(parametrization_nodes)

    missing_default_paths = [
        field_path
        for field_path in all_leaf_paths
        if not path_exists_in_payload(default_params, split_param_path(field_path))
    ]

    if backfill_missing:
        params = merge_missing_with_fallback(default_params, saved_params)
        candidate_name = "defaults_plus_saved"
    else:
        params = default_params
        candidate_name = name

    return PayloadCandidate(
        name=candidate_name,
        params=params,
        payload_tree=build_payload_tree(params, []),
        explicit_default_paths=explicit_default_paths,
        missing_default_paths=missing_default_paths,
    )


def normalize_param_node(
    node: ParamNodeRaw,
    param_types: dict[str, ParamTypeRaw],
) -> ParamNodeSpec:
    payload_path = split_param_path(node.name)
    param_type = param_types.get(node.name or "")
    normalized_children = [
        normalize_param_node(child, param_types)
        for child in node.content
    ]

    return ParamNodeSpec(
        node_kind=classify_node_kind(node.type),
        raw_type=node.type,
        name=node.name,
        title=node.title,
        label=node.ui_name,
        path=payload_path,
        payload_path=payload_path,
        views=list(node.views),
        method=node.method,
        default=node.default,
        python_type_hint=infer_python_type_hint(node, param_type),
        metadata=build_metadata(node, param_type),
        extra=extract_node_extra(node),
        children=normalized_children,
    )


def deep_merge(base: Any, override: Any) -> Any:
    if isinstance(base, dict) and isinstance(override, dict):
        merged = deepcopy(base)
        for key, value in override.items():
            if key in merged:
                merged[key] = deep_merge(merged[key], value)
            else:
                merged[key] = deepcopy(value)
        return merged

    return deepcopy(override)


def build_nested_override(path: list[str], value: Any) -> dict[str, Any]:
    nested: Any = deepcopy(value)
    for key in reversed(path):
        nested = {key: nested}
    return nested


def iter_payload_values(
    value: Any,
    path: list[str] | None = None,
) -> Iterable[tuple[list[str], Any]]:
    current_path = path or []
    yield current_path, value

    if isinstance(value, dict):
        for key, child_value in value.items():
            yield from iter_payload_values(child_value, current_path + [key])
    elif isinstance(value, list):
        for index, child_value in enumerate(value):
            yield from iter_payload_values(child_value, current_path + [f"[{index}]"])


def is_list_of_strings(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(item, str) for item in value)


def is_list_of_objects(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(item, dict) for item in value)


def mutate_scalar_value(value: Any) -> Any:
    if isinstance(value, bool):
        return not value
    if isinstance(value, int) and not isinstance(value, bool):
        return value + 1
    if isinstance(value, float):
        return round(value + 1.0, 3)
    if isinstance(value, str):
        return f"{value} loop"
    return value


def mutate_object_row(row: dict[str, Any]) -> dict[str, Any]:
    mutated = deepcopy(row)
    for key, value in mutated.items():
        new_value = mutate_scalar_value(value)
        if new_value != value:
            mutated[key] = new_value
            return mutated
    return mutated


def build_auto_scenarios(
    base_params: dict[str, Any],
    max_scenarios: int = 4,
) -> list[ParametrizationScenario]:
    scenarios: list[ParametrizationScenario] = []
    used_paths: set[tuple[str, ...]] = set()

    bool_candidate: tuple[list[str], Any] | None = None
    numeric_candidate: tuple[list[str], Any] | None = None
    string_list_candidate: tuple[list[str], Any] | None = None
    object_list_candidate: tuple[list[str], Any] | None = None
    text_candidate: tuple[list[str], Any] | None = None

    for path, value in iter_payload_values(base_params):
        normalized_path = [part for part in path if not part.startswith("[")]
        path_key = tuple(normalized_path)
        if not normalized_path or path_key in used_paths:
            continue

        if bool_candidate is None and isinstance(value, bool):
            bool_candidate = (normalized_path, value)
            used_paths.add(path_key)
            continue

        if numeric_candidate is None and isinstance(value, (int, float)) and not isinstance(value, bool):
            numeric_candidate = (normalized_path, value)
            used_paths.add(path_key)
            continue

        if string_list_candidate is None and is_list_of_strings(value):
            string_list_candidate = (normalized_path, value)
            used_paths.add(path_key)
            continue

        if object_list_candidate is None and is_list_of_objects(value):
            object_list_candidate = (normalized_path, value)
            used_paths.add(path_key)
            continue

        if text_candidate is None and isinstance(value, str):
            text_candidate = (normalized_path, value)
            used_paths.add(path_key)

    if bool_candidate and len(scenarios) < max_scenarios:
        path, value = bool_candidate
        scenarios.append(
            ParametrizationScenario(
                name=f"toggle_{'_'.join(path)}",
                description=f"Toggle boolean field at {'.'.join(path)}.",
                overrides=build_nested_override(path, not value),
            )
        )

    if numeric_candidate and len(scenarios) < max_scenarios:
        path, value = numeric_candidate
        updated_value = value + 1 if isinstance(value, int) else round(value + 1.0, 3)
        scenarios.append(
            ParametrizationScenario(
                name=f"adjust_{'_'.join(path)}",
                description=f"Adjust numeric field at {'.'.join(path)}.",
                overrides=build_nested_override(path, updated_value),
            )
        )

    if string_list_candidate and len(scenarios) < max_scenarios:
        path, value = string_list_candidate
        rotated_value = value[1:] + value[:1] if len(value) > 1 else value
        scenarios.append(
            ParametrizationScenario(
                name=f"rotate_{'_'.join(path)}",
                description=f"Rotate list[str] field at {'.'.join(path)}.",
                overrides=build_nested_override(path, rotated_value),
            )
        )

    if object_list_candidate and len(scenarios) < max_scenarios:
        path, value = object_list_candidate
        updated_list = deepcopy(value)
        updated_list.append(mutate_object_row(value[0]))
        scenarios.append(
            ParametrizationScenario(
                name=f"extend_{'_'.join(path)}",
                description=f"Append one item to list[dict] field at {'.'.join(path)}.",
                overrides=build_nested_override(path, updated_list),
            )
        )

    if text_candidate and len(scenarios) < max_scenarios:
        path, value = text_candidate
        scenarios.append(
            ParametrizationScenario(
                name=f"rewrite_{'_'.join(path)}",
                description=f"Update text field at {'.'.join(path)}.",
                overrides=build_nested_override(path, f"{value} loop"),
            )
        )

    return scenarios


def load_scenarios_from_json(path: Path) -> list[ParametrizationScenario]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        payload = [payload]
    if not isinstance(payload, list):
        raise ValueError("Scenario file must contain a JSON object or list of objects.")
    return [ParametrizationScenario.model_validate(item) for item in payload]


def iter_param_nodes(nodes: Iterable[ParamNodeRaw]) -> Iterable[ParamNodeRaw]:
    for node in nodes:
        yield node
        yield from iter_param_nodes(node.content)


def extract_methods(
    entity_type: EntityTypeResponse,
    parametrization: ParametrizationResponse,
) -> list[MethodSpec]:
    methods: dict[tuple[str, str], MethodSpec] = {}

    if entity_type.preprocess_method:
        key = ("preprocess", entity_type.preprocess_method)
        methods[key] = MethodSpec(
            kind="preprocess",
            method_name=entity_type.preprocess_method,
            source="entity_type",
        )

    for view in entity_type.views:
        if not view.controller_method:
            continue
        method_classification, expected_result_kind = classify_method_spec(
            kind="view",
            view_type=view.view_type,
            raw_node_type=None,
        )
        key = ("view", view.controller_method)
        methods[key] = MethodSpec(
            kind="view",
            method_name=view.controller_method,
            source="entity_type",
            label=view.label,
            view_type=view.view_type,
            automatic_update=view.automatic_update,
            method_classification=method_classification,
            expected_result_kind=expected_result_kind,
        )

    for node in iter_param_nodes(parametrization.content.parametrization):
        if not node.method:
            continue
        method_classification, expected_result_kind = classify_method_spec(
            kind="button",
            view_type=None,
            raw_node_type=node.type,
        )
        key = ("button", node.method)
        methods[key] = MethodSpec(
            kind="button",
            method_name=node.method,
            source="parametrization",
            label=node.ui_name or node.title,
            source_path=split_param_path(node.name),
            raw_node_type=node.type,
            method_classification=method_classification,
            expected_result_kind=expected_result_kind,
        )

    return list(methods.values())


def merge_values(values: list[Any]) -> Any:
    filtered = [value for value in values if value is not None]
    if not filtered:
        return None

    if all(isinstance(value, dict) for value in filtered):
        merged: dict[str, Any] = {}
        all_keys = sorted({key for value in filtered for key in value})
        for key in all_keys:
            merged[key] = merge_values([value.get(key) for value in filtered])
        return merged

    if all(isinstance(value, list) for value in filtered):
        flattened: list[Any] = []
        for value in filtered:
            flattened.extend(value)
        if not flattened:
            return []
        return [merge_values(flattened)]

    return filtered[0]


def infer_scalar_type(value: Any) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int) and not isinstance(value, bool):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    return "Any"


def build_payload_tree(value: Any, path: list[str] | None = None) -> PayloadNodeSpec:
    current_path = path or []

    if value is None:
        return PayloadNodeSpec(
            value_kind="null",
            path=current_path,
            python_type_hint="None",
            example=None,
        )

    if isinstance(value, dict):
        children = [
            build_payload_tree(child_value, current_path + [child_key])
            for child_key, child_value in sorted(value.items())
        ]
        return PayloadNodeSpec(
            value_kind="object",
            path=current_path,
            python_type_hint="dict[str, Any]",
            example=None,
            children=children,
        )

    if isinstance(value, list):
        if not value:
            return PayloadNodeSpec(
                value_kind="list",
                path=current_path,
                python_type_hint="list[Any]",
                example=[],
            )

        representative_item = merge_values(value)
        item_node = build_payload_tree(representative_item, current_path + ["[]"])

        if item_node.value_kind == "scalar":
            python_type_hint = f"list[{item_node.python_type_hint}]"
        elif item_node.value_kind == "null":
            python_type_hint = "list[Any]"
        else:
            python_type_hint = "list[dict[str, Any]]"

        return PayloadNodeSpec(
            value_kind="list",
            path=current_path,
            python_type_hint=python_type_hint,
            example=value[:2],
            item=item_node,
        )

    return PayloadNodeSpec(
        value_kind="scalar",
        path=current_path,
        python_type_hint=infer_scalar_type(value),
        example=value,
    )


def build_summary(
    parametrization_tree: list[ParamNodeSpec],
    methods: list[MethodSpec],
) -> CaptureSummary:
    total_nodes = 0
    total_containers = 0
    total_fields = 0
    total_actions = 0

    def walk(node: ParamNodeSpec) -> None:
        nonlocal total_nodes, total_containers, total_fields, total_actions
        total_nodes += 1
        if node.node_kind in CONTAINER_NODE_KINDS:
            total_containers += 1
        elif node.node_kind in {"field", "table", "dynamic_array"}:
            total_fields += 1
        elif node.node_kind == "action":
            total_actions += 1

        for child in node.children:
            walk(child)

    for top_level_node in parametrization_tree:
        walk(top_level_node)

    return CaptureSummary(
        total_nodes=total_nodes,
        total_containers=total_containers,
        total_fields=total_fields,
        total_actions=total_actions,
        total_methods=len(methods),
    )


def build_summary_delta(
    baseline: CaptureSummary,
    current: CaptureSummary,
) -> SummaryDelta:
    return SummaryDelta(
        total_nodes=current.total_nodes - baseline.total_nodes,
        total_containers=current.total_containers - baseline.total_containers,
        total_fields=current.total_fields - baseline.total_fields,
        total_actions=current.total_actions - baseline.total_actions,
        total_methods=current.total_methods - baseline.total_methods,
    )


def node_identity(node: ParamNodeSpec, ancestry: list[str], index: int) -> str:
    if node.path:
        return ".".join(node.path)

    fallback = node.name or node.title or node.label or node.raw_type
    return "/".join(ancestry + [f"{node.node_kind}[{index}]::{fallback}"])


def flatten_parametrization_tree(
    nodes: list[ParamNodeSpec],
    ancestry: list[str] | None = None,
) -> dict[str, dict[str, Any]]:
    current_ancestry = ancestry or []
    flattened: dict[str, dict[str, Any]] = {}

    for index, node in enumerate(nodes):
        identity = node_identity(node, current_ancestry, index)
        flattened[identity] = {
            "node_kind": node.node_kind,
            "raw_type": node.raw_type,
            "title": node.title,
            "label": node.label,
            "views": node.views,
            "method": node.method,
            "python_type_hint": node.python_type_hint,
            "metadata": node.metadata,
            "extra": node.extra,
        }
        flattened.update(flatten_parametrization_tree(node.children, current_ancestry + [identity]))

    return flattened


def flatten_methods(methods: list[MethodSpec]) -> dict[str, dict[str, Any]]:
    return {
        f"method::{method.method_name}": method.model_dump(mode="json")
        for method in methods
    }


def detect_result_kind(result: JobResultPayload | None) -> tuple[str | None, list[str], dict[str, Any]]:
    if result is None:
        return (None, [], {})

    payload = result.model_dump(mode="json", exclude_none=True)
    result_keys = sorted(payload.keys())
    actual_result_kind = result_keys[0] if result_keys else None
    result_summary: dict[str, Any] = {}

    if actual_result_kind == "web":
        result_summary["url"] = payload.get("web", {}).get("url")
    elif actual_result_kind == "download":
        result_summary["url"] = payload.get("download", {}).get("url")
    elif actual_result_kind in {"plotly", "data", "table", "pdf", "image", "geometry"}:
        value = payload.get(actual_result_kind)
        if isinstance(value, dict):
            result_summary["keys"] = sorted(value.keys())[:10]

    return (actual_result_kind, result_keys, result_summary)


def diff_flat_maps(
    baseline: dict[str, dict[str, Any]],
    current: dict[str, dict[str, Any]],
) -> list[ParametrizationDiffEntry]:
    diffs: list[ParametrizationDiffEntry] = []
    all_paths = sorted(set(baseline) | set(current))

    for path in all_paths:
        before = baseline.get(path)
        after = current.get(path)
        if before is None and after is not None:
            diffs.append(
                ParametrizationDiffEntry(path=path, change_type="added", before=None, after=after)
            )
        elif before is not None and after is None:
            diffs.append(
                ParametrizationDiffEntry(path=path, change_type="removed", before=before, after=None)
            )
        elif before != after:
            diffs.append(
                ParametrizationDiffEntry(path=path, change_type="changed", before=before, after=after)
            )

    return diffs


def normalize_iteration(
    *,
    entity: EntityResponse,
    entity_type: EntityTypeResponse,
    editor_session: str,
    scenario: ParametrizationScenario,
    params_used: dict[str, Any],
    parametrization: ParametrizationResponse,
    baseline_summary: CaptureSummary | None = None,
    baseline_tree: list[ParamNodeSpec] | None = None,
    baseline_methods: list[MethodSpec] | None = None,
) -> ParametrizationIterationResult:
    parametrization_tree = [
        normalize_param_node(node, entity.param_types)
        for node in parametrization.content.parametrization
    ]
    methods = extract_methods(entity_type, parametrization)
    payload_tree = build_payload_tree(params_used, [])
    summary = build_summary(parametrization_tree, methods)

    diffs: list[ParametrizationDiffEntry] = []
    summary_delta = SummaryDelta()
    if baseline_summary is not None and baseline_tree is not None and baseline_methods is not None:
        baseline_map = flatten_parametrization_tree(baseline_tree)
        baseline_map.update(flatten_methods(baseline_methods))
        current_map = flatten_parametrization_tree(parametrization_tree)
        current_map.update(flatten_methods(methods))
        diffs = diff_flat_maps(baseline_map, current_map)
        summary_delta = build_summary_delta(baseline_summary, summary)

    return ParametrizationIterationResult(
        scenario=scenario,
        params_used=params_used,
        editor_session=editor_session,
        parametrization=parametrization,
        methods=methods,
        parametrization_tree=parametrization_tree,
        payload_tree=payload_tree,
        summary=summary,
        summary_delta=summary_delta,
        diffs=diffs,
    )


def validate_payload_candidate(
    *,
    client: ViktorParametrizationClient,
    entity: EntityResponse,
    entity_type: EntityTypeResponse,
    baseline_iteration: ParametrizationIterationResult,
    candidate: PayloadCandidate,
) -> PayloadValidationResult:
    editor_session = client.create_editor_session()

    try:
        parametrization = client.get_parametrization(
            str(editor_session.editor_session),
            params=deepcopy(candidate.params),
        )
    except requests.RequestException as exc:
        return PayloadValidationResult(
            name=candidate.name,
            success=False,
            params_used=deepcopy(candidate.params),
            payload_tree=candidate.payload_tree,
            error=str(exc),
        )
    except Exception as exc:
        return PayloadValidationResult(
            name=candidate.name,
            success=False,
            params_used=deepcopy(candidate.params),
            payload_tree=candidate.payload_tree,
            error=str(exc),
        )

    iteration = normalize_iteration(
        entity=entity,
        entity_type=entity_type,
        editor_session=str(editor_session.editor_session),
        scenario=ParametrizationScenario(
            name=candidate.name,
            description=f"Validation run for payload candidate '{candidate.name}'.",
            overrides={},
        ),
        params_used=deepcopy(candidate.params),
        parametrization=parametrization,
        baseline_summary=baseline_iteration.summary,
        baseline_tree=baseline_iteration.parametrization_tree,
        baseline_methods=baseline_iteration.methods,
    )

    return PayloadValidationResult(
        name=candidate.name,
        success=True,
        params_used=deepcopy(candidate.params),
        payload_tree=candidate.payload_tree,
        validated_summary=iteration.summary,
        diff_count_vs_baseline=len(iteration.diffs),
        parametrization_tree=iteration.parametrization_tree,
        methods=iteration.methods,
    )


def probe_methods(
    *,
    client: ViktorParametrizationClient,
    methods: list[MethodSpec],
    params: dict[str, Any],
) -> list[MethodProbeResult]:
    probes: list[MethodProbeResult] = []

    for method in methods:
        try:
            job = client.create_job(
                method_name=method.method_name,
                params=deepcopy(params),
            )
            actual_result_kind, result_keys, result_summary = detect_result_kind(job.result)
            probes.append(
                MethodProbeResult(
                    method_name=method.method_name,
                    label=method.label,
                    declared_kind=method.method_classification,
                    declared_view_type=method.view_type or method.raw_node_type,
                    expected_result_kind=method.expected_result_kind,
                    invocation_success=job.status == "success",
                    job_status=job.status,
                    actual_result_kind=actual_result_kind,
                    result_keys=result_keys,
                    result_summary=result_summary,
                    job_kind=job.kind,
                    error=job.error.get("message") if isinstance(job.error, dict) else None,
                )
            )
        except Exception as exc:
            probes.append(
                MethodProbeResult(
                    method_name=method.method_name,
                    label=method.label,
                    declared_kind=method.method_classification,
                    declared_view_type=method.view_type or method.raw_node_type,
                    expected_result_kind=method.expected_result_kind,
                    invocation_success=False,
                    error=str(exc),
                )
            )

    return probes


def capture_sample_app(config: SampleAppConfig | None = None) -> ParametrizationCapture:
    effective_config = config or build_sample_app_config()
    client = ViktorParametrizationClient(effective_config)

    entity = client.get_entity()
    entity_type = client.get_entity_type(entity.entity_type)
    editor_session = client.create_editor_session()
    parametrization = client.get_parametrization(
        str(editor_session.editor_session),
        params=deepcopy(entity.properties),
    )

    parametrization_tree = [
        normalize_param_node(node, entity.param_types)
        for node in parametrization.content.parametrization
    ]
    methods = extract_methods(entity_type, parametrization)
    payload_tree = build_payload_tree(entity.properties, [])
    summary = build_summary(parametrization_tree, methods)

    return ParametrizationCapture(
        api_base=effective_config.api_base,
        workspace_id=effective_config.workspace_id,
        entity_id=effective_config.entity_id,
        entity=entity,
        entity_type=entity_type,
        editor_session=editor_session.editor_session,
        parametrization=parametrization,
        methods=methods,
        parametrization_tree=parametrization_tree,
        payload_tree=payload_tree,
        summary=summary,
    )


def run_parametrization_testing_loop(
    config: SampleAppConfig | None = None,
    scenarios: list[ParametrizationScenario] | None = None,
    max_auto_scenarios: int = 4,
) -> ParametrizationLoopReport:
    effective_config = config or build_sample_app_config()
    client = ViktorParametrizationClient(effective_config)

    entity = client.get_entity()
    entity_type = client.get_entity_type(entity.entity_type)
    base_params = deepcopy(entity.properties)

    baseline_editor_session = client.create_editor_session()
    baseline_parametrization = client.get_parametrization(
        str(baseline_editor_session.editor_session),
        params=base_params,
    )
    baseline_iteration = normalize_iteration(
        entity=entity,
        entity_type=entity_type,
        editor_session=str(baseline_editor_session.editor_session),
        scenario=ParametrizationScenario(
            name="baseline",
            description="Current saved entity properties.",
            overrides={},
        ),
        params_used=base_params,
        parametrization=baseline_parametrization,
    )

    defaults_editor_session = client.create_editor_session()
    defaults_parametrization = client.get_parametrization(
        str(defaults_editor_session.editor_session),
        params={},
    )
    default_payload = build_default_payload_candidate(
        defaults_parametrization,
        base_params,
        name="defaults_only",
        backfill_missing=False,
    )
    default_backfilled_payload = build_default_payload_candidate(
        defaults_parametrization,
        base_params,
        name="defaults_plus_saved",
        backfill_missing=True,
    )
    default_validation = validate_payload_candidate(
        client=client,
        entity=entity,
        entity_type=entity_type,
        baseline_iteration=baseline_iteration,
        candidate=default_payload,
    )
    default_backfilled_validation = validate_payload_candidate(
        client=client,
        entity=entity,
        entity_type=entity_type,
        baseline_iteration=baseline_iteration,
        candidate=default_backfilled_payload,
    )
    method_probe_params = default_backfilled_payload.params or base_params
    method_probes = probe_methods(
        client=client,
        methods=baseline_iteration.methods,
        params=method_probe_params,
    )

    effective_scenarios = scenarios or build_auto_scenarios(base_params, max_scenarios=max_auto_scenarios)
    iterations: list[ParametrizationIterationResult] = []
    for scenario in effective_scenarios:
        iteration_params = deep_merge(base_params, scenario.overrides)
        iteration_editor_session = client.create_editor_session()
        iteration_parametrization = client.get_parametrization(
            str(iteration_editor_session.editor_session),
            params=iteration_params,
        )
        iterations.append(
            normalize_iteration(
                entity=entity,
                entity_type=entity_type,
                editor_session=str(iteration_editor_session.editor_session),
                scenario=scenario,
                params_used=iteration_params,
                parametrization=iteration_parametrization,
                baseline_summary=baseline_iteration.summary,
                baseline_tree=baseline_iteration.parametrization_tree,
                baseline_methods=baseline_iteration.methods,
            )
        )

    return ParametrizationLoopReport(
        api_base=effective_config.api_base,
        workspace_id=effective_config.workspace_id,
        entity_id=effective_config.entity_id,
        entity=entity,
        entity_type=entity_type,
        baseline=baseline_iteration,
        default_payload=default_payload,
        default_backfilled_payload=default_backfilled_payload,
        default_validation=default_validation,
        default_backfilled_validation=default_backfilled_validation,
        method_probes=method_probes,
        iterations=iterations,
    )


def load_targets_from_json(path: Path) -> list[AppTarget]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        payload = payload.get("targets", payload)
    if not isinstance(payload, list):
        raise ValueError("Target file must contain a JSON list or an object with a 'targets' list.")
    return [AppTarget.model_validate(item) for item in payload]


def run_parametrization_bench(
    *,
    targets: list[AppTarget] | None = None,
    scenario_map: dict[str, list[ParametrizationScenario]] | None = None,
    max_auto_scenarios: int = 4,
    output_root: Path | None = None,
) -> ParametrizationBenchReport:
    effective_targets = targets or build_default_bench_targets()
    effective_output_root = output_root or Path("vk-params-pydantic/artifacts/bench")

    app_reports: list[BenchAppReport] = []
    for target in effective_targets:
        config = build_config_for_target(target)
        target_scenarios = None if scenario_map is None else scenario_map.get(target.name)
        loop_report = run_parametrization_testing_loop(
            config=config,
            scenarios=target_scenarios,
            max_auto_scenarios=max_auto_scenarios,
        )
        target_output_dir = effective_output_root / build_target_slug(target)
        write_loop_report_json(loop_report, target_output_dir / "loop_report.json")
        write_iteration_artifacts(loop_report, target_output_dir / "iterations")
        app_reports.append(
            BenchAppReport(
                target=target,
                loop_report=loop_report,
                output_dir=str(target_output_dir),
            )
        )

    return ParametrizationBenchReport(
        targets=effective_targets,
        apps=app_reports,
    )


def write_capture_json(capture: ParametrizationCapture, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(capture.model_dump(mode="json"), indent=2),
        encoding="utf-8",
    )
    return output_path


def write_loop_report_json(report: ParametrizationLoopReport, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report.model_dump(mode="json"), indent=2),
        encoding="utf-8",
    )
    return output_path


def write_bench_report_json(report: ParametrizationBenchReport, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report.model_dump(mode="json"), indent=2),
        encoding="utf-8",
    )
    return output_path


def write_iteration_artifacts(
    report: ParametrizationLoopReport,
    output_dir: Path,
) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    written_paths: list[Path] = []

    baseline_path = output_dir / "baseline.json"
    baseline_path.write_text(
        json.dumps(report.baseline.model_dump(mode="json"), indent=2),
        encoding="utf-8",
    )
    written_paths.append(baseline_path)

    for iteration in report.iterations:
        iteration_path = output_dir / f"{iteration.scenario.name}.json"
        iteration_path.write_text(
            json.dumps(iteration.model_dump(mode="json"), indent=2),
            encoding="utf-8",
        )
        written_paths.append(iteration_path)

    return written_paths


def build_console_loop_summary(report: ParametrizationLoopReport) -> dict[str, Any]:
    return {
        "workspace_id": report.workspace_id,
        "entity_id": report.entity_id,
        "entity_name": report.entity.name,
        "entity_type": report.entity_type.name,
        "baseline": report.baseline.summary.model_dump(mode="json"),
        "default_payload": {
            "explicit_default_count": len(report.default_payload.explicit_default_paths) if report.default_payload else 0,
            "missing_default_count": len(report.default_payload.missing_default_paths) if report.default_payload else 0,
            "validation_success": report.default_validation.success if report.default_validation else False,
            "diff_count_vs_baseline": report.default_validation.diff_count_vs_baseline if report.default_validation else None,
        },
        "default_backfilled_payload": {
            "validation_success": report.default_backfilled_validation.success if report.default_backfilled_validation else False,
            "diff_count_vs_baseline": (
                report.default_backfilled_validation.diff_count_vs_baseline
                if report.default_backfilled_validation
                else None
            ),
        },
        "method_probes": [
            {
                "method_name": probe.method_name,
                "declared_kind": probe.declared_kind,
                "declared_view_type": probe.declared_view_type,
                "expected_result_kind": probe.expected_result_kind,
                "invocation_success": probe.invocation_success,
                "job_status": probe.job_status,
                "actual_result_kind": probe.actual_result_kind,
                "result_keys": probe.result_keys,
                "error": probe.error,
            }
            for probe in report.method_probes
        ],
        "iterations": [
            {
                "scenario": iteration.scenario.name,
                "summary": iteration.summary.model_dump(mode="json"),
                "summary_delta": iteration.summary_delta.model_dump(mode="json"),
                "diff_count": len(iteration.diffs),
            }
            for iteration in report.iterations
        ],
    }


def build_console_bench_summary(report: ParametrizationBenchReport) -> dict[str, Any]:
    return {
        "apps": [
            {
                "target": app_report.target.name,
                "workspace_id": app_report.target.workspace_id,
                "entity_id": app_report.target.entity_id,
                "entity_name": app_report.loop_report.entity.name,
                "entity_type": app_report.loop_report.entity_type.name,
                "baseline": app_report.loop_report.baseline.summary.model_dump(mode="json"),
                "default_payload": {
                    "explicit_default_count": (
                        len(app_report.loop_report.default_payload.explicit_default_paths)
                        if app_report.loop_report.default_payload
                        else 0
                    ),
                    "missing_default_count": (
                        len(app_report.loop_report.default_payload.missing_default_paths)
                        if app_report.loop_report.default_payload
                        else 0
                    ),
                    "validation_success": (
                        app_report.loop_report.default_validation.success
                        if app_report.loop_report.default_validation
                        else False
                    ),
                    "diff_count_vs_baseline": (
                        app_report.loop_report.default_validation.diff_count_vs_baseline
                        if app_report.loop_report.default_validation
                        else None
                    ),
                },
                "default_backfilled_payload": {
                    "validation_success": (
                        app_report.loop_report.default_backfilled_validation.success
                        if app_report.loop_report.default_backfilled_validation
                        else False
                    ),
                    "diff_count_vs_baseline": (
                        app_report.loop_report.default_backfilled_validation.diff_count_vs_baseline
                        if app_report.loop_report.default_backfilled_validation
                        else None
                    ),
                },
                "method_probes": [
                    {
                        "method_name": probe.method_name,
                        "declared_kind": probe.declared_kind,
                        "declared_view_type": probe.declared_view_type,
                        "expected_result_kind": probe.expected_result_kind,
                        "invocation_success": probe.invocation_success,
                        "job_status": probe.job_status,
                        "actual_result_kind": probe.actual_result_kind,
                        "result_keys": probe.result_keys,
                        "error": probe.error,
                    }
                    for probe in app_report.loop_report.method_probes
                ],
                "iterations": [
                    {
                        "scenario": iteration.scenario.name,
                        "diff_count": len(iteration.diffs),
                        "summary_delta": iteration.summary_delta.model_dump(mode="json"),
                    }
                    for iteration in app_report.loop_report.iterations
                ],
                "output_dir": app_report.output_dir,
            }
            for app_report in report.apps
        ]
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Capture the sample VIKTOR app parametrization into generic Pydantic models and emit JSON."
    )
    parser.add_argument(
        "--mode",
        choices=("single", "loop", "bench"),
        default="bench",
        help="Run one baseline capture, a multi-iteration testing loop, or the multi-app bench.",
    )
    parser.add_argument(
        "--output",
        default="vk-params-pydantic/artifacts/sample_app_capture.json",
        help="Where to write the captured JSON artifact.",
    )
    parser.add_argument(
        "--iterations-dir",
        default="vk-params-pydantic/artifacts/iterations",
        help="Where to write per-iteration JSON artifacts when using loop mode.",
    )
    parser.add_argument(
        "--scenario-file",
        help="Optional JSON file with custom scenarios. Each item needs name, optional description, and overrides.",
    )
    parser.add_argument(
        "--max-auto-scenarios",
        type=int,
        default=4,
        help="Maximum number of automatically generated scenarios for loop mode.",
    )
    parser.add_argument(
        "--target-file",
        help="Optional JSON file with bench targets. Defaults to the two configured VIKTOR demo apps.",
    )
    args = parser.parse_args()

    if args.mode == "single":
        capture = capture_sample_app()
        output_path = write_capture_json(capture, Path(args.output))
        print(json.dumps(capture.summary.model_dump(mode="json"), indent=2))
        print(f"Wrote capture to {output_path}")
        return

    if args.mode == "bench":
        targets = build_default_bench_targets()
        if args.target_file:
            targets = load_targets_from_json(Path(args.target_file))

        output_path = Path(args.output)
        if output_path == Path("vk-params-pydantic/artifacts/sample_app_capture.json"):
            output_path = Path("vk-params-pydantic/artifacts/bench/bench_report.json")

        bench_root = output_path.parent
        bench_report = run_parametrization_bench(
            targets=targets,
            max_auto_scenarios=args.max_auto_scenarios,
            output_root=bench_root,
        )
        report_path = write_bench_report_json(bench_report, output_path)
        print(json.dumps(build_console_bench_summary(bench_report), indent=2))
        print(f"Wrote bench report to {report_path}")
        return

    scenarios: list[ParametrizationScenario] | None = None
    if args.scenario_file:
        scenarios = load_scenarios_from_json(Path(args.scenario_file))

    report = run_parametrization_testing_loop(
        scenarios=scenarios,
        max_auto_scenarios=args.max_auto_scenarios,
    )
    report_path = write_loop_report_json(report, Path(args.output))
    written_iteration_paths = write_iteration_artifacts(report, Path(args.iterations_dir))
    print(json.dumps(build_console_loop_summary(report), indent=2))
    print(f"Wrote loop report to {report_path}")
    print(f"Wrote {len(written_iteration_paths)} iteration artifacts to {Path(args.iterations_dir)}")


if __name__ == "__main__":
    main()
