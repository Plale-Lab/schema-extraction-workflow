# Methodology

A stage groups related steps. A step is one minimal operation such as reading a
passage, requesting a decision or checking a response. Review can return a stage
to its evidence for bounded repair. The implementation has two logical model
layers, classification and extraction; preparation and deterministic assembly
surround those layers.

## Paper workflow

1. **Prepare passage evidence.** Extract PDF text and save source identities,
   physical page numbers and exact character intervals. Classification builds a
   relevance index and treats uncertainty as relevant. Name discovery still scans
   every passage, including units marked irrelevant.
2. **Discover names and group tables.** Propose dataset views, fields and other
   descriptive elements. Keep, merge or exclude candidates using the record
   entity, level of detail, files and relationships stated in the paper.
   A schema table denotes a dataset object or view identified across relevant
   passages and printed tables. A printed table may describe part of a dataset
   view, and several passages may describe that same view. Retain names, membership and supporting sources.
3. **Enrich attributes.** Fill field descriptions, datatypes, units and
   encoding attributes for small batches of established objects from focused
   passages. Every attribute needs its own source support or
   an explicit unknown decision. Name evidence alone does not establish a field
   description, datatype, unit, provenance or processing. Descriptions require
   supporting source statements. An unknown from selected passages does not
   prove absence from the whole paper.

Paper-side requests use the publication only. Dataset files, repository
documentation, human annotations and previous model answers are excluded.

## Dataset workflow

1. **Read native facts and fix the sample.** Native adapters preserve physical
   column names and order, duplicate columns and available type metadata.
   Preserve the original bytes and a saved sample of at most 200 rows for each
   selected table/file using seed 42; keep all rows if fewer are available.
   Every later step uses that same sample.
2. **Establish names and bind documentation.** Physical columns fix the field
   inventory. Position-based handles distinguish duplicate names. Associate
   supporting documents with the corresponding tables and fields.
3. **Enrich attributes.** The model uses the native readout, fixed sample and
   associated documents to fill field descriptions, datatypes, units and encoding
   attributes. Descriptions require supporting source statements or documentation;
   a name or general knowledge alone does not establish a description.
   Evidence distinguishes explicit declarations,
   direct observations and inference. Sample values do not establish constraints
   over an entire dataset. Unsupported optional attributes remain unknown.

The current campaign adapter verifies the saved archive and reads native facts;
sampling was upstream of that adapter. CSV with a header, headerless CSV.gz and
Parquet are its implemented readout formats. Other formats require an explicit
adapter rather than an assumption that this snapshot already supports them.

## Shared harness

Requests are bound to JSON contracts and an established object inventory.
Missing decision keys, foreign source IDs, unsupported new names and stale
membership links are rejected. Declared, observed and inferred claims retain a
value, reason and references; unresolved decisions have unknown status.
These statuses apply to structural and semantic attributes. Sample observations
and inference are not treated as declared constraints. Aggregate profiling
statistics are not emitted as schema claims.

Separate model review checks source evidence without relying on the generator's
persuasive rationale. Deterministic guards additionally reject known failures,
including unsupported population constraints and provenance claims based only on
headers. Review feedback permits bounded repair or explicit subdivision.
Context, wire, output and repair limits produce visible stops rather than silent
source clipping. A lost transport observation requires inspection before another
generation request.

Assembly is deterministic. Accepted outputs retain `full-schema.json` separately
from `evidence.json`. Acceptance checks include source replay, coverage,
independent review, original request/response and tokenizer replay, and
byte-identical delivery from an owned snapshot container.

Well-formed output, exact citations and passing mechanical checks do not guarantee
semantic correctness. Offline tests and synthetic examples establish code behavior,
not semantic validation across the twenty study cases. Failures can arise from
source accounting, grouping, printed-name fidelity, unsupported assertions,
response contracts or request limits; they are not attributed solely to context
length.

The accompanying [TeX fragment](methodology.tex) and
[PDF preview](methodology_preview.pdf) are the latest supplied Section II B–D
methodology files. They are preserved without editing the paper's main file.
