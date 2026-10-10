import copy
import unittest
import jsonschema
from tempfile import TemporaryDirectory

from schema_extract.compact import expand_decision, expand_review, decision_contract, compact_sources, field_attributes, duplicate_claim_variants, defer_optional_contract
from schema_extract.semantic import bind_contract, claim_issues, review_issues, review_view
from schema_extract.operations import ItemFailure, bind_object_names, Operations
from schema_extract.acceptance import assertion_targets
from schema_extract.inventory import applicability_contract, membership_aliases, expand_memberships
from schema_extract.native import IncompleteGeneration
from schema_extract.compiler import RequestTooLarge, strict_json


class CompactTests(unittest.TestCase):
    def setUp(self):
        self.source = {'s':{'source_id':'s','source_sha256':'0'*64,'physical_page':1,
                            'start_char':0,'end_char':3,'text':'age','kind':'header'}}
        self.raw = {'keep':True,'name':'age','reason':'Original field retained.',
                    'attribute_decisions':{a:None for a in field_attributes()},
                    'unknown_reason':'Unresolved optional decisions in the bounded pass.'}
        self.raw['attribute_decisions']['name']=[{'value':'age','status':'observed','refs':[{'source_id':'s'}],'reason':'Original header.'}]

    def test_expansion_retains_native_values_and_explicit_unknowns(self):
        original = copy.deepcopy(self.raw)
        jsonschema.validate({'items':{'f':self.raw}},decision_contract(['f'],self.source))
        result = expand_decision(self.raw)
        jsonschema.validate({'field':result},bind_contract(False,self.source))
        self.assertEqual(self.raw,original)
        self.assertEqual(result['claims'][0],{'attribute':'name',**self.raw['attribute_decisions']['name'][0]})
        self.assertFalse(claim_issues(result,self.source,expected_name='age',allow_optional_unknown=True))
        self.assertEqual([c['attribute'] for c in result['claims']],field_attributes())

    def test_optional_deferral_only_constrains_unresolved_slots(self):
        base = decision_contract(['old', 'new'], self.source)
        snapshot = copy.deepcopy(base)
        bound = defer_optional_contract(base, ['new'])
        old = copy.deepcopy(self.raw)
        old['attribute_decisions']['value.datatype'] = [{'value': 'integer', 'status': 'observed',
            'refs': [{'source_id': 's'}], 'reason': 'Offline contract fixture.'}]
        jsonschema.validate({'items': {'old': old, 'new': self.raw}}, bound)
        self.assertEqual(base, snapshot)
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate({'items': {'old': old, 'new': old}}, bound)
        missing_name = copy.deepcopy(self.raw)
        missing_name['attribute_decisions']['name'] = None
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate({'items': {'old': old, 'new': missing_name}}, bound)

    def test_missing_extra_and_unknown_name_decisions_fail(self):
        variants=[]
        value=copy.deepcopy(self.raw);value['attribute_decisions'].pop('description');variants.append(value)
        value=copy.deepcopy(self.raw);value['attribute_decisions']['extra']=None;variants.append(value)
        value=copy.deepcopy(self.raw);value['attribute_decisions']['name']=None;variants.append(value)
        for value in variants:
            with self.assertRaises(ItemFailure): expand_decision(value)

    def test_table_projection_requires_all_five_original_attribute_keys(self):
        value=copy.deepcopy(self.raw)
        value['attribute_decisions']={a:None for a in field_attributes(True)}
        value['attribute_decisions']['name']=self.raw['attribute_decisions']['name']
        jsonschema.validate({'items':{'table':value}},decision_contract(['table'],self.source,['table']))
        decision=expand_decision(value,table=True)
        jsonschema.validate({'table':decision},bind_contract(True,self.source))
        self.assertEqual([c['attribute'] for c in decision['claims']],field_attributes(True))

    def test_source_enum_is_shared_instead_of_repeated_for_every_attribute(self):
        source_ids={str(i):{} for i in range(300)}
        contract=decision_contract(['f'],source_ids)
        self.assertEqual(contract['$defs']['ref']['properties']['source_id']['enum'],list(source_ids))
        self.assertNotIn('table',contract['$defs'])
        self.assertEqual(contract['$defs']['field-name']['properties']['refs']['items'],{'$ref':'#/$defs/ref'})

    def test_duplicate_projection_preserves_native_variants_and_distinct_support(self):
        value=copy.deepcopy(self.raw)
        original=value['attribute_decisions']['name'][0]
        same={**copy.deepcopy(original),'reason':'Another native rationale for the same assertion.'}
        different={**copy.deepcopy(original),'refs':[{'source_id':'other'}]}
        value['attribute_decisions']['name']=[original,same,different]
        snapshot=copy.deepcopy(value)
        result=expand_decision(value)
        names=[claim for claim in result['claims'] if claim['attribute']=='name']
        self.assertEqual(len(names),2)
        self.assertEqual(duplicate_claim_variants(value)[0]['record'],same)
        self.assertEqual(value,snapshot)

    def test_native_review_cannot_omit_or_approve_wrong_name(self):
        decision = expand_decision(self.raw)
        audit = {'claims':{'0':{'supported':False,'problem':'Wrong source name.'}},
                 'unknown_decisions_permitted':True,'description_available':True,'availability_reason':'Optional description exists.'}
        self.assertTrue(review_issues(decision,expand_review(decision,audit),allow_optional_unknown=True))
        audit['claims']={}
        with self.assertRaises(ItemFailure): expand_review(decision,audit)
        audit['claims']={'0':{'supported':True,'problem':''}}
        audit['unknown_decisions_permitted']=False
        self.assertFalse(review_issues(decision,expand_review(decision,audit),allow_optional_unknown=True))

    def test_a_cited_dataset_paper_cannot_become_a_metadata_standard(self):
        quote='The fictional records are described in the paper [Synthetic Records Study](https://example.org/study).'
        registry={'s':{**self.source['s'],'text':quote,'end_char':len(quote)}}
        value=copy.deepcopy(self.raw)
        value['attribute_decisions']['conformsTo']=[{'value':'https://example.org/study','status':'declared','refs':[{'source_id':'s'}],'reason':'Associated paper.'}]
        errors=claim_issues(expand_decision(value),registry,allow_optional_unknown=True)
        self.assertTrue(any(e.startswith('conformsTo:') for e in errors))

    def test_unit_namespace_and_target_role_need_original_information(self):
        value=copy.deepcopy(self.raw)
        value['attribute_decisions']['value.unit.system']=[{'value':'http://unitsofmeasure.org','status':'inferred','refs':[{'source_id':'s'}],'reason':'Conventional namespace.'}]
        value['attribute_decisions']['role']=[{'value':'target','status':'inferred','refs':[{'source_id':'s'}],'reason':'A measured variable.'}]
        errors=claim_issues(expand_decision(value),self.source,allow_optional_unknown=True)
        self.assertTrue(any(e.startswith('value.unit.system:') for e in errors))
        self.assertTrue(any(e.startswith('role:') for e in errors))

    def test_native_contract_requires_original_name_instead_of_internal_handle(self):
        base=decision_contract(['f0008'],self.source)
        bound=bind_object_names(base,{'f0008':{'name':'age'}})
        jsonschema.validate({'items':{'f0008':self.raw}},bound)
        for location in ['name','claim']:
            wrong=copy.deepcopy(self.raw)
            if location=='name':wrong['name']='f0008'
            else:wrong['attribute_decisions']['name'][0]['value']='f0008'
            with self.assertRaises(jsonschema.ValidationError):jsonschema.validate({'items':{'f0008':wrong}},bound)
        self.assertEqual(base['properties']['items']['properties']['f0008'],{'$ref':'#/$defs/field'})

    def test_optional_unknown_contract_stays_constrained_across_new_guard_errors(self):
        original=copy.deepcopy(self.raw)
        calls=[]
        class Native:
            def complete(_, prepared):
                slot=prepared['response_contract']['properties']['items']['properties']['f']
                attrs=slot['properties']['attribute_decisions']['properties']
                self.assertEqual(attrs['description'],{'type':'null'})
                value=copy.deepcopy(original)
                if not calls:
                    value['attribute_decisions']['conformsTo']=[{'value':'https://unsupported.example','status':'inferred','refs':[{'source_id':'s'}],'reason':'Unsupported.'}]
                else:self.assertEqual(attrs['conformsTo'],{'type':'null'})
                calls.append(prepared)
                return {'items':{'f':value}},{'folder':'offline-fixture'}
        payload={'original_objects':{'f':{'name':'age','required_attribute_decisions':field_attributes()}},
                 'independent_review_repair':{'errors':['f: description: unsupported known description']}}
        def validate(value):
            return ['f: conformsTo: URI absent from original source'] if value['items']['f']['attribute_decisions']['conformsTo'] is not None else []
        with TemporaryDirectory() as folder:
            value,_=Operations(Native(),folder,'dataset').call('sticky-unknown','Fixture only.',payload,decision_contract(['f'],self.source),validate)
        self.assertEqual(len(calls),2)
        self.assertIsNone(value['items']['f']['attribute_decisions']['description'])
        self.assertIsNone(value['items']['f']['attribute_decisions']['conformsTo'])

    def test_audit_echo_cannot_bind_a_description_verdict_to_a_name(self):
        decision=expand_decision(self.raw)
        audit={'claims':{'0':{'attribute':'description','supported':True,'problem':''}},
               'unknown_decisions_permitted':True,'description_available':False,'availability_reason':''}
        with self.assertRaises(ItemFailure):expand_review(decision,audit)
        audit['claims']['0']['attribute']='name'
        audit['claims']['0']['supported']=False
        self.assertTrue(review_issues(decision,expand_review(decision,audit),allow_optional_unknown=True))

    def test_source_applicability_requires_consistent_disposition_and_membership(self):
        contract=applicability_contract()
        contract['$defs']={'table_handle':{'enum':['t']},'field_handle':{'enum':['f']}}
        value={'kind':'methods','table_handles':[],'field_handles':[],'all_fields_in_tables':[],
               'reason':'Original source does not describe the supplied file.'}
        jsonschema.validate(value,contract)
        self.assertEqual(expand_memberships({'sources':{'s':value}},{'t':'table','f':'field'})[0]['disposition'],'excluded')
        value['all_fields_in_tables']=['t']
        jsonschema.validate(value,contract)
        value['field_handles']=['f']
        jsonschema.validate(value,contract)
        self.assertEqual(expand_memberships({'sources':{'s':value}},{'t':'table','f':'field'})[0]['disposition'],'used')
        value['disposition']='used'
        with self.assertRaises(jsonschema.ValidationError):jsonschema.validate(value,contract)

    def test_short_membership_handles_expand_without_changing_names_or_membership(self):
        tables=[{'handle':'view-long-identity','name':'Original Table','fields':[{'handle':'candidate-long-identity','name':'age'}]}]
        before=copy.deepcopy(tables)
        aliases=membership_aliases(tables)
        value={'sources':{'s':{'kind':'methods','table_handles':['t0'],'field_handles':['f0'],
                             'all_fields_in_tables':[],'disposition':'used','reason':'Original statement.'}}}
        snapshot=copy.deepcopy(value)
        expanded=expand_memberships(value,aliases)
        self.assertEqual(expanded[0]['table_handles'],['view-long-identity'])
        self.assertEqual(expanded[0]['field_handles'],['candidate-long-identity'])
        self.assertEqual(value,snapshot)
        self.assertEqual(tables,before)

    def test_membership_repair_finds_new_name_source_and_keeps_disposition_open(self):
        calls = []
        class Native:
            def complete(_, prepared):
                calls.append(prepared)
                return {'assignments': {'c': {'action': 'keep', 'group_id': 'c',
                        'new_name': 'age', 'ref': {'source_id': 'bad' if len(calls)==1 else 'good'},
                        'reason': 'Offline fixture.'}}}, {}
        payload = {'source_units': [{'source_id': 'bad', 'text': 'An explanatory caption.'},
                                    {'source_id': 'good', 'text': 'age'}]}
        def validate(value):
            return ['Wrong own source.'] if value['assignments']['c']['ref']['source_id']=='bad' else []
        with TemporaryDirectory() as folder:
            Operations(Native(), folder, 'paper').call('field-membership-offline-decision',
                'Offline candidate disposition.', payload, {'type': 'object'}, validate)
        self.assertEqual(len(calls), 2)
        repair = strict_json(calls[1]['messages'][1]['content'])['same_contract_repair']
        self.assertEqual(repair['name_reference_choices'], {'age': ['good']})
        self.assertIn('Exclude unsupported or explanatory candidates', repair['instruction'])
        self.assertIn('Preserve established native group identities', repair['instruction'])

    def test_independent_membership_review_excludes_rationale_from_every_candidate_view(self):
        calls = []
        candidate = {'assignments': {'c': {'action': 'keep', 'new_name': 'age', 'reason': 'Generated rationale.'}}}
        class Native:
            def complete(_, prepared):
                calls.append(prepared)
                if len(calls)==1:
                    return copy.deepcopy(candidate), {}
                return {'verdicts': {'c': {'decision_correct': True, 'problem': ''}},
                        'complete': True, 'problem': ''}, {}
        payload = {'source_units': [{'source_id': 's', 'text': 'age: the original reason text is evidence.',
                                    'raw_value': {'reason': 'An original dataset value.'}}],
                   'current_candidates': [{'name': 'age', 'reason': 'Generated discovery rationale.'}],
                   'existing_groups': {'g': {'name': 'age', 'reason': 'Generated grouping rationale.'}}}
        with TemporaryDirectory() as folder:
            _, trace = Operations(Native(), folder, 'paper').checked('field-membership-offline',
                'Offline review fixture.', payload, {'type': 'object'}, ['c'],
                audit_context=lambda _: {'exact_projected_assignments': [{'name': 'age', 'reason': 'Generated projection rationale.'}]})
        audit = strict_json(calls[1]['messages'][1]['content'])
        self.assertTrue(audit['rationale_excluded_from_review'])
        self.assertTrue(trace['rationale_excluded_from_review'])
        self.assertEqual(audit['source_units'], payload['source_units'])
        self.assertNotIn('reason', audit['current_candidates'][0])
        self.assertNotIn('reason', audit['existing_groups']['g'])
        self.assertNotIn('reason', audit['exact_projected_assignments'][0])
        self.assertNotIn('reason', audit['exact_current_candidate']['assignments']['c'])
        self.assertIn('reason', candidate['assignments']['c'])

    def test_repeated_received_output_limits_allow_bounded_subdivision(self):
        calls=[]
        class Native:
            def complete(_,prepared):
                calls.append(prepared)
                raise IncompleteGeneration('Received terminal length: offline fixture.')
        with TemporaryDirectory() as folder:
            with self.assertRaises(RequestTooLarge):
                Operations(Native(),folder,'dataset').call('terminal-length-fixture','Fixture only.',{},decision_contract(['f'],self.source))
        self.assertEqual(len(calls),2)

    def test_focus_is_an_exact_subdivision_with_original_preserved(self):
        text='a'*2100+' age '+'z'*2100
        registry={'s':{**self.source['s'],'text':text,'end_char':len(text)}}
        before=copy.deepcopy(registry['s'])
        selected=compact_sources(['s'],'age',registry)
        focus=registry[selected[0]]
        self.assertEqual(registry['s'],before)
        self.assertEqual(focus['text'],text[focus['start_char']:focus['end_char']])
        self.assertTrue(focus['citation_only'])
        self.assertEqual(focus['optional_source_subdivision']['original_source_id'],'s')

    def test_final_source_audit_only_repeats_assertions_linked_to_its_packet(self):
        registry=copy.deepcopy(self.source)
        registry['other']={**registry['s'],'source_id':'other','physical_page':2}
        registry['quote']={**registry['s'],'source_id':'quote','start_char':1,'end_char':3,'text':'ge'}
        decisions={'f':expand_decision(self.raw),'g':expand_decision(self.raw)}
        decisions['g']['claims'][0]['refs']=[{'source_id':'other'}]
        self.assertEqual(assertion_targets(['quote'],['f','g'],decisions,registry),['f'])
        self.assertEqual(assertion_targets(['other'],['f','g'],decisions,registry),['g'])


if __name__=='__main__':unittest.main()
