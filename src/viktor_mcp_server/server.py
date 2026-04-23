import os
from uuid import uuid4
from typing import Any

from dotenv import load_dotenv
from fastmcp import FastMCP
from fastmcp.apps import AppConfig, ResourceCSP
from pydantic import BaseModel, Field, TypeAdapter, model_validator

from viktor_mcp_server.viktor_api import ViktorJobClient

load_dotenv()


def _get_env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    return int(value) if value else default


def _get_env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    return float(value) if value else default


def _get_env_str(name: str, default: str) -> str:
    return (os.getenv(name) or default).strip()


class Settings(BaseModel):
    api_base: str = Field(default_factory=lambda: _get_env_str("VIKTOR_API_BASE", "https://demo.viktor.ai/api"))
    token: str = Field(default_factory=lambda: _get_env_str("TOKEN_VK_APP", ""))
    workspace_id: int = Field(default_factory=lambda: _get_env_int("VIKTOR_WORKSPACE_ID", 2141))
    footing_sizing_entity_id: int = Field(default_factory=lambda: _get_env_int("VIKTOR_FOOTING_SIZING_ENTITY_ID", 11536))
    footing_sizing_method_name: str = Field(default_factory=lambda: _get_env_str("VIKTOR_FOOTING_SIZING_METHOD_NAME", "download_results"))
    footing_sizing_report_method_name: str = Field(default_factory=lambda: _get_env_str("VIKTOR_FOOTING_SIZING_REPORT_METHOD_NAME", "view_report"))
    max_poll_seconds: int = Field(default_factory=lambda: _get_env_int("VIKTOR_MAX_POLL_SECONDS", 120))
    connect_timeout: float = Field(default_factory=lambda: _get_env_float("VIKTOR_HTTP_CONNECT_TIMEOUT", 5.0))
    read_timeout: float = Field(default_factory=lambda: _get_env_float("VIKTOR_HTTP_READ_TIMEOUT", 120.0))


class NodeCoordinate(BaseModel):
    node_name: str = Field(description="Node identifier")
    x: float = Field(default=0.0, description="X coordinate in meters")
    y: float = Field(default=0.0, description="Y coordinate in meters")
    z: float = Field(default=0.0, description="Z coordinate in meters")


class ReactionLoadEntry(BaseModel):
    F1: float = Field(default=0.0, description="Force in X direction (kN)")
    F2: float = Field(default=0.0, description="Force in Y direction (kN)")
    F3: float = Field(default=0.0, description="Force in Z direction (kN)")
    M1: float = Field(default=0.0, description="Moment about X axis (kN.m)")
    M2: float = Field(default=0.0, description="Moment about Y axis (kN.m)")
    M3: float = Field(default=0.0, description="Moment about Z axis (kN.m)")


class ReactionLoadCaseRow(ReactionLoadEntry):
    lc_name: str = Field(description="Load combination name")
    node_name: str = Field(description="Node identifier for this reaction row")


class SoilSection(BaseModel):
    q_allow: float = Field(default=150.0, description="Allowable bearing pressure in kN/m2")
    gamma_c: float = Field(default=25.0, description="Concrete unit weight in kN/m3")
    depth: float = Field(default=0.5, description="Foundation depth in meters")
    b_min: float = Field(default=1.5, description="Minimum pad size in meters")


class FootingSizingRequest(BaseModel):
    q_allow: float = Field(default=150.0, description="Allowable bearing pressure in kN/m2")
    gamma_c: float = Field(default=25.0, description="Concrete unit weight in kN/m3")
    depth: float = Field(default=0.5, description="Foundation depth in meters")
    b_min: float = Field(default=1.5, description="Minimum pad size in meters")
    support_coordinates: list[NodeCoordinate] = Field(
        default_factory=list,
        description="Support node coordinates.",
    )
    reaction_loads: dict[str, dict[str, ReactionLoadEntry]] = Field(
        default_factory=dict,
        description=(
            "Reaction loads keyed by node name, then by load combination name. "
            "Example: {'N1': {'ULS1': {'F1': 0, 'F2': 0, 'F3': -1000, 'M1': 10, 'M2': 20, 'M3': 0}}}"
        ),
    )

    @model_validator(mode="after")
    def validate_support_coordinates(self) -> "FootingSizingRequest":
        if not self.support_coordinates:
            raise ValueError("support_coordinates must contain at least one node.")

        seen: set[str] = set()
        duplicates: list[str] = []
        for node in self.support_coordinates:
            if node.node_name in seen:
                duplicates.append(node.node_name)
            seen.add(node.node_name)

        if duplicates:
            raise ValueError(
                f"support_coordinates contains duplicate node names: {sorted(set(duplicates))}"
            )

        if not self.reaction_loads:
            raise ValueError("reaction_loads must contain at least one node entry.")
        return self


class FootingSizingRowsRequest(BaseModel):
    q_allow: float = Field(default=150.0, description="Allowable bearing pressure in kN/m2")
    gamma_c: float = Field(default=25.0, description="Concrete unit weight in kN/m3")
    depth: float = Field(default=0.5, description="Foundation depth in meters")
    b_min: float = Field(default=1.5, description="Minimum pad size in meters")
    support_coordinates: list[NodeCoordinate] = Field(
        default_factory=list,
        description="Support node coordinates.",
    )
    reaction_load_cases: list[ReactionLoadCaseRow] = Field(
        default_factory=list,
        description="Reaction loads as one row per node and load combination.",
    )

    @model_validator(mode="after")
    def validate_rows(self) -> "FootingSizingRowsRequest":
        if not self.support_coordinates:
            raise ValueError("support_coordinates must contain at least one node.")

        seen: set[str] = set()
        duplicates: list[str] = []
        for node in self.support_coordinates:
            if node.node_name in seen:
                duplicates.append(node.node_name)
            seen.add(node.node_name)

        if duplicates:
            raise ValueError(
                f"support_coordinates contains duplicate node names: {sorted(set(duplicates))}"
            )

        if not self.reaction_load_cases:
            raise ValueError("reaction_load_cases must contain at least one load row.")

        node_names = {node.node_name for node in self.support_coordinates}
        unknown_nodes = sorted(
            {
                row.node_name
                for row in self.reaction_load_cases
                if row.node_name not in node_names
            }
        )
        if unknown_nodes:
            raise ValueError(
                f"reaction_load_cases contains nodes not present in support_coordinates: {unknown_nodes}"
            )

        return self


class FootingSizingResultEntry(BaseModel):
    node_id: str
    B: float
    L: float
    x: float
    y: float
    z: float
    acting_bearing_pressure: float


class FootingSizingResponse(BaseModel):
    workspace_id: int
    entity_id: int
    method_name: str
    nodes_sized: int
    results: list[FootingSizingResultEntry]


class HealthResponse(BaseModel):
    name: str
    version: str
    api_base: str
    workspace_id: int
    footing_sizing_entity_id: int
    footing_sizing_method_name: str
    footing_sizing_report_method_name: str
    token_configured: bool
    max_poll_seconds: int


class FootingSizingReportHtmlResponse(BaseModel):
    workspace_id: int
    entity_id: int
    method_name: str
    web_url: str


class FootingSizingEntityCreateResponse(BaseModel):
    workspace_id: int
    entity_id: int
    entity_name: str
    entity_type: int
    creation_mode: str
    parent_entity_id: int | None
    editor_session: str
    entity_url: str

def _settings() -> Settings:
    return Settings()


def _job_client(settings: Settings) -> ViktorJobClient:
    return ViktorJobClient(
        api_base=settings.api_base,
        token=settings.token,
        max_poll_seconds=settings.max_poll_seconds,
        connect_timeout=settings.connect_timeout,
        read_timeout=settings.read_timeout,
    )


def _build_payload_params(request: FootingSizingRequest) -> dict[str, Any]:
    node_names = {node.node_name for node in request.support_coordinates}
    load_cases_table: list[dict[str, Any]] = []

    for support in request.support_coordinates:
        node_reactions = request.reaction_loads.get(support.node_name)
        if not isinstance(node_reactions, dict) or not node_reactions:
            raise ValueError(
                f"No reaction data found for support node '{support.node_name}'."
            )

        for combo_name, reaction in node_reactions.items():
            load_cases_table.append(
                {
                    "lc_name": combo_name,
                    "node_name": support.node_name,
                    "f1": reaction.F1,
                    "f2": reaction.F2,
                    "f3": reaction.F3,
                    "m1": reaction.M1,
                    "m2": reaction.M2,
                    "m3": reaction.M3,
                }
            )

    extra_nodes = sorted(set(request.reaction_loads.keys()) - node_names)
    if extra_nodes:
        raise ValueError(
            f"reaction_loads contains nodes not present in support_coordinates: {extra_nodes}"
        )

    return {
        "soil": SoilSection(
            q_allow=request.q_allow,
            gamma_c=request.gamma_c,
            depth=request.depth,
            b_min=request.b_min,
        ).model_dump(mode="json"),
        "nodes_section": {
            "nodes_table": [node.model_dump(mode="json") for node in request.support_coordinates]
        },
        "lc_section": {
            "load_cases_table": load_cases_table,
        },
    }


def _build_payload_params_from_rows(request: FootingSizingRowsRequest) -> dict[str, Any]:
    return {
        "soil": SoilSection(
            q_allow=request.q_allow,
            gamma_c=request.gamma_c,
            depth=request.depth,
            b_min=request.b_min,
        ).model_dump(mode="json"),
        "nodes_section": {
            "nodes_table": [node.model_dump(mode="json") for node in request.support_coordinates]
        },
        "lc_section": {
            "load_cases_table": [
                {
                    "lc_name": row.lc_name,
                    "node_name": row.node_name,
                    "f1": row.F1,
                    "f2": row.F2,
                    "f3": row.F3,
                    "m1": row.M1,
                    "m2": row.M2,
                    "m3": row.M3,
                }
                for row in request.reaction_load_cases
            ]
        },
    }


def _build_unique_entity_name(name: str | None) -> str:
    base_name = (name or "").strip() or "Footing Sizing"
    short_id = uuid4().hex[:8]
    return f"{base_name} - {short_id}"


mcp_version = "0.3.0"


mcp = FastMCP(
    name="VIKTOR Foundation MCP",
    version=mcp_version,
    instructions=(
        "MCP server for running VIKTOR-based foundation sizing workflows. "
        "The footing_sizing tool expects direct support coordinates and reaction loads; "
        "it does not read from VIKTOR Storage."
    ),
)

REPORT_UI_URI = "ui://footing/report-viewer"


def _report_viewer_html() -> str:
    return """<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Footing Report Viewer</title>
    <style>
      :root {
        color-scheme: light dark;
        --bg: var(--color-background-primary, #ffffff);
        --panel: var(--color-background-secondary, #f6f8fb);
        --text: var(--color-text-primary, #111827);
        --muted: var(--color-text-secondary, #6b7280);
        --border: var(--color-border-default, #d1d5db);
        --accent: #1f3b64;
      }
      * { box-sizing: border-box; }
      body {
        margin: 0;
        font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        background: var(--bg);
        color: var(--text);
      }
      .shell {
        display: grid;
        grid-template-rows: auto auto 1fr;
        min-height: 100vh;
      }
      .header {
        padding: 14px 18px 10px;
        border-bottom: 1px solid var(--border);
      }
      .title {
        margin: 0;
        font-size: 16px;
        font-weight: 700;
        color: var(--accent);
      }
      .subtitle {
        margin: 6px 0 0;
        font-size: 13px;
        color: var(--muted);
      }
      .toolbar {
        display: flex;
        gap: 10px;
        align-items: center;
        padding: 10px 18px;
        border-bottom: 1px solid var(--border);
        background: var(--panel);
      }
      .toolbar a {
        color: var(--accent);
        text-decoration: none;
        font-size: 13px;
        font-weight: 600;
      }
      .status {
        font-size: 13px;
        color: var(--muted);
      }
      .viewer-wrap {
        padding: 12px;
        min-height: 480px;
      }
      iframe {
        width: 100%;
        height: calc(100vh - 132px);
        min-height: 520px;
        border: 1px solid var(--border);
        border-radius: 10px;
        background: white;
      }
      .placeholder {
        display: grid;
        place-items: center;
        min-height: 520px;
        border: 1px dashed var(--border);
        border-radius: 10px;
        color: var(--muted);
        text-align: center;
        padding: 24px;
      }
      .error {
        color: #b91c1c;
      }
    </style>
  </head>
  <body>
    <div class="shell">
      <div class="header">
        <h1 class="title">Footing Calculation Report</h1>
        <p class="subtitle">Rendered from the VIKTOR <code>view_report</code> webview result.</p>
      </div>
      <div class="toolbar">
        <a id="open-link" href="#" target="_blank" rel="noopener noreferrer" hidden>Open report in new tab</a>
        <span id="status" class="status">Waiting for tool result...</span>
      </div>
      <div class="viewer-wrap" id="viewer-wrap">
        <div class="placeholder">Call the report tool to load the VIKTOR report.</div>
      </div>
    </div>

    <script>
      const statusEl = document.getElementById("status");
      const viewerWrap = document.getElementById("viewer-wrap");
      const openLink = document.getElementById("open-link");

      function renderPlaceholder(message, isError = false) {
        viewerWrap.innerHTML = `<div class="placeholder ${isError ? "error" : ""}">${message}</div>`;
        if (isError) {
          statusEl.textContent = "Report unavailable";
        }
      }

      function renderReport(data) {
        const webUrl = data?.web_url;
        if (!webUrl) {
          renderPlaceholder("The tool result did not include a web URL.", true);
          return;
        }

        openLink.href = webUrl;
        openLink.hidden = false;
        statusEl.textContent = "Report loaded";
        viewerWrap.innerHTML = `<iframe src="${webUrl}" sandbox="allow-scripts allow-same-origin allow-popups allow-forms"></iframe>`;
      }

      function handleToolResult(result) {
        if (result?.isError) {
          const text = result?.content?.find?.((item) => item.type === "text")?.text ?? "Tool call failed.";
          renderPlaceholder(text, true);
          return;
        }
        renderReport(result?.structuredContent ?? result ?? {});
      }

      handleToolResult(window.openai?.toolOutput);

      window.addEventListener(
        "openai:set_globals",
        (event) => {
          handleToolResult(event.detail?.globals?.toolOutput ?? window.openai?.toolOutput);
        },
        { passive: true }
      );

      window.addEventListener(
        "message",
        (event) => {
          if (event.source !== window.parent) return;
          const message = event.data;
          if (!message || message.jsonrpc !== "2.0") return;
          if (message.method !== "ui/notifications/tool-result") return;
          handleToolResult(message.params);
        },
        { passive: true }
      );
    </script>
  </body>
</html>
"""


@mcp.resource(
    REPORT_UI_URI,
    name="footing_report_viewer",
    title="Footing Report Viewer",
    description="UI resource that renders the VIKTOR footing report inside an iframe.",
    meta={
        "openai/widgetDescription": "Displays the VIKTOR footing sizing report inside an iframe.",
    },
    app=AppConfig(
        csp=ResourceCSP(
            frameDomains=["https://viktor-storage-eu1.s3.amazonaws.com"],
        ),
        prefersBorder=True,
    ),
)
def footing_report_viewer() -> str:
    return _report_viewer_html()


@mcp.tool(name="health", description="Return server configuration status for the VIKTOR MCP server.")
def health() -> HealthResponse:
    settings = _settings()
    return HealthResponse(
        name="VIKTOR Foundation MCP",
        version=mcp_version,
        api_base=settings.api_base,
        workspace_id=settings.workspace_id,
        footing_sizing_entity_id=settings.footing_sizing_entity_id,
        footing_sizing_method_name=settings.footing_sizing_method_name,
        footing_sizing_report_method_name=settings.footing_sizing_report_method_name,
        token_configured=bool(settings.token),
        max_poll_seconds=settings.max_poll_seconds,
    )


@mcp.tool(
    name="footing_sizing_v3",
    description=(
        "Run the VIKTOR footing sizing app. "
        "Requires support_coordinates and reaction_load_cases, where each load case "
        "is one row with lc_name, node_name, F1, F2, F3, M1, M2, and M3."
    ),
)
def footing_sizing_v3(
    support_coordinates: list[NodeCoordinate],
    reaction_load_cases: list[ReactionLoadCaseRow],
    q_allow: float = 150.0,
    gamma_c: float = 25.0,
    depth: float = 0.5,
    b_min: float = 1.5,
) -> FootingSizingResponse:
    request = FootingSizingRowsRequest(
        q_allow=q_allow,
        gamma_c=gamma_c,
        depth=depth,
        b_min=b_min,
        support_coordinates=support_coordinates,
        reaction_load_cases=reaction_load_cases,
    )
    settings = _settings()
    client = _job_client(settings)
    params = _build_payload_params_from_rows(request)
    job = client.create_job(
        workspace_id=settings.workspace_id,
        entity_id=settings.footing_sizing_entity_id,
        method_name=settings.footing_sizing_method_name,
        params=params,
    )

    if not job.download_url:
        raise RuntimeError("The VIKTOR job completed without a download URL.")

    content = client.download_json(job.download_url)
    results = TypeAdapter(list[FootingSizingResultEntry]).validate_python(content)

    return FootingSizingResponse(
        workspace_id=settings.workspace_id,
        entity_id=settings.footing_sizing_entity_id,
        method_name=settings.footing_sizing_method_name,
        nodes_sized=len(results),
        results=results,
    )


@mcp.tool(
    name="footing_sizing_report_html_v3",
    description=(
        "Render the VIKTOR footing sizing report. "
        "Requires support_coordinates and reaction_load_cases, where each load case "
        "is one row with lc_name, node_name, F1, F2, F3, M1, M2, and M3."
    ),
    meta={
        "openai/outputTemplate": REPORT_UI_URI,
        "openai/toolInvocation/invoking": "Loading report…",
        "openai/toolInvocation/invoked": "Report ready",
    },
    app=AppConfig(
        resourceUri=REPORT_UI_URI,
    ),
)
def footing_sizing_report_html_v3(
    support_coordinates: list[NodeCoordinate],
    reaction_load_cases: list[ReactionLoadCaseRow],
    q_allow: float = 150.0,
    gamma_c: float = 25.0,
    depth: float = 0.5,
    b_min: float = 1.5,
) -> FootingSizingReportHtmlResponse:
    request = FootingSizingRowsRequest(
        q_allow=q_allow,
        gamma_c=gamma_c,
        depth=depth,
        b_min=b_min,
        support_coordinates=support_coordinates,
        reaction_load_cases=reaction_load_cases,
    )
    settings = _settings()
    client = _job_client(settings)
    params = _build_payload_params_from_rows(request)
    job = client.create_job(
        workspace_id=settings.workspace_id,
        entity_id=settings.footing_sizing_entity_id,
        method_name=settings.footing_sizing_report_method_name,
        params=params,
    )

    web_result = job.result.web if job.result else None
    if not isinstance(web_result, dict):
        raise RuntimeError(
            "The VIKTOR job completed without a web result payload."
        )

    web_url = web_result.get("url")
    if not isinstance(web_url, str) or not web_url:
        raise RuntimeError(
            "The VIKTOR web result did not include a web URL."
        )

    return FootingSizingReportHtmlResponse(
        workspace_id=settings.workspace_id,
        entity_id=settings.footing_sizing_entity_id,
        method_name=settings.footing_sizing_report_method_name,
        web_url=web_url,
    )


@mcp.tool(
    name="footing_sizing_create_entity_v3",
    description=(
        "Create a new VIKTOR footing sizing entity from direct support coordinates "
        "and reaction_load_cases, save the input parameters on the entity, and return "
        "the editor URL."
    ),
)
def footing_sizing_create_entity_v3(
    support_coordinates: list[NodeCoordinate],
    reaction_load_cases: list[ReactionLoadCaseRow],
    q_allow: float = 150.0,
    gamma_c: float = 25.0,
    depth: float = 0.5,
    b_min: float = 1.5,
    name: str | None = None,
) -> FootingSizingEntityCreateResponse:
    request = FootingSizingRowsRequest(
        q_allow=q_allow,
        gamma_c=gamma_c,
        depth=depth,
        b_min=b_min,
        support_coordinates=support_coordinates,
        reaction_load_cases=reaction_load_cases,
    )
    settings = _settings()
    client = _job_client(settings)
    params = _build_payload_params_from_rows(request)

    template_entity = client.get_entity(
        workspace_id=settings.workspace_id,
        entity_id=settings.footing_sizing_entity_id,
    )
    parent_entity = client.get_parent_entity(
        workspace_id=settings.workspace_id,
        entity_id=settings.footing_sizing_entity_id,
    )

    entity_name = _build_unique_entity_name(name)

    if parent_entity is not None:
        created_entity = client.create_child_entity(
            workspace_id=settings.workspace_id,
            parent_entity_id=parent_entity.id,
            entity_type=template_entity.entity_type,
            name=entity_name,
            properties=params,
        )
        creation_mode = "sibling"
        parent_entity_id = parent_entity.id
    else:
        created_entity = client.create_entity(
            workspace_id=settings.workspace_id,
            entity_type=template_entity.entity_type,
            name=entity_name,
            properties=params,
        )
        creation_mode = "root"
        parent_entity_id = None

    updated_entity = client.update_entity(
        workspace_id=settings.workspace_id,
        entity_id=created_entity.id,
        name=created_entity.name,
        properties=params,
        message="Set footing sizing parameters from MCP input",
    )
    editor_session = client.create_editor_session(
        workspace_id=settings.workspace_id,
        entity_id=updated_entity.id,
    )

    return FootingSizingEntityCreateResponse(
        workspace_id=settings.workspace_id,
        entity_id=updated_entity.id,
        entity_name=updated_entity.name,
        entity_type=updated_entity.entity_type,
        creation_mode=creation_mode,
        parent_entity_id=parent_entity_id,
        editor_session=str(editor_session.editor_session),
        entity_url=client.build_entity_editor_url(
            workspace_id=settings.workspace_id,
            entity_id=updated_entity.id,
        ),
    )


def main() -> None:
    mcp.run()
