# Repository instructions

Preserve the two logical model layers: classification and extraction.
Preparation, name discovery and table grouping, attribute enrichment, independent
review and bounded repair, and deterministic acceptance are explicit operations.

Use current prompts and schema templates. Preserve supplied prompt bytes;
version any intentional future change. Paper prompts use paper passages only.
Dataset prompts use native facts, the saved sample and associated documentation.
Never feed historical schemas or prior case answers into extraction.

Offline checks and synthetic demonstrations do not establish model generation
success or semantic correctness on real cases. Failed coverage or review blocks
acceptance. Keep pure schema and evidence as separate outputs.

Model generation is disabled by default. Tests must use local fake services or
mocks. Never commit credentials, private paths, raw run logs or real datasets.
