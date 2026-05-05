# vk-params-pydantic

Prototype pipeline for capturing a VIKTOR app parametrization into generic Pydantic models and writing a JSON artifact.

## What it does

- calls the sample app at workspace `2366`, entity `11838`
- validates the raw REST responses with generic Pydantic wrappers
- normalizes the parametrization into a container/field tree
- infers a payload tree from `entity.properties`
- can run a multi-iteration testing loop with modified params against `/parametrization/`
- writes the results to `vk-params-pydantic/artifacts/`

## Files

- `src/vk_params_pydantic/bench.py`: API client, recursive normalizer, loop runner, JSON writers
- `src/vk_params_pydantic/models.py`: raw and normalized Pydantic models
- `tests/test_bench.py`: unit test for payload inference and integration test for the sample app capture

## Run

```bash
python -m vk_params_pydantic
python -m vk_params_pydantic --mode single
python -m unittest discover -s vk-params-pydantic/tests -p 'test_*.py'
```

## Loop mode

Default CLI mode is `loop`. It will:

- fetch the saved sample app payload
- generate a few generic override scenarios
- call `/parametrization/` once per scenario
- diff the normalized tree against baseline
- write:
  - `sample_app_capture.json` or custom `--output`
  - per-iteration artifacts under `artifacts/iterations/`

Optional custom scenarios:

```bash
python -m vk_params_pydantic \
  --scenario-file vk-params-pydantic/my_scenarios.json
```
