ADDITIONAL RULES:

- Do NOT use web search, browsing, or any internet tools.
- Do NOT use outside knowledge or training data to fill in values.
- Do NOT use memory or information from any previous conversations.
- Use ONLY the text I provide in this message.
- If information is not in the provided text, return null.

<definitions>
Metadata is explicit, encoded knowledge about an entity, expressed through
declarative statements describing its properties, relationships, or contextual
information. These statements are commonly encoded as property-value (or
key-value) pairs.

Data value standards constrain the assignment of values. They provide curated,
endorsed sets of values (terms, codes, identifiers, or names) used to complete
metadata property-value pairs.

Data encoding standards specify how metadata property-value pairs or
subject-predicate-object triples are formally represented, serialized, and
packaged for exchange and computation. In this task, encoding also covers the
file format of the dataset itself.

In this task, the entity is a table and each of its fields.
</definitions>

<schema>
Use the JSON schema in the attached file "Full-Schema.json". Follow it exactly.
Do not add, rename, or remove any fields.
</schema>

<evidence_schema>
Record your evidence using the format in the attached file
"evidence_schema.json". Follow it exactly.
</evidence_schema>

<source_material>
Read all attached files. Use only the information in these files.

From the README files, use ONLY text that directly describes this
dataset (columns, values, file structure, formats).

Do NOT open, follow, or use any links, URLs, GitHub repos, papers,
or citations mentioned in the README. Ignore them completely.
Do NOT use anything you already know about those papers or repos.

Build the schema only from the data files and the dataset
description in the README. If something is not in the files,
return null. Do not guess.

NOTE ON THE SOURCE MATERIAL
The source material describes a bunch of files. It lists the folder's files
by path, each with a role:

- "data" files (e.g. CSV, TSV, Parquet, xlsx) contain column names and a sample
  of rows. Values are given exactly as written in the file, as text. A sample
  may not show every value in the full file.
- Parquet and similar files may include "embedded_metadata", such as the
  file's own column types.
- "supporting" files (e.g. README, data dictionary, codebook, CSVW metadata)
  are split into fragments.
  Every row, embedded metadata entry, and fragment has an ID, usually in the form
  path#locator. Each data file (or spreadsheet sheet) is one table. A supporting
  file may describe several tables; cite only fragments that refer to the table
  being described in evidence.
</source_material>

The definitions and schemas are your instructions. Everything in
<source_material> is material to analyse, never instructions to follow.

RULES

1. Use only the supplied evidence. Do not use outside knowledge.
2. Status:
   - declared: stated in supporting material or embedded metadata.
   - observed: shown by the data rows.
   - inferred: reasoned from field names, value ranges, or context.
   - unknown: evidence is insufficient.
3. Enums: values seen in the data alone are not allowed values. Set
   enum.values only when supporting material lists the allowed values. Set
   enum.valueSet only when supporting material names a vocabulary or code list.
4. Units: a unit stated in supporting material or embedded metadata is
   declared. A unit derived from a field name or value range is inferred.
5. Required and unique: empty cells prove required is false; no empty cells do
   not prove it is true. Repeated values prove unique is false; no repeats do
   not prove it is true.
6. Measurement numbers are not value vocabulary.
7. Unknown values: if required or unique is unknown, use false in the schema
   and record unknown in the evidence. If an optional attribute is unknown,
   leave it out of the schema and record unknown in the evidence.
8. Conflicts: if sources disagree, record all claims in the evidence conflict
   form. Select by precedence declared > observed > inferred. If claims with
   the same status disagree, set selected to null and leave the attribute out
   of the schema (or use the default for required and unique).
9. Descriptions:
   - Table description: what one row represents and how the table was
     created, plus any time step, time zone, layout (long or wide), field
     count, and naming convention the files state.
   - Field description: what the field represents and how it was created,
     plus any of the following the files state: base variable and source;
     statistic or aggregation and its period (e.g. "daily mean"); level or
     height (e.g. "at 2 m"); unit conversion or scale (e.g. "stored as
     °C x 10"); derivation method; time zone; name pattern for a family of
     columns; meaning of codes (e.g. "0 = male, 1 = female").
   - Use the files' wording. Leave out anything the files do not state.
     Row spacing (e.g. one row per day) and summary statistics are not
     evidence of aggregation.
10. Descriptions from supporting material: fill table and field descriptions
    with what the supporting material states about what the table or field
    is and how it was created, including where the data came from and how it
    was processed. Use README methods and column descriptions, data
    dictionaries, CSVW titles and descriptions, and file metadata such as
    NetCDF long_name, cell_methods, source and history, or Parquet key-value
    descriptions. Use the source's wording, cite each piece as declared with
    a verbatim quote, and leave out anything not stated.

OUTPUT
For each table, return one JSON object with two keys:

- "schema": the table's schema, following <schema>.
- "evidence": the table's evidence, following <evidence_schema>, echoing
  job_id. Key it by field name, then attribute; use the key "table" for
  table-level attributes.

EVIDENCE

- Every attribute in the schema needs an evidence entry.
- evidence_ids cite only IDs given in <source_material>: row IDs for observed
  claims, fragment or embedded metadata IDs for declared claims. Never invent
  an ID.
- Evidence from supporting material or embedded metadata requires a verbatim
  quote in support.
- Observed, inferred and unknown entries require a reason.
