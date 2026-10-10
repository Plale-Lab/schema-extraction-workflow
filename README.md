# Schema extraction workflow

This is a reproducible source snapshot of the implemented names-first schema
harness. It establishes table and field names before enriching attributes,
then reviews the decisions and assembles a pure schema with separate evidence.

There are two logical model layers: **classification** and **extraction**.
Classification provides a passage navigation index. Extraction includes name
discovery, table grouping, attribute filling, review and bounded repair.
The three stages in the methodology describe related operations; they are
not three additional model layers.

The snapshot preserves the current working implementation and supplied prompts.
Model generation is disabled by default. Publishing this code does not certify
semantic correctness across all twenty study cases. The repository contains
synthetic examples and tests, without study inputs, historical model responses
or campaign outputs.

## Install and check

Use Python 3.12. From a fresh clone, create an environment and install the pinned
requirements:

```console
python -m venv .venv
```

Activate it with `source .venv/bin/activate` on Linux/macOS or
`.venv\Scripts\Activate.ps1` in PowerShell, then run:

```console
python -m pip install -r requirements.txt
python -m pip check
python -B -m unittest discover -s tests -v
python tools/synthetic_example.py --out runs/synthetic
```

The example creates fictional observations, fixes at most 200 rows with seed 42,
runs the native CSV reader and source replay, and writes offline Paper and Dataset
request plans. It uses hand-written synthetic classification decisions and makes
zero model calls. Re-running it requires a new output directory.

The offline compiler can also consume your own prepared registry:

```console
python -m schema_extract classification --kind paper --registry runs/synthetic/paper/source-registry.json --out runs/paper-classification-plan.json
python -m schema_extract extraction --kind dataset --registry runs/synthetic/dataset/source-registry.json --decisions runs/synthetic/dataset/classification-decisions.json --dataset-inventory runs/synthetic/dataset/dataset-inventory.json --out runs/dataset-extraction-plan.json
```

Plans retain original source identities, ordered coverage and complete target
text. They are prepared requests, without generated schema results.

## Workflow and outputs

| Source | Prepare evidence | Establish names | Enrich attributes |
| --- | --- | --- | --- |
| Paper | Extract PDF text; record source hashes, physical pages and exact character intervals | Scan all passages; reconcile candidate views and fields using record entities, granularity and source relationships | Fill small batches from focused paper passages; retain independent support for each attribute |
| Dataset | Read physical columns, order and native metadata; preserve a fixed seed-42 sample of at most 200 rows and support documents | Keep position-based field handles, including duplicate names; bind documents to their tables and fields | Interpret native facts, saved observations and documents; distinguish declared, observed, inferred and unknown decisions |

Paper extraction excludes dataset files and external knowledge. Dataset
observations do not establish population-wide requiredness, uniqueness or other
constraints. Unsupported optional attributes may remain unknown; known claims,
names and membership still undergo review.

Accepted runs export `full-schema.json` and `evidence.json` separately. Evidence
includes sources, coverage, conflicts and unresolved decisions. A single-table
schema has a `table` root; multi-table output has a `tables` array of schema
objects. Evidence is not embedded into the comparison schema.

## Configuration and native runs

[config/workflow.json](config/workflow.json) sets the names-first profile, request
limits, bounded repair and optional-unknown policy. Important defaults are:

- Model generation disabled; runtime manual injection disabled.
- Context limit 32,768 tokens with a 512-token reserve.
- Classification/extraction output limits 3,072/4,096 tokens.
- Maximum wire size 65,536 bytes; at most two repairs and split depth two.
- Compact attribute batches of at most four objects.

The native adapter targets a vLLM-compatible service providing `/v1/models`,
`/v1/chat/completions`, `/tokenize`, `/health` and `/metrics`, including guided
JSON and the configured model's exact tokenizer/chat template. An arbitrary
OpenAI-compatible endpoint is not sufficient.

See [native usage](docs/USAGE.md) for provider configuration, bounded run policy,
the existing ten-pair campaign input layout and recovery behavior. The campaign
preparer consumes an already-frozen sample archive; it does not create the study's
random sample. The synthetic example demonstrates the fixed-sample procedure.

Final delivery requires Docker and a pinned official Python image. To check
the delivery mechanism without inference:

```console
docker pull python@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1
python tools/snapshot_smoke.py
```

The container has no network and is removed after ownership and byte checks.
The smoke check verifies synthetic schema/evidence delivery; it does not perform
a real-case semantic acceptance run.

## Methodology and provenance

- [Plain-English methodology](docs/METHODOLOGY.md).
- [Latest methodology TeX](docs/methodology.tex) and [rendered preview](docs/methodology_preview.pdf).
  The TeX replaces paper Section II B–D and is a fragment, not a standalone document.
- [Snapshot provenance and publication changes](docs/source-snapshot.json).
- [Validation scope](docs/VALIDATION.md) and [source attribution](NOTICE.md).

The Paper/Dataset prompt bytes live in `templates/` and
`prompts/versions/swathi-2026-10-08-descriptions/`. The active extraction route
uses the copies in `templates/`, with harness instructions in `prompts/`.
`templates/template.json` is the supplied schema template;
`contracts/` contains the enforced request, decision and final-schema contracts.
