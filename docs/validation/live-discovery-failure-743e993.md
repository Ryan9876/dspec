# DS-CHG-001 — Physical live-discovery failure evidence

**Baseline build:** `743e99311a2af81ebfda0e6d94406feb72267785`  
**Observed on:** physical target Mac  
**Provider:** LM Studio at `http://127.0.0.1:1234`  
**Selected model:** `meta/muse-glimmer`

## Observed behavior

- DSpec launched successfully and `/api/health` reported the exact expected build, healthy database, LM Studio online, selected model `meta/muse-glimmer`, and `active_provider_ready=true`.
- LM Studio model discovery returned `meta/muse-glimmer` plus an embedding model.
- A direct OpenAI-compatible `/v1/chat/completions` request to `meta/muse-glimmer` succeeded and returned the requested content.
- The same DSpec build failed `Analyze gaps` with `assistant_unavailable`.
- The direct inference response reported 105 completion tokens, including 89 reasoning tokens, demonstrating that completion-token ceilings on this model include substantial hidden reasoning overhead.

## Diagnosis

The failure boundary is inside DSpec's DSPy discovery integration, not LM Studio reachability, model selection, or basic inference. The failing implementation used `ChatAdapter(use_json_adapter_fallback=False)` and a 900-token discovery completion ceiling.

The active discovery-efficiency amendment requires bounded DSPy discovery, a substantially smaller budget than full specification generation, predictable structured-output failure, and no unbounded retry amplification. It does not require a fixed 900-token ceiling.

## Corrective implementation

The repair candidate:

- uses DSPy `JSONAdapter` for the structured discovery signature;
- keeps `num_retries=1`;
- keeps all existing bounded context/schema limits;
- raises the discovery completion ceiling to 3072 tokens, still substantially below the 24,000-token full-spec generation budget;
- records that the configured completion ceiling may include hidden reasoning tokens and does not claim provider token accounting or a reasoning/visible split.

## Evidence status

- Direct LM Studio inference on baseline build: **PASS — user/runtime verified**.
- DSpec live discovery on baseline build: **FAIL**.
- Repair automated validation: pending CI.
- Repair physical target-Mac live discovery: **NOT TESTED** until a validated repair candidate is produced.
