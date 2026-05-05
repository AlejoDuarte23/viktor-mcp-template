from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from vk_params_pydantic.bench import (  # noqa: E402
    SampleAppConfig,
    build_payload_tree,
    build_auto_scenarios,
    build_default_payload_candidate,
    build_default_bench_targets,
    build_target_from_app_url,
    capture_sample_app,
    classify_method_spec,
    deep_merge,
    load_repo_env,
    load_targets_from_json,
    parse_app_url,
    run_parametrization_bench,
    run_parametrization_testing_loop,
    write_capture_json,
    write_bench_report_json,
    write_loop_report_json,
)
from vk_params_pydantic.models import (  # noqa: E402
    ParamNodeRaw,
    ParametrizationContentRaw,
    ParametrizationResponse,
)


class PayloadTreeInferenceTest(unittest.TestCase):
    def test_build_payload_tree_keeps_list_shape(self) -> None:
        payload = {
            "step_1": {
                "names": ["A", "B"],
                "rows": [
                    {"force": 10.0, "label": "ULS"},
                    {"force": 12.5, "label": "SLS"},
                ],
            }
        }

        tree = build_payload_tree(payload)
        step_node = next(child for child in tree.children if child.path == ["step_1"])
        names_node = next(child for child in step_node.children if child.path == ["step_1", "names"])
        rows_node = next(child for child in step_node.children if child.path == ["step_1", "rows"])

        self.assertEqual(names_node.python_type_hint, "list[str]")
        self.assertEqual(rows_node.python_type_hint, "list[dict[str, Any]]")
        self.assertIsNotNone(rows_node.item)
        self.assertEqual(rows_node.item.value_kind, "object")

    def test_deep_merge_updates_nested_branch(self) -> None:
        base = {
            "step_1": {
                "sec_numeric": {
                    "num_elements": 10,
                    "load_factor": 1.5,
                }
            }
        }
        override = {"step_1": {"sec_numeric": {"num_elements": 12}}}

        merged = deep_merge(base, override)

        self.assertEqual(merged["step_1"]["sec_numeric"]["num_elements"], 12)
        self.assertEqual(merged["step_1"]["sec_numeric"]["load_factor"], 1.5)

    def test_auto_scenarios_cover_common_shapes(self) -> None:
        payload = {
            "step_1": {
                "advanced_mode": False,
                "num_elements": 10,
                "load_cases": ["Dead", "Live"],
                "load_points": [{"force": 10.0, "label": "ULS"}],
                "project_name": "Demo",
            }
        }

        scenarios = build_auto_scenarios(payload, max_scenarios=5)
        scenario_names = [scenario.name for scenario in scenarios]

        self.assertIn("toggle_step_1_advanced_mode", scenario_names)
        self.assertIn("adjust_step_1_num_elements", scenario_names)
        self.assertIn("rotate_step_1_load_cases", scenario_names)
        self.assertIn("extend_step_1_load_points", scenario_names)

    def test_default_bench_targets_match_expected_apps(self) -> None:
        targets = build_default_bench_targets()
        pairs = {(target.workspace_id, target.entity_id) for target in targets}
        self.assertEqual(pairs, {(2232, 11640), (2141, 11536)})

    def test_load_targets_from_json(self) -> None:
        targets = load_targets_from_json(ROOT / "bench_targets.json")
        self.assertEqual(len(targets), 2)
        self.assertEqual((targets[0].workspace_id, targets[0].entity_id), (2232, 11640))

    def test_build_default_payload_candidate_extracts_defaults_and_missing(self) -> None:
        parametrization = ParametrizationResponse(
            content=ParametrizationContentRaw(
                parametrization=[
                    ParamNodeRaw(
                        name="step_1",
                        type="step",
                        content=[
                            ParamNodeRaw(
                                name="step_1.sec_numeric",
                                type="section",
                                content=[
                                    ParamNodeRaw(
                                        name="step_1.sec_numeric.num_elements",
                                        type="integer",
                                        default=10,
                                    ),
                                    ParamNodeRaw(
                                        name="step_1.sec_numeric.project_number",
                                        type="number",
                                    ),
                                ],
                            )
                        ],
                    )
                ]
            )
        )
        saved_params = {
            "step_1": {
                "sec_numeric": {
                    "num_elements": 12,
                    "project_number": 42,
                }
            }
        }

        defaults_only = build_default_payload_candidate(
            parametrization,
            saved_params,
            name="defaults_only",
            backfill_missing=False,
        )
        defaults_plus_saved = build_default_payload_candidate(
            parametrization,
            saved_params,
            name="defaults_plus_saved",
            backfill_missing=True,
        )

        self.assertEqual(defaults_only.params["step_1"]["sec_numeric"]["num_elements"], 10)
        self.assertNotIn("project_number", defaults_only.params["step_1"]["sec_numeric"])
        self.assertIn("step_1.sec_numeric.num_elements", defaults_only.explicit_default_paths)
        self.assertIn("step_1.sec_numeric.project_number", defaults_only.missing_default_paths)
        self.assertEqual(defaults_plus_saved.params["step_1"]["sec_numeric"]["project_number"], 42)

    def test_classify_method_spec_maps_supported_method_types(self) -> None:
        self.assertEqual(classify_method_spec(kind="view", view_type="web", raw_node_type=None), ("webview", "web"))
        self.assertEqual(classify_method_spec(kind="view", view_type="data", raw_node_type=None), ("dataview", "data"))
        self.assertEqual(classify_method_spec(kind="view", view_type="table", raw_node_type=None), ("tableview", "table"))
        self.assertEqual(
            classify_method_spec(kind="button", view_type=None, raw_node_type="download-button"),
            ("download_button", "download"),
        )

    def test_parse_app_url(self) -> None:
        parsed = parse_app_url("https://demo.viktor.ai/workspaces/2141/app/editor/11536")
        self.assertEqual(parsed["api_base"], "https://demo.viktor.ai/api")
        self.assertEqual(parsed["workspace_id"], 2141)
        self.assertEqual(parsed["entity_id"], 11536)

    def test_build_target_from_app_url(self) -> None:
        target = build_target_from_app_url(
            "https://demo.viktor.ai/workspaces/2232/app/editor/11640",
            name="demo_target",
        )
        self.assertEqual(target.name, "demo_target")
        self.assertEqual(target.workspace_id, 2232)
        self.assertEqual(target.entity_id, 11640)
        self.assertEqual(target.api_base, "https://demo.viktor.ai/api")


class SampleAppCaptureIntegrationTest(unittest.TestCase):
    def test_capture_sample_app_and_write_json(self) -> None:
        load_repo_env()
        token = (os.getenv("TOKEN_VK_APP") or "").strip()
        if not token:
            self.skipTest("TOKEN_VK_APP is not configured.")

        capture = capture_sample_app(
            SampleAppConfig(
                api_base=(os.getenv("VIKTOR_API_BASE") or "https://demo.viktor.ai/api").strip(),
                workspace_id=2366,
                entity_id=11838,
                token=token,
            )
        )

        output_path = write_capture_json(
            capture,
            ROOT / "artifacts" / "sample_app_capture.json",
        )

        self.assertEqual(capture.entity.id, 11838)
        self.assertGreater(capture.summary.total_nodes, 0)
        self.assertGreater(capture.summary.total_methods, 0)
        self.assertTrue(any(node.node_kind in {"step", "page"} for node in capture.parametrization_tree))
        self.assertTrue(any(method.method_name == "view_web_info" for method in capture.methods))
        self.assertTrue(output_path.exists())

    def test_run_parametrization_testing_loop_and_write_json(self) -> None:
        load_repo_env()
        token = (os.getenv("TOKEN_VK_APP") or "").strip()
        if not token:
            self.skipTest("TOKEN_VK_APP is not configured.")

        report = run_parametrization_testing_loop(
            SampleAppConfig(
                api_base=(os.getenv("VIKTOR_API_BASE") or "https://demo.viktor.ai/api").strip(),
                workspace_id=2366,
                entity_id=11838,
                token=token,
            ),
            max_auto_scenarios=3,
        )

        output_path = write_loop_report_json(
            report,
            ROOT / "artifacts" / "sample_app_loop_report.json",
        )

        self.assertEqual(report.entity.id, 11838)
        self.assertGreater(len(report.iterations), 0)
        self.assertTrue(output_path.exists())
        self.assertTrue(any(iteration.scenario.name.startswith("toggle_") for iteration in report.iterations))
        self.assertIsNotNone(report.default_payload)
        self.assertIsNotNone(report.default_validation)
        self.assertTrue(report.default_validation.success)
        self.assertGreater(len(report.method_probes), 0)

    def test_run_parametrization_bench_and_write_json(self) -> None:
        load_repo_env()
        token = (os.getenv("TOKEN_VK_APP") or "").strip()
        if not token:
            self.skipTest("TOKEN_VK_APP is not configured.")

        report = run_parametrization_bench(
            targets=load_targets_from_json(ROOT / "bench_targets.json"),
            max_auto_scenarios=2,
            output_root=ROOT / "artifacts" / "bench_test",
        )
        output_path = write_bench_report_json(
            report,
            ROOT / "artifacts" / "bench_test" / "bench_report.json",
        )

        self.assertEqual(len(report.apps), 2)
        self.assertTrue(output_path.exists())
        self.assertEqual(
            {(app.target.workspace_id, app.target.entity_id) for app in report.apps},
            {(2232, 11640), (2141, 11536)},
        )
        self.assertTrue(all(app.loop_report.default_validation for app in report.apps))
        self.assertTrue(all(app.loop_report.default_backfilled_validation for app in report.apps))
        self.assertTrue(all(app.loop_report.method_probes for app in report.apps))


if __name__ == "__main__":
    unittest.main()
