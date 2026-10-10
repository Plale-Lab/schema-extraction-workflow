"""Lossless native representations for bounded names-first field batches."""
import copy
import jsonschema
from pathlib import Path

from .compiler import ROOT, canonical, identity, strict_json, settings
from .semantic import bind_contract, claim_issues, review_view, review_issues
from .operations import ItemFailure
from .inventory import obj, arr, STRING, BOOLEAN, split_work
from .extraction import local_sources
from .native import sha
from .semantic import source_spans


REPRESENTATION = 'compact-object-batch-v2'


def field_attributes(table=False):
    return strict_json((ROOT/'contracts'/'evidence-contract.json').read_text(encoding='utf-8'))['table_attributes' if table else 'field_attributes']


def expand_decision(value, table=False):
    """Expand only unknown attributes explicitly returned by the native model."""
    attributes = field_attributes(table)
    if 'attribute_decisions' in value:
        decisions = value['attribute_decisions']
        if set(decisions) != set(attributes) or decisions['name'] is None:
            raise ItemFailure('Every attribute needs its native keyed decision; name must be known.')
        converted = {key:copy.deepcopy(child) for key,child in value.items() if key!='attribute_decisions'}
        converted['claims'] = [{'attribute':attribute,**claim} for attribute in attributes
                               for claim in decisions[attribute] or []]
        converted['unknown_attributes'] = [attribute for attribute in attributes if decisions[attribute] is None]
        value = converted
    known = copy.deepcopy(value['claims'])
    unresolved = value['unknown_attributes']
    if len(unresolved) != len(set(unresolved)) or 'name' in unresolved:
        raise ItemFailure('Unknown attributes must be unique and cannot include name.')
    named = {claim['attribute'] for claim in known}
    if any(claim['status'] == 'unknown' for claim in known) or named & set(unresolved):
        raise ItemFailure('Known and unresolved attribute decisions must be disjoint.')
    if named | set(unresolved) != set(attributes):
        raise ItemFailure('Every original field attribute needs a native known or explicit unknown decision.')
    unknown = [{'attribute': attribute, 'value': False if attribute in {'required','unique'} else None,
                'status': 'unknown', 'refs': [], 'reason': value['unknown_reason']}
               for attribute in unresolved]
    claims, seen = [], set()
    for attribute in attributes:
        for claim in known+unknown:
            if claim['attribute'] != attribute:continue
            signature=identity({key:claim[key] for key in ['attribute','value','status','refs']})
            if signature not in seen:claims.append(claim);seen.add(signature)
    return {'keep': value['keep'], 'name': value['name'], 'reason': value['reason'], 'claims': claims}


def duplicate_claim_variants(value):
    seen, duplicates = set(), []
    for attribute, records in value['attribute_decisions'].items():
        for index, record in enumerate(records or []):
            signature=identity({'attribute':attribute,**{key:record[key] for key in ['value','status','refs']}})
            if signature in seen:duplicates.append({'attribute':attribute,'native_record_index':index,'record':copy.deepcopy(record)})
            seen.add(signature)
    return duplicates


def expand_review(decision, value):
    """A native object verdict covers explicit unknowns without source-absence claims."""
    known = {key for key, claim in review_view(decision).items() if claim['status'] != 'unknown'}
    if set(value['claims']) != known:
        raise ItemFailure('The independent batch audit must cover every exact known assertion.')
    claims = {}
    for key, verdict in value['claims'].items():
        if 'attribute' in verdict and verdict['attribute'] != review_view(decision)[key]['attribute']:
            raise ItemFailure('Independent audit verdict attribute does not match its exact keyed assertion.')
        claims[key] = {name:copy.deepcopy(verdict[name]) for name in ['supported','problem']}
    for key, claim in review_view(decision).items():
        if claim['status'] == 'unknown':
            claims[key] = {'supported': value['unknown_decisions_permitted'],
                           'problem': 'Native audit of explicit optional deferral; no source absence asserted.'}
    return {'claims': claims, 'description_available': value['description_available'],
            'availability_reason': value['availability_reason']}


def decision_contract(handles, local, table_handles=()):
    contract = obj({'items':obj({handle:{'$ref':'#/$defs/'+('table' if handle in table_handles else 'field')} for handle in handles})})
    definitions = {'ref':obj({'source_id':{'enum':list(local)}})}
    for table in sorted({handle in table_handles for handle in handles}):
        kind='table' if table else 'field'
        stage=bind_contract(table,local)
        branches=stage['$defs']['claim']['allOf'][1]['oneOf']
        properties={}
        for attribute in field_attributes(table):
            shape=next(branch['properties']['value'] for branch in branches if branch['properties'].get('attribute',{}).get('const')==attribute and 'status' in branch['properties'] and branch['properties']['status'].get('enum'))
            claim=obj({'value':copy.deepcopy(shape),'status':{'enum':['declared','observed','inferred']},
                       'refs':{'type':'array','minItems':1,'maxItems':4,'items':{'$ref':'#/$defs/ref'}},'reason':STRING})
            key=kind+'-'+attribute
            definitions[key]=claim
            records={'type':'array','minItems':1,'maxItems':1 if attribute=='name' else 4,'items':{'$ref':'#/$defs/'+key}}
            properties[attribute]=records if attribute=='name' else {'anyOf':[{'type':'null'},records]}
        definitions[kind]=obj({'keep':BOOLEAN,'name':STRING,'reason':STRING,
                               'attribute_decisions':obj(properties),'unknown_reason':STRING})
    contract['$defs']=definitions
    return contract


def defer_optional_contract(contract, handles, table_handles=()):
    """Only unresolved work is constrained to native explicit optional nulls."""
    bound = copy.deepcopy(contract)
    for handle in handles:
        kind = 'table' if handle in table_handles else 'field'
        slot = copy.deepcopy(bound['$defs'][kind])
        for attribute in slot['properties']['attribute_decisions']['properties']:
            if attribute != 'name':
                slot['properties']['attribute_decisions']['properties'][attribute] = {'type': 'null'}
        bound['properties']['items']['properties'][handle] = slot
    return bound


def compact_sources(ids, name, registry, table=False):
    """An exact name-bearing subdivision; the complete original remains deferred."""
    result = []
    for sid in ids:
        unit = registry[sid]
        text = unit['text']
        if len(text) <= 1500:
            result.append(sid)
            if len(result) == 2: break
            continue
        offset = text.find(canonical(name))
        if offset < 0: offset = text.find(name)
        if offset < 0:
            if not table: continue
            offset=0
        a = max(0, offset-350); b = min(len(text), offset+len(canonical(name))+650)
        child = 'focus-'+identity([sid,a,b])[:24]
        registry[child] = {**unit,'source_id':child,'text':text[a:b],
                           'start_char':unit['start_char']+a,'end_char':unit['start_char']+b,
                           'base_source_id':sid,'citation_only':True,
                           'optional_source_subdivision':{'original_source_id':sid,'start_offset':a,'end_offset':b}}
        result.append(child)
        break
    if not result: raise ItemFailure('No original name-bearing source available for compact field work: '+name)
    return result


def verified_partials(operations, handle, name, registry):
    """Retain current-campaign table partials after a failed ordinary reconciliation."""
    claims, sources = {}, []
    for path in operations.output.glob('*.json'):
        saved = strict_json(path.read_text(encoding='utf-8'))
        if saved.get('errors') != [] or 'extraction_receipt' not in saved or 'decision' not in saved: continue
        if saved['decision']['name'] != name: continue
        folder = Path(saved['extraction_receipt']['folder'])
        receipt = strict_json((folder/'receipt.json').read_text(encoding='utf-8'))
        if receipt.get('status')!='received' or sha(folder/'response.raw') != receipt.get('response_sha256'):
            raise ItemFailure('Original verified table partial response integrity differs.')
        wire = strict_json((folder/'request.json').read_text(encoding='utf-8'))
        payload = strict_json(wire['messages'][1]['content'])
        if payload.get('handle') != handle: continue
        response = strict_json((folder/'response.raw').read_bytes().decode('utf-8'))
        native = strict_json(response['choices'][0]['message']['content'])
        if response['choices'][0]['finish_reason']!='stop' or native.get('table') != saved['decision']:
            raise ItemFailure('Verified table partial does not replay from its original native response.')
        units = payload['source_units']
        for unit in units:
            value={k:v for k,v in unit.items() if k!='exact_citation_spans'}
            sid=value['source_id']
            if sid in registry and registry[sid] != value: raise ItemFailure('Current-round partial source binding differs.')
            registry[sid]=value
        source_spans([u['source_id'] for u in units],registry)
        for claim in saved['decision']['claims']:
            if claim['status']=='unknown':continue
            key='partial-'+identity(claim)[:20]
            claims[key]=copy.deepcopy(claim)
            sources.extend(ref['source_id'] for ref in claim['refs'])
    return claims,list(dict.fromkeys(sources))


def extract_fields(case, operations, registry, jobs, progress=None):
    """One native extraction and independent audit per bounded complete field batch."""
    results, traces = {}, {}
    by_handle = {job['handle']:job for job in jobs}
    handles = list(by_handle)
    maximum = settings().get('max_field_batch_items', 6)
    def work(batch):
        selected, scopes, previous = [], {}, {}
        for handle in batch:
            job = by_handle[handle]
            primary = compact_sources(job['primary'],job['name'],registry,table=job.get('table',False))
            witnesses = job.get('fact',{}).get('witness_source_ids',[]) if job.get('fact') else []
            contextual = job['contextual'][:1]
            # A large applicable passage is an explicit bounded subdivision.
            from .extraction import linked_source_ids
            contextual = linked_source_ids(contextual,registry,target=1000)[:1]
            own = list(dict.fromkeys(primary+witnesses+contextual))
            if job.get('table'):
                older, refs=verified_partials(operations,handle,job['name'],registry)
                previous[handle]=older
                own=list(dict.fromkeys(own+refs))
            selected.extend(own)
            scopes[handle] = {'consumed_source_ids':own,
                             'deferred_optional_source_ids':[sid for sid in job['primary']+job['contextual'] if sid not in own],
                             'complete_source_consumption':False}
        sources, local = local_sources(list(dict.fromkeys(selected)),registry)
        objects = {handle:{'name':by_handle[handle]['name'],
                           'object_kind':'table' if by_handle[handle].get('table') else 'field',
                           'required_attribute_decisions':field_attributes(by_handle[handle].get('table',False)),
                           'name_origin':(by_handle[handle].get('fact') or {}).get('name_origin'),
                           'source_scope':scopes[handle],
                           'negative_constraint_witnesses':(by_handle[handle].get('fact') or {}).get('negative_constraint_witnesses')}
                   for handle in batch}
        payload = {'kind':case['kind'],'job_id':case['case_id'],'source_units':sources,
                   'original_objects':objects,'current_round_verified_partial_claims':previous,
                   'source_scope':'Exact field-focused sources and explicitly deferred optional context. All original inputs remain preserved; this pass does not assert complete optional-information recall.'}
        contract = decision_contract(batch,local,[h for h in batch if by_handle[h].get('table')])
        deferred = [h for h in batch if by_handle[h].get('defer_optional')]
        if deferred:
            contract = defer_optional_contract(contract, deferred)
            payload['names_only_optional_deferral'] = {
                'handles': deferred,
                'instruction': 'The user prioritizes names and permits more unknown attributes. Return explicit null for these unresolved optional attributes. Original sources remain preserved; do not claim their information is absent. Keep every exact source-bound name.'}
        def validate(response):
            errors = []
            for handle in batch:
                table=by_handle[handle].get('table',False)
                decision = expand_decision(response['items'][handle],table=table)
                jsonschema.validate({'table' if table else 'field':decision},bind_contract(table,local))
                errors += [handle+': '+e for e in claim_issues(decision,local,table=table,expected_name=by_handle[handle]['name'],
                            dataset_fact=by_handle[handle].get('fact'),allow_optional_unknown=True)]
            return errors
        last_errors = []
        for attempt in range(settings()['max_repairs_per_item']+1):
            current = copy.deepcopy(payload)
            if attempt: current['independent_review_repair'] = {'errors':last_errors,
                'instruction':'Retain every source-bound name and verified known assertion. Resolve unsupported optional assertions through original evidence or explicitly mark their attributes unknown. Unknown is permitted without a source-absence assertion.'}
            raw, receipt = operations.call('field-batch-'+identity(batch)[:16]+'-claims-'+str(attempt),
                'Return every named table and field using the compact complete contract. Return all fixed attribute_decisions keys. Each known key holds its source-supported claim records; each unresolved optional key is explicitly null with a brief truthful unknown_reason. '
                'The program expands only explicit native null decisions into ordinary unknown template decisions; omitted keys are rejected. Preserve distinct conflicting source claims within the same key. '
                'Keep names exact and cite their own original text. Retain reliable supported optional values; do not exhaust deferred optional context before completing this batch. '
                'Use brief reasons. Samples do not establish legal enums, population requiredness or uniqueness.',current,contract,validate)
            decisions = {handle:expand_decision(raw['items'][handle],table=by_handle[handle].get('table',False)) for handle in batch}
            reviews = {handle:obj({'claims':obj({key:obj({'attribute':{'const':claim['attribute']},'supported':BOOLEAN,'problem':STRING}) for key, claim in review_view(decisions[handle]).items() if claim['status']!='unknown'}),
                'unknown_decisions_permitted':BOOLEAN,'description_available':BOOLEAN,'availability_reason':STRING}) for handle in batch}
            audit_payload = {'kind':case['kind'],'job_id':case['case_id'],'source_units':sources,
                             'original_objects':objects,'exact_current_claims':{h:review_view(d) for h,d in decisions.items()}}
            if deferred:
                audit_payload['names_only_optional_deferral'] = payload['names_only_optional_deferral']
            audit, audit_receipt = operations.call('field-batch-'+identity(batch)[:16]+'-review-'+str(attempt),
                'Independently audit each exact known assertion against its own cited original source. Check each field name and every fact in each known description. '
                'Return a verdict for every known claim. Separately check that unknown decisions are explicit optional deferrals, without treating them as claims that information is absent. '
                'Echo the exact attribute of each keyed assertion. A name verdict must judge that name and its own source; unavailable optional description is not a name defect. '
                'Available optional description detail is diagnostic and may remain unresolved. Never approve an unsupported known claim merely because its status is inferred. No extraction rationale is supplied.',
                audit_payload,obj({'items':obj(reviews)}))
            last_errors = []
            for handle, decision in decisions.items():
                normalized = expand_review(decision,audit['items'][handle])
                last_errors += [handle+': '+e for e in review_issues(decision,normalized,allow_optional_unknown=True)]
            if not last_errors:
                registry.update(local)
                for handle, decision in decisions.items():
                    results[handle] = decision
                    traces[handle] = {**scopes[handle],'completion_profile':'names_first','partials':[],'reconciliation':[],
                        'final':{'extraction_receipt':receipt,'review_receipt':audit_receipt,
                                 'decision_sha256':identity(decision),'independent_review_passed':True,
                                 'source_guards_passed':True,'claim_accounting':[],
                                 'previous_verified_partial_claims':previous.get(handle,{}),
                                 'duplicate_native_claim_variants':duplicate_claim_variants(raw['items'][handle]),
                                 'optional_native_review_diagnostics':{key:audit['items'][handle][key] for key in ['unknown_decisions_permitted','description_available','availability_reason']},
                                 'unresolved_partial_claims':{key:claim for key,claim in previous.get(handle,{}).items() if any(c['attribute']==claim['attribute'] and c['status']=='unknown' for c in decision['claims'])},
                                 'native_projection':{'representation':REPRESENTATION,'handle':handle,'table':by_handle[handle].get('table',False)},'automatic_success':False}}
                if progress: progress(results,traces)
                return
        raise ItemFailure('Bounded independent compact-field repairs exhausted: '+canonical(last_errors))
    for offset in range(0,len(handles),maximum):
        packet = handles[offset:offset+maximum]
        segments = []
        for handle in packet:
            if not segments or bool(by_handle[segments[-1][0]].get('defer_optional')) != bool(by_handle[handle].get('defer_optional')):
                segments.append([])
            segments[-1].append(handle)
        for segment in segments:
            split_work(segment,work)
    return results,traces
