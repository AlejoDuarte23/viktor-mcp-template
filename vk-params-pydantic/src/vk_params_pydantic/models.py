from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ParamTypeRaw(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: str | None = None
    sub_types: dict[str, "ParamTypeRaw"] = Field(default_factory=dict)


class EntityResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: int
    name: str
    entity_type: int
    entity_type_name: str | None = None
    actions: list[str] = Field(default_factory=list)
    properties: dict[str, Any] = Field(default_factory=dict)
    param_types: dict[str, ParamTypeRaw] = Field(default_factory=dict)


class ViewRaw(BaseModel):
    model_config = ConfigDict(extra="allow")

    label: str | None = None
    view_type: str | None = None
    controller_method: str | None = None
    automatic_update: bool | None = None


class EntityTypeResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: int
    name: str
    class_name: str | None = None
    preprocess_method: str | None = None
    views: list[ViewRaw] = Field(default_factory=list)


class EditorSessionResponse(BaseModel):
    editor_session: UUID


class ParametrizationViewRaw(BaseModel):
    model_config = ConfigDict(extra="allow")

    controller_method: str | None = None


class ParamNodeRaw(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str | None = None
    type: str
    title: str | None = None
    ui_name: str | None = None
    description: str | None = None
    default: Any = None
    views: list[str] = Field(default_factory=list)
    content: list["ParamNodeRaw"] = Field(default_factory=list)
    method: str | None = None
    options: Any = None
    variant: str | None = None
    suffix: str | None = None
    multiple: bool | None = None


class ParametrizationContentRaw(BaseModel):
    model_config = ConfigDict(extra="allow")

    parametrization: list[ParamNodeRaw] = Field(default_factory=list)
    views: list[ParametrizationViewRaw] = Field(default_factory=list)


class ParametrizationResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    content: ParametrizationContentRaw


class MethodSpec(BaseModel):
    kind: Literal["view", "button", "preprocess"]
    method_name: str
    source: Literal["entity_type", "parametrization"]
    label: str | None = None
    view_type: str | None = None
    automatic_update: bool | None = None
    source_path: list[str] = Field(default_factory=list)
    raw_node_type: str | None = None
    method_classification: str | None = None
    expected_result_kind: str | None = None


class DownloadResult(BaseModel):
    url: str


class JobResultPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    web: dict[str, Any] | None = None
    pdf: dict[str, Any] | None = None
    data: dict[str, Any] | None = None
    table: dict[str, Any] | None = None
    plotly: dict[str, Any] | None = None
    image: dict[str, Any] | None = None
    geometry: dict[str, Any] | None = None
    download: DownloadResult | None = None
    set_params: dict[str, Any] | None = None
    ifc: dict[str, Any] | None = None
    geojson: dict[str, Any] | None = None
    optimization: dict[str, Any] | None = None


class JobCreateResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    uid: int | None = None
    url: str | None = None
    message: str | None = None
    kind: str | None = None
    status: str | None = None
    error_message: str | None = None
    content: dict[str, Any] | None = None


class JobStatusResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    uid: int | None = None
    kind: str | None = None
    status: str
    result: JobResultPayload | None = None
    error: dict[str, Any] | None = None
    message: dict[str, Any] | None = None
    log_download_url: str | None = None


class MethodProbeResult(BaseModel):
    method_name: str
    label: str | None = None
    declared_kind: str | None = None
    declared_view_type: str | None = None
    expected_result_kind: str | None = None
    invocation_success: bool
    job_status: str | None = None
    actual_result_kind: str | None = None
    result_keys: list[str] = Field(default_factory=list)
    error: str | None = None
    result_summary: dict[str, Any] = Field(default_factory=dict)
    job_kind: str | None = None


class ParamNodeSpec(BaseModel):
    node_kind: Literal[
        "page",
        "step",
        "tab",
        "section",
        "field",
        "table",
        "dynamic_array",
        "action",
        "unknown",
    ]
    raw_type: str
    name: str | None = None
    title: str | None = None
    label: str | None = None
    path: list[str] = Field(default_factory=list)
    payload_path: list[str] = Field(default_factory=list)
    views: list[str] = Field(default_factory=list)
    method: str | None = None
    default: Any = None
    python_type_hint: str | None = None
    metadata: list[str] = Field(default_factory=list)
    extra: dict[str, Any] = Field(default_factory=dict)
    children: list["ParamNodeSpec"] = Field(default_factory=list)


class PayloadNodeSpec(BaseModel):
    value_kind: Literal["object", "list", "scalar", "null"]
    path: list[str] = Field(default_factory=list)
    python_type_hint: str
    example: Any = None
    children: list["PayloadNodeSpec"] = Field(default_factory=list)
    item: "PayloadNodeSpec | None" = None


class CaptureSummary(BaseModel):
    total_nodes: int
    total_containers: int
    total_fields: int
    total_actions: int
    total_methods: int


class AppTarget(BaseModel):
    name: str
    workspace_id: int
    entity_id: int
    api_base: str | None = None
    description: str | None = None


class SummaryDelta(BaseModel):
    total_nodes: int = 0
    total_containers: int = 0
    total_fields: int = 0
    total_actions: int = 0
    total_methods: int = 0


class ParametrizationCapture(BaseModel):
    api_base: str
    workspace_id: int
    entity_id: int
    entity: EntityResponse
    entity_type: EntityTypeResponse
    editor_session: UUID
    parametrization: ParametrizationResponse
    methods: list[MethodSpec] = Field(default_factory=list)
    parametrization_tree: list[ParamNodeSpec] = Field(default_factory=list)
    payload_tree: PayloadNodeSpec | None = None
    summary: CaptureSummary


class ParametrizationScenario(BaseModel):
    name: str
    description: str | None = None
    overrides: dict[str, Any] = Field(default_factory=dict)


class ParametrizationDiffEntry(BaseModel):
    path: str
    change_type: Literal["added", "removed", "changed"]
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None


class PayloadCandidate(BaseModel):
    name: str
    params: dict[str, Any] = Field(default_factory=dict)
    payload_tree: PayloadNodeSpec | None = None
    explicit_default_paths: list[str] = Field(default_factory=list)
    missing_default_paths: list[str] = Field(default_factory=list)


class PayloadValidationResult(BaseModel):
    name: str
    success: bool
    params_used: dict[str, Any] = Field(default_factory=dict)
    payload_tree: PayloadNodeSpec | None = None
    error: str | None = None
    validated_summary: CaptureSummary | None = None
    diff_count_vs_baseline: int | None = None
    parametrization_tree: list[ParamNodeSpec] = Field(default_factory=list)
    methods: list[MethodSpec] = Field(default_factory=list)


class ParametrizationIterationResult(BaseModel):
    scenario: ParametrizationScenario
    params_used: dict[str, Any] = Field(default_factory=dict)
    editor_session: UUID
    parametrization: ParametrizationResponse
    methods: list[MethodSpec] = Field(default_factory=list)
    parametrization_tree: list[ParamNodeSpec] = Field(default_factory=list)
    payload_tree: PayloadNodeSpec | None = None
    summary: CaptureSummary
    summary_delta: SummaryDelta = Field(default_factory=SummaryDelta)
    diffs: list[ParametrizationDiffEntry] = Field(default_factory=list)


class ParametrizationLoopReport(BaseModel):
    api_base: str
    workspace_id: int
    entity_id: int
    entity: EntityResponse
    entity_type: EntityTypeResponse
    baseline: ParametrizationIterationResult
    default_payload: PayloadCandidate | None = None
    default_backfilled_payload: PayloadCandidate | None = None
    default_validation: PayloadValidationResult | None = None
    default_backfilled_validation: PayloadValidationResult | None = None
    method_probes: list[MethodProbeResult] = Field(default_factory=list)
    iterations: list[ParametrizationIterationResult] = Field(default_factory=list)


class BenchAppReport(BaseModel):
    target: AppTarget
    loop_report: ParametrizationLoopReport
    output_dir: str


class ParametrizationBenchReport(BaseModel):
    targets: list[AppTarget] = Field(default_factory=list)
    apps: list[BenchAppReport] = Field(default_factory=list)


ParamNodeRaw.model_rebuild()
ParamTypeRaw.model_rebuild()
ParamNodeSpec.model_rebuild()
PayloadNodeSpec.model_rebuild()
