"""Generate fictional source fixtures and zero-call request plans."""
from pathlib import Path
import argparse
import csv
import hashlib
import io
import json
import random
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from schema_extract.compiler import base_ids, canonical, classification_plan, extraction_plan, identity
from schema_extract.native import atomic, sha
from schema_extract.preparation import add_text, prepare_dataset, replay_preparation


def csv_bytes(rows):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(["record_id", "reading", "reading"])
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8")


def plan(root, registry, kind, inventory=None):
    # These illustrative decisions are hand-written fixtures, not model results.
    decisions = [{"source_id": sid, "label": "relevant", "quote_ids": [sid],
                  "reason": "Hand-written decision for the fictional example."}
                 for sid in base_ids(registry)]
    atomic(root / "classification-decisions.json", decisions)
    atomic(root / "classification-plan.json", classification_plan(registry, kind))
    atomic(root / "extraction-plan.json", extraction_plan(registry, decisions, kind, inventory))


def create(output, rows=257):
    output = Path(output).resolve()
    if output.exists():
        raise ValueError("Use a new output directory; saved examples are not overwritten")
    if rows < 1:
        raise ValueError("At least one synthetic row is required")
    dataset = output / "dataset"
    (dataset / "inputs").mkdir(parents=True)
    population = [[str(i), f"{i / 10:.1f}", f"{i / 20:.2f}"] for i in range(rows)]
    # Preserve all rows below the cap. Sorting restores original row order.
    selected = sorted(random.Random(42).sample(range(rows), 200)) if rows > 200 else list(range(rows))
    raw = csv_bytes([population[i] for i in selected])
    original = dataset / "inputs/original-sample.csv"
    original.write_bytes(raw)
    sample = {"source_format": ".csv", "sampled_rows": len(selected),
              "columns": ["record_id", "reading", "reading"], "seed": 42,
              "sampling_algorithm": "stdlib random.Random(42).sample; indices sorted into source order",
              "source_rows": rows, "selected_row_indices": selected,
              "synthetic_population_sha256": hashlib.sha256(csv_bytes(population)).hexdigest(),
              "output_sha256": hashlib.sha256(raw).hexdigest(), "synthetic_only": True}
    document = output / "synthetic-documentation.txt"
    document.write_text(
        "Fictional sensor records. record_id identifies a generated record. "
        "The two physical reading columns describe independent fictional channels. "
        "All values are synthetic. No population-wide constraints are declared.\n",
        encoding="utf-8")
    registry, texts, inputs = prepare_dataset(sample, raw, [(document, document.name)], dataset)
    inputs.insert(0, {"path": str(original), "sha256": sha(original),
                      "source_format": ".csv", "representation": "original_sample"})
    (dataset / "source-registry.json").write_text(json.dumps(registry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    atomic(dataset / "source-texts.json", texts)
    atomic(dataset / "sample-provenance.json", sample)
    case = {"directory": str(dataset), "kind": "dataset", "case_id": "synthetic-dataset",
            "registry_sha256": identity(registry), "input_manifest": inputs}
    atomic(dataset / "preparation.json", case)
    replay_preparation(case)
    inventory = __import__("json").loads((dataset / "dataset-inventory.json").read_text(encoding="utf-8"))
    assert len(inventory) == 3 and len({f["field_id"] for f in inventory}) == 3
    assert [f["name"] for f in inventory] == sample["columns"]
    plan(dataset, registry, "dataset", inventory)

    paper = output / "paper"
    paper.mkdir()
    text = (
        "A fictional sensor dataset contains a readings table with one row per record. "
        "Its fields are record_id and signal. signal is documented in volts. "
        "This paragraph is fabricated for an offline software example.\n")
    paper_registry, paper_texts = {}, {}
    add_text(paper_registry, paper_texts, text, hashlib.sha256(text.encode()).hexdigest(),
             1, "synthetic-paper.txt", "paper")
    (paper / "source-registry.json").write_text(json.dumps(paper_registry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    atomic(paper / "source-texts.json", paper_texts)
    plan(paper, paper_registry, "paper")
    report = {"synthetic_only": True, "model_calls": 0, "semantic_acceptance": False,
              "source_rows": rows, "sample_rows": len(selected), "sample_sha256": sha(original),
              "seed": 42, "duplicate_column_handles_preserved": True,
              "preparation_replay_passed": True}
    atomic(output / "example-report.json", report)
    print(canonical(report))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True)
    parser.add_argument("--rows", type=int, default=257)
    args = parser.parse_args()
    create(args.out, args.rows)
