"""Check isolated byte-identical delivery with synthetic schema/evidence only."""
from pathlib import Path
from tempfile import TemporaryDirectory
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import jsonschema
from schema_extract.assembly import owned_delivery, SANDBOX_IMAGE
from schema_extract.compiler import ROOT, canonical, strict_json
from schema_extract.native import sha


def main():
    schema = {"table": {"name": "synthetic_records", "structure": {"fields": [
        {"name": "record_id", "required": False, "unique": False, "value": {"datatype": "integer"}},
        {"name": "signal", "required": False, "unique": False, "value": {"datatype": "decimal"}}]}, "encoding": {"fileType": "csv", "header": True}}}
    evidence = {"synthetic_only": True, "semantic_acceptance": False,
                "note": "Hand-written fixture for the delivery mechanism."}
    candidate = {"schema": schema, "evidence": evidence}

    def validate(snapshot):
        assert snapshot == candidate
        jsonschema.validate(snapshot["schema"], strict_json((ROOT / "contracts/final-schema.json").read_text(encoding="utf-8")))
        return {"passed": True, "synthetic_only": True, "semantic_acceptance": False,
                "schema_contract_passed": True}

    with TemporaryDirectory(prefix="schema-snapshot-smoke-") as folder:
        receipt = owned_delivery(candidate, folder, validate,
                                 {"full-schema.json": schema, "evidence.json": evidence})
        for name, expected in [("full-schema.json", schema), ("evidence.json", evidence)]:
            observed = receipt["deliverables"][name]
            path = Path(observed["artifact"])
            assert path.read_bytes() == (canonical(expected) + "\n").encode("utf-8")
            assert sha(path) == observed["sha256"] == observed["container_snapshot_sha256"]
        lease = strict_json((Path(folder) / "snapshot-lease.json").read_text(encoding="utf-8"))
        assert lease["owned_container_removed"]
        print(canonical({"passed": True, "synthetic_only": True, "model_calls": 0,
                         "semantic_acceptance": False, "byte_identical_delivery": True,
                         "schema_and_evidence_separate": True, "owned_container_removed": True,
                         "snapshot_image": SANDBOX_IMAGE}))


if __name__ == "__main__":
    main()
