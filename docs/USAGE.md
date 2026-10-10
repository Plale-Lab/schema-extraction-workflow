# Usage and native configuration

## Offline planning

A registry is an insertion-ordered object keyed by source ID. Each unit requires
`source_id`, `source_sha256`, `physical_page`, `start_char`, `end_char` and exact
`text`. Character intervals must match the text length. Additional fields retain
source paths, kinds and representation metadata. Citation-only entries supplement
the original base units without replacing them.

Classification decisions are an ordered array of
`{source_id, label, quote_ids, reason}`. Cover every base unit exactly once in
original order. Labels are `relevant` or `none`; relevant decisions have one to
three valid navigation IDs. Dataset extraction also requires an ordered inventory
of `{field_id, name, source_id}`, with distinct handles for repeated names.
Paper planning rejects a dataset inventory.

Run `python tools/synthetic_example.py --out runs/example` for generated input
files and classification/extraction plans. The written decisions are synthetic
fixtures, not model outputs. The compiler conservatively checks bytes during
offline planning. Native dispatch additionally checks the exact tokenizer.

## Existing campaign preparation adapter

The current study adapter deliberately expects exactly ten Paper/Dataset pairs
with matching pair IDs. It uses these directories supplied by the operator:

| Input | Layout and required fields |
| --- | --- |
| Papers | `source-selection.json` with `cases`: each entry has `pair_id`, `folder`, `sha256`; each PDF is `<pair_id>/paper.pdf` |
| Samples | `manifest.json` with `tables`: each entry has `pair_id`, `output_path`, `output_sha256`, `source_format`, `sampled_rows`, optionally `columns` |
| Sample archive | `workshop-random-200rows-seed42-20261002-v1.zip`; `output_path` names a member containing the saved sample bytes |
| Supporting documents | `manifest.json` with `documents`: each entry has `pair_id`, `package_path`, `sha256`, `source_path`; an empty document list is allowed |

`source_format` is `.csv` for a headed CSV, `.csv.gz` for a headerless compressed
CSV, or `.parquet`. Headerless input receives physical-position handles, not
invented original names. Preserve upstream sampling provenance, seed, row limit,
original source hash, selected row indices and sample hash alongside the required
manifest fields. The adapter verifies hashes and shapes, but cannot recover an
upstream random selection from its seed alone.

The fixed archive basename and ten-pair scope are compatibility constraints of
this snapshot. Do not rename arbitrary new data into that layout and call it a
reproduction of the study. For a small native CSV demonstration, use the synthetic
example; arbitrary production campaigns require an explicitly tested adapter.

```console
python -m schema_extract.campaign prepare --papers data/papers --samples data/samples --supporting data/supporting --out runs/campaign
```

The output preserves source bytes, registries, input manifests and sample
provenance. Existing preparation is replayed rather than overwritten.

## Provider binding

Create a local ignored `provider.local.json`. Start from
[config/provider.example.json](../config/provider.example.json), replace
`MODEL_ID` with the authorized service's exact model ID and provide the existing
credential through the named environment variable. The example contains only a
credential reference. Do not put a token in the JSON.

The base URL must end in `/v1` and contain no credentials, query or fragment.
The service must provide the model, health, metrics and exact-tokenizer endpoints,
guided JSON decoding and the supported chat-template controls. This adapter was
implemented for a vLLM-compatible service; other providers need qualification.

Generation remains disabled in the committed workflow configuration. An operator
must intentionally set `native_generation_enabled` to true before a separately
authorized live run. Offline compiler commands never enable it. This publication
and its tests make no model inference calls.

## Immutable bounded run policy

Copy [config/run-policy.example.json](../config/run-policy.example.json) into a
local file and supply the exact fresh campaign's case IDs. The policy must cover
the campaign scope exactly. All four limits are positive finite integers:
aggregate generation requests and wall seconds, and per-case generation requests
and wall seconds. The example numbers illustrate admission control and are not
validated sufficient budgets for the study.

The policy hash is bound to a durable admission ledger. A pending or uncertain
request blocks further generation; changing a policy cannot reset used budgets.
Only the completion profile and compact-batch routing may be overridden.

```console
python -m schema_extract.campaign run --campaign runs/campaign --provider provider.local.json --endpoint http://127.0.0.1:8000/v1 --run-policy run-policy.local.json
```

Use `classify` or `extract` in place of `run` to dispatch one logical layer.
`extract --case <case-id>` selects extraction work within the bound policy;
`--follow` observes classification completion. Keep existing request evidence
and verify controller ownership before resuming. A timeout is an observation
failure, not proof that remote generation stopped.

## Delivery and recovery

Docker must be running for final snapshot delivery. The default image is the
official Python image pinned by digest in `schema_extract/assembly.py`.
`SCHEMA_SNAPSHOT_IMAGE` may select a trusted locally available Python image;
record its immutable digest when using that override.

`python tools/snapshot_smoke.py` exercises synthetic byte-identical schema and
evidence delivery in a container with no network. Acceptance on real cases still
requires the complete source, native transcript, tokenizer, coverage and review
checks. Passing the smoke check establishes only the delivery mechanism.

After a separately authorized campaign has accepted outputs, run:

```console
python tools/collect_deliverables.py --campaign runs/campaign
```

The collector replays acceptance and preserves output bytes. It does not generate
new schemas. Keep run directories outside version control: they can contain
source material, local paths and raw provider responses.
