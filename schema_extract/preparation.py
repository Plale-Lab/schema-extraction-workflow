"""Fresh original-input preparation; no historical model answers are loaded."""
from pathlib import Path
import csv
import gzip
import hashlib
import io
import json
import re
import shutil
import zipfile
from datetime import date, datetime

from .compiler import canonical, identity, strict_json, validate_registry
from .native import atomic, sha


def extract_pages(path):
    from pypdf import PdfReader
    return [page.extract_text() or '' for page in PdfReader(path).pages]


def add_text(registry, texts, text, digest, page, path, kind, target=1500, **extra):
    texts.setdefault(digest, {})[str(page)] = text
    cursor = 0
    ids = []
    while cursor < len(text):
        end = min(len(text), cursor + target)
        if end < len(text):
            boundary = text.rfind('\n', cursor + 1, end)
            if boundary > cursor:
                end = boundary + 1
        sid = 'src-' + identity([digest, page, cursor, end, path, kind])[:24]
        registry[sid] = {'source_id': sid, 'source_sha256': digest,
                         'physical_page': page, 'start_char': cursor, 'end_char': end,
                         'text': text[cursor:end], 'source_path': path, 'kind': kind,
                         'section_context': path + '#page-' + str(page),
                         'continuation': cursor > 0, **extra}
        ids.append(sid)
        cursor = end
    if ''.join(registry[sid]['text'] for sid in ids) != text:
        raise ValueError('Preparation lost original text')
    return ids


def lexical(value):
    if value is None:
        return ''
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, (list, dict)):
        return canonical(value)
    return str(value)


def decode_sample(raw, fmt):
    metadata = []
    if fmt == '.parquet':
        import pyarrow as pa
        import pyarrow.parquet as pq
        table = pq.read_table(pa.BufferReader(raw))
        names = table.column_names
        # Work positionally: duplicate field names cannot collapse dictionary keys.
        columns = [table.column(i).to_pylist() for i in range(table.num_columns)]
        rows = [[lexical(column[i]) for column in columns] for i in range(table.num_rows)]
        for i, field in enumerate(table.schema):
            typ = field.type
            datatype = None
            if pa.types.is_integer(typ): datatype = 'integer'
            elif pa.types.is_floating(typ) or pa.types.is_decimal(typ): datatype = 'decimal'
            elif pa.types.is_boolean(typ): datatype = 'boolean'
            elif pa.types.is_string(typ) or pa.types.is_large_string(typ): datatype = 'string'
            elif pa.types.is_date(typ): datatype = 'date'
            elif pa.types.is_timestamp(typ): datatype = 'timestamp'
            metadata.append({'column_index': i, 'name': field.name, 'native_type': str(typ),
                             'template_datatype': datatype, 'nullable': field.nullable,
                             'metadata': {k.decode('utf-8'): v.decode('utf-8') for k, v in (field.metadata or {}).items()}})
        readout = io.StringIO(newline='')
        writer = csv.writer(readout, lineterminator='\n')
        writer.writerow(names)
        writer.writerows(rows)
        return names, rows, readout.getvalue(), metadata
    text = (gzip.decompress(raw) if fmt == '.csv.gz' else raw).decode('utf-8')
    reader = list(csv.reader(io.StringIO(text, newline='')))
    if fmt == '.csv.gz':
        names = ['physical-position-' + str(i + 1) for i in range(len(reader[0]))]
        rows = reader
    else:
        names, rows = reader[0], reader[1:]
    return names, rows, text, metadata


def prepare_dataset(sample, raw, documents, out):
    registry, texts, inputs = {}, {}, []
    fmt = sample['source_format']
    names, rows, text, metadata = decode_sample(raw, fmt)
    if len(rows) != sample['sampled_rows'] or any(len(row) != len(names) for row in rows):
        raise ValueError('Native sample shape differs from original manifest')
    if sample.get('columns') is not None and names != sample['columns']:
        raise ValueError('Native ordered fields differ from original sample')
    # Original bytes are preserved separately; binary/decompressed representations
    # are explicit machine readouts and must replay from their original binary.
    readout = out / 'inputs' / 'sample-readout.csv'
    readout.write_bytes(text.encode('utf-8'))
    digest = sha(readout)
    add_text(registry, texts, text, digest, 1, readout.name, 'data',
             machine_readout=fmt != '.csv', original_binary_sha256=hashlib.sha256(raw).hexdigest(),
             original_format=fmt, physical_header_present=fmt == '.csv')
    inputs.append({'path': str(readout), 'sha256': digest, 'representation': 'native_csv_readout' if fmt != '.csv' else 'original_sample_bytes'})
    # A field handle is positional and survives repeated names. Metadata is a
    # native observation, never a claim that an unnamed CSV has an actual header.
    inventory = []
    facts = []
    for i, name in enumerate(names):
        values = [row[i] for row in rows]
        entry = {'field_id': 'f' + str(i).zfill(4), 'name': name, 'column_index': i,
                 'name_origin': 'native_embedded_schema' if fmt == '.parquet' else ('positional_handle_without_original_name' if fmt == '.csv.gz' else 'original_header'),
                 'original_sample_sha256': hashlib.sha256(raw).hexdigest(),
                 'sample_rows': len(rows), 'values_as_written': values,
                 'embedded_metadata': metadata[i] if metadata else None,
                 'population_constraints_not_established': True}
        derived = out / 'inputs' / ('field-' + str(i).zfill(4) + '.json')
        derived.write_bytes(canonical(entry).encode('utf-8'))
        ids = add_text(registry, texts, derived.read_text(encoding='utf-8'), sha(derived), 1,
                       derived.name, 'native_field_readout', machine_readout=True,
                       original_binary_sha256=hashlib.sha256(raw).hexdigest(), column_index=i,
                       citation_only=True)
        inventory.append({'field_id': entry['field_id'], 'name': name, 'source_id': ids[0]})
        facts.append({**entry, 'source_ids': ids})
        inputs.append({'path': str(derived), 'sha256': sha(derived), 'representation': 'native_field_readout'})
    for document, origin in documents:
        raw_doc = document.read_bytes()
        target = out / 'inputs' / document.name
        if target.exists():
            target = out / 'inputs' / (sha(document)[:12] + '-' + document.name)
        target.write_bytes(raw_doc)
        inputs.append({'path': str(target), 'sha256': sha(target), 'original_path': str(document), 'source_origin': origin, 'representation': 'original_supporting_document'})
        if target.suffix.lower() == '.pdf':
            for p, page_text in enumerate(extract_pages(target), 1):
                add_text(registry, texts, page_text, sha(target), p, target.name, 'supporting')
        else:
            add_text(registry, texts, raw_doc.decode('utf-8'), sha(target), 1, target.name, 'supporting')
    atomic(out / 'dataset-inventory.json', inventory)
    atomic(out / 'dataset-facts.json', facts)
    return registry, texts, inputs


def prepare_campaign(papers_root, samples_root, supporting_root, output):
    papers_root, samples_root, supporting_root, output = map(Path, (papers_root, samples_root, supporting_root, output))
    if (output / 'campaign.json').exists():
        raise ValueError('Campaign already prepared; reuse and verify existing identity')
    papers = strict_json((papers_root / 'source-selection.json').read_text(encoding='utf-8'))['cases']
    manifest = strict_json((samples_root / 'manifest.json').read_text(encoding='utf-8'))
    documents = strict_json((supporting_root / 'manifest.json').read_text(encoding='utf-8'))['documents']
    archive_path = samples_root / 'workshop-random-200rows-seed42-20261002-v1.zip'
    samples = {s['pair_id']: s for s in manifest['tables']}
    if len(papers) != 10 or len(samples) != 10 or {p['pair_id'] for p in papers} != set(samples):
        raise ValueError('Exactly ten original Paper/Dataset pairs required')
    campaign = {'model_layers': ['classification', 'extraction'], 'cases': [],
                'original_papers_manifest_sha256': sha(papers_root / 'source-selection.json'),
                'original_samples_manifest_sha256': sha(samples_root / 'manifest.json'),
                'original_supporting_manifest_sha256': sha(supporting_root / 'manifest.json'),
                'sample_archive_path': str(archive_path), 'sample_archive_sha256': sha(archive_path),
                'required_papers': 10, 'required_datasets': 10, 'runtime_manual_enabled': False,
                'automatic_success': False}
    with zipfile.ZipFile(archive_path) as archive:
        for paper in papers:
            pair = paper['pair_id']
            for kind in ['paper', 'dataset']:
                case = {'case_id': pair + '-' + kind, 'pair_id': pair, 'kind': kind, 'name': paper['folder']}
                out = output / 'cases' / case['case_id']
                if (out / 'preparation.json').exists():
                    prepared_case = strict_json((out / 'preparation.json').read_text(encoding='utf-8'))
                    replay_preparation(prepared_case)
                    # Previously prepared redundant native field readouts remain
                    # immutable in this campaign. New cases mark them citation-only.
                    campaign['cases'].append(prepared_case)
                    continue
                (out / 'inputs').mkdir(parents=True, exist_ok=True)
                if kind == 'paper':
                    original = papers_root / pair / 'paper.pdf'
                    if sha(original) != paper['sha256']:
                        raise ValueError('Original PDF differs: ' + pair)
                    target = out / 'inputs' / 'paper.pdf'
                    shutil.copyfile(original, target)
                    pages = extract_pages(target)
                    registry, texts = {}, {}
                    for p, page_text in enumerate(pages, 1):
                        add_text(registry, texts, page_text, sha(target), p, target.name, 'paper')
                    inputs = [{'path': str(target), 'sha256': sha(target), 'original_path': str(original),
                               'representation': 'original_pdf', 'extractor': 'pypdf.extract_text', 'pages': len(pages)}]
                else:
                    sample = samples[pair]
                    raw = archive.read(sample['output_path'])
                    if hashlib.sha256(raw).hexdigest() != sample['output_sha256']:
                        raise ValueError('Original sample differs: ' + pair)
                    sample_path = out / 'inputs' / ('original-sample' + sample['source_format'])
                    sample_path.write_bytes(raw)
                    supplied = []
                    for document in documents:
                        if document['pair_id'] != pair: continue
                        source = supporting_root / document['package_path']
                        if sha(source) != document['sha256']:
                            raise ValueError('Original supporting document differs')
                        supplied.append((source, document['source_path']))
                    registry, texts, inputs = prepare_dataset(sample, raw, supplied, out)
                    inputs.insert(0, {'path': str(sample_path), 'sha256': sha(sample_path), 'representation': 'original_sample',
                                      'archive_member': sample['output_path'], 'source_format': sample['source_format']})
                    atomic(out / 'sample-provenance.json', sample)
                validate_registry(registry)
                atomic(out / 'source-registry.json', registry)
                atomic(out / 'source-texts.json', texts)
                case.update(directory=str(out), registry_sha256=identity(registry),
                            base_units=sum(not u.get('citation_only') for u in registry.values()), input_manifest=inputs)
                atomic(out / 'preparation.json', case)
                campaign['cases'].append(case)
                atomic(output / 'preparation-progress.json', campaign)
    import pyarrow
    campaign['arrow_dependency_path'] = str(Path(pyarrow.__file__).resolve().parent.parent)
    campaign['arrow_version'] = pyarrow.__version__
    campaign['preparation_runtime_sha256'] = sha(__file__)
    atomic(output / 'campaign.json', campaign)
    return campaign


def replay_preparation(case):
    out = Path(case['directory'])
    registry = strict_json((out / 'source-registry.json').read_text(encoding='utf-8'))
    texts = strict_json((out / 'source-texts.json').read_text(encoding='utf-8'))
    validate_registry(registry)
    if identity(registry) != case['registry_sha256']:
        raise ValueError('Prepared registry identity changed')
    for source in case['input_manifest']:
        if sha(source['path']) != source['sha256']:
            raise ValueError('Prepared source byte identity changed')
        if source.get('original_path') and sha(source['original_path']) != source['sha256']:
            raise ValueError('Original source changed after preparation')
        if source['representation'] == 'original_pdf':
            if {str(i): text for i, text in enumerate(extract_pages(source['path']), 1)} != texts[source['sha256']]:
                raise ValueError('Original PDF extraction replay differs')
    if case['kind'] == 'dataset':
        sample = strict_json((out / 'sample-provenance.json').read_text(encoding='utf-8'))
        source = case['input_manifest'][0]
        names, rows, text, metadata = decode_sample(Path(source['path']).read_bytes(), source['source_format'])
        if text.encode('utf-8') != (out / 'inputs' / 'sample-readout.csv').read_bytes():
            raise ValueError('Native sample parser replay differs')
        facts = strict_json((out / 'dataset-facts.json').read_text(encoding='utf-8'))
        if len(facts) != len(names): raise ValueError('Native field count changed')
        for i, field in enumerate(facts):
            if field['name'] != names[i] or field['values_as_written'] != [row[i] for row in rows] or field['embedded_metadata'] != (metadata[i] if metadata else None):
                raise ValueError('Native field observation replay differs')
    for unit in registry.values():
        full = texts[unit['source_sha256']][str(unit['physical_page'])]
        if full[unit['start_char']:unit['end_char']] != unit['text']:
            raise ValueError('Exact original source replay differs')
    return registry
