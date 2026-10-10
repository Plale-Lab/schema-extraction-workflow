# Validation

Verified locally on October 10, 2026. No paid or model inference was launched.

| Check | Result and scope |
| --- | --- |
| Authoritative source checkout | 105 existing offline unittest tests passed; source files and Git status remained unchanged |
| Published snapshot | 93 offline unittest tests passed in a fresh Python 3.12.10 environment with pinned requirements |
| Initial published snapshot CI | [GitHub offline checks](https://github.com/Plale-Lab/schema-extraction-workflow/actions/runs/38033140165) passed on Linux: 93 tests, dependency integrity, synthetic preparation and isolated delivery |
| Dependency integrity | `python -m pip check` passed |
| Native transport tests | Local fake HTTP service only; exact-tokenizer ordering, byte preservation, limits and no retry after failed observations |
| Synthetic fixed sample | 257 fictional rows yielded 200 saved rows using seed 42; repeated runs produced identical sample bytes |
| Small sample | All three fictional rows retained in original order |
| Dataset preparation | Native CSV replay passed; duplicate names retained distinct positional handles |
| Offline CLI | Documented Paper classification and Dataset extraction planning commands passed; zero model calls |
| Docker delivery | Synthetic contract validation, separate schema/evidence, byte equality and ownership-checked container cleanup passed |
| Source/prompt integrity | Supplied Paper/Dataset prompt and schema template bytes unchanged; provenance manifests omit local paths |
| Methodology | Latest supplied version-2 TeX and two-page PDF verified by readable bytes and SHA-256; PDF text and metadata inspected |
| Publication audit | No sensitive-pattern matches; excluded historical data, notes, logs, credentials, caches and model files absent |
| JSON and formatting | Candidate JSON files parsed strictly; Git whitespace check passed |

The source suite includes twelve tests for auxiliary immediate names-only output
and review routes. Those routes and tests are omitted from this snapshot so its
public entrypoints describe the names-first inventory, attribute enrichment and
acceptance workflow. Two retained regression excerpts use fictional replacements
for historical real-source material.

The Docker image is the official Python image pinned to
`python@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1`.
The production publication changes are the portable snapshot image and optional
override, normalized guard line endings, and omission of an unused legacy guard
bridge referencing an absent prior-harness helper. The active semantic guard and
owned-container acceptance and delivery checks remain intact.

## Scope limits

No fresh real-case generation, complete-case semantic acceptance or twenty-case
evaluation was run for this publication. Passing code tests does not establish
semantic correctness. Study data and historical accepted products are not in the
repository. The current campaign preparation adapter still expects the original
ten-pair manifest layout and pre-frozen sample archive described in
[USAGE.md](USAGE.md).

The TeX is the supplied Section II B–D replacement fragment. Its accompanying PDF
is the supplied preview, inspected here rather than recompiled. The paper's main
file was not edited.

The version-2 methodology files have SHA-256 values
`a3086c371a2b7462612f485c42e2cfcb4d9c1d780f785b0107a4457b525a6dc7`
(TeX) and `ac5ba69a0679e7b45884adc4231ec11d6f8d458c9dba9d6e2446b5594b846b70`
(PDF). Their additions explicitly cover field descriptions, dataset views and
structural/semantic provenance. This documentation update changes no runtime
behavior or supplied prompt/template bytes.
