import unittest

from schema_extract.inventory import field_role_issues, retained_name_issues


class FieldRoleTests(unittest.TestCase):
    def test_printed_dictionary_heading_cannot_become_retained_record_field(self):
        assignment = {'action': 'keep', 'name': 'Unit', 'ref': {'source_id': 'dictionary'},
                      'accounting_role': 'dictionary_heading'}
        self.assertFalse(retained_name_issues([assignment], {'dictionary': {'text': 'Variable Description Unit'}}, table=False))
        self.assertTrue(field_role_issues({'candidate': assignment}))
        self.assertFalse(field_role_issues({'candidate': {**assignment, 'action': 'exclude'}}))

    def test_declared_native_field_and_feature_family_roles_allow_retention(self):
        for action in ['keep', 'merge']:
            for role in ['dataset_field', 'feature_family']:
                self.assertFalse(field_role_issues({'candidate': {'action': action, 'accounting_role': role}}))

    def test_unresolved_or_reporting_role_does_not_establish_a_data_field(self):
        for role in ['unresolved', 'dataset_summary_property', 'analysis_result', 'method_example']:
            self.assertTrue(field_role_issues({'candidate': {'action': 'keep', 'accounting_role': role}}))


if __name__ == '__main__':
    unittest.main()
