# Source and attribution

This repository publishes the current author-owned schema harness at William
Qiu's request. The source base commit is recorded in
[docs/source-snapshot.json](docs/source-snapshot.json); the snapshot also includes
the verified working implementation that had not yet been committed there.

The description scope guard was adopted from an earlier author-owned harness
draft. Its original source SHA-256 and scope note are retained in
[schema_extract/guards/origin.json](schema_extract/guards/origin.json).

The Paper and Dataset prompts are the current supplied October 8, 2026 versions.
Their bytes and hashes are preserved, as is the supplied schema template.
Local attachment paths were removed from the prompt provenance manifest.

Neither the source checkout nor the target repository contained a license file.
This publication does not add a legal license. Third-party packages and the
official Python container image are external dependencies; their licenses apply
to their respective components. No dependency source, wheels or model weights
are bundled here.
