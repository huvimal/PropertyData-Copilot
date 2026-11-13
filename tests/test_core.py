import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from urllib.error import HTTPError

from PIL import Image
from pydantic import ValidationError
from domain import Facts, local_extract, parse_price, parse_area, derived, quality, validate_ai, read_csv
from make_demo import fixtures
from evaluate import evaluate
from search import Query, propose, search, duplicates
from provider import extract, request_json, image_hash, ProviderError
from store import Store


class DomainTests(unittest.TestCase):
    def test_authored_rules_and_filter_cases(self):
        result = evaluate()['summary']
        self.assertEqual(result['exact_listing_cases'], 16)
        self.assertEqual(result['exact_query_cases'], 4)

    def test_price_basis_and_scale(self):
        self.assertEqual(parse_price('2,5 tỷ'), ('2500000000', 'total'))
        self.assertEqual(parse_price('35 triệu/m²'), ('35000000', 'per_m2'))
        self.assertEqual(parse_price('15 triệu/tháng'), ('15000000', 'per_month'))

    def test_ambiguous_separators_rejected(self):
        for value in ('2.500 triệu', '2,500 tỷ', '1.250.000.000 VND'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_price(value)

    def test_area_unit_and_positive_value(self):
        self.assertEqual(parse_area('70,5 m²'), '70.5')
        for value in ('0 m2', '70 sqft'):
            with self.assertRaises(ValueError):
                parse_area(value)

    def test_derived_total_not_monthly_rent(self):
        facts = Facts(purpose='sale', property_type='apartment', area_m2='85', price_vnd='35000000', price_basis='per_m2').model_dump()
        result = derived(facts)
        self.assertEqual(result['sale_total_vnd'], '2975000000')
        self.assertTrue(result['price_is_derived'])
        self.assertIsNone(result['monthly_rent_vnd'])

    def test_rent_without_month_basis_blocked(self):
        facts = Facts(purpose='rent', property_type='apartment', price_vnd='12000000', price_basis='total').model_dump()
        self.assertTrue(quality(facts))
        self.assertIsNone(derived(facts)['monthly_rent_vnd'])

    def test_title_price_conflict_abstains(self):
        source = next(r for r in fixtures()[0] if r['id'] == 'R013')
        facts, issues = local_extract(source)
        self.assertIsNone(facts['price_vnd'])
        self.assertTrue(issues)

    def test_missing_fields_not_zero_filled(self):
        facts, _ = local_extract({'id': 'X', 'title': 'Tin chưa đủ thông tin', 'description': 'Giao dịch: bán; Loại: căn hộ'})
        self.assertIsNone(facts['price_vnd'])
        self.assertIsNone(facts['bedrooms'])

    def test_ai_value_must_match_source_unit(self):
        source = {'id': 'X', 'title': 'Căn hộ', 'description': 'Giá: 35 triệu/m²'}
        facts, _ = validate_ai({'price_vnd': '35000000', 'price_basis': 'total',
            'evidence': {'price_vnd': '35 triệu/m²', 'price_basis': '35 triệu/m²'}}, source)
        self.assertIsNone(facts['price_basis'])

    def test_digit_in_price_does_not_prove_bedrooms(self):
        facts, _ = validate_ai({'bedrooms': 2, 'evidence': {'bedrooms': '2'}},
                               {'id': 'X', 'title': 'Tin mẫu', 'description': 'Giá: 2,5 tỷ'})
        self.assertIsNone(facts['bedrooms'])

    def test_csv_duplicate_ids_rejected(self):
        with self.assertRaises(ValueError):
            read_csv(b'id,title,description\nA,x,y\nA,x,y')


class SearchTests(unittest.TestCase):
    def indexed(self):
        return [{'id': r['id'], 'source': r, 'facts': local_extract(r)[0], 'approval': {'test': True}}
                for r in fixtures()[0]]

    def test_natural_query_preserves_strict_budget(self):
        proposal, _ = propose('Mua căn hộ An Phú TP.HCM dưới 3 tỷ, ít nhất 2 phòng ngủ, từ 65 m2', ['an phú'])
        self.assertEqual(proposal['purpose'], 'sale')
        self.assertEqual(proposal['budget_operator'], 'lt')
        self.assertEqual(proposal['max_budget_vnd'], '3000000000')
        self.assertEqual(proposal['min_bedrooms'], 2)
        self.assertEqual(proposal['min_area_m2'], '65')

    def test_unknown_budget_needs_purpose(self):
        with self.assertRaises(ValueError):
            search(self.indexed(), Query(max_budget_vnd='3000000000').model_dump())

    def test_unreviewed_rows_never_search(self):
        rows = self.indexed()
        for row in rows:
            row['approval'] = None
        self.assertEqual(search(rows, Query().model_dump()), [])

    def test_missing_fields_excluded_not_ranked_around_filter(self):
        query = Query(purpose='sale', city='TP.HCM', area_name='An Phú', property_type='apartment',
            min_bedrooms=2, min_area_m2='65', max_budget_vnd='3000000000', budget_operator='lt',
            keywords='thiếu thông tin').model_dump()
        self.assertEqual({r['id'] for r in search(self.indexed(), query)}, {'R001', 'R002'})

    def test_exact_bedroom_query_has_min_and_max(self):
        proposal, _ = propose('Mua căn hộ 2PN', [])
        self.assertEqual((proposal['min_bedrooms'], proposal['max_bedrooms']), (2, 2))

    def test_duplicate_candidates_never_remove_records(self):
        rows = self.indexed()
        matches = duplicates(rows)
        self.assertTrue(any(r['id_a'] == 'R001' and r['id_b'] == 'R002' for r in matches))
        self.assertEqual(len(rows), 16)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'test.sqlite3'
        self.store = Store(self.path)
        self.source = fixtures()[0][0]
        self.store.import_rows([self.source])
        facts, issues = local_extract(self.source)
        self.store.save_result('R001', 0, facts, issues, facts, 'test_rules')

    def approve(self):
        row = self.store.get('R001')
        self.store.approve('R001', row['revision'], 'Huni', 'Source and units checked', True)

    def test_restart_and_idempotent_import(self):
        self.approve()
        other = Store(self.path)
        self.assertEqual(other.import_rows([self.source])['unchanged'], 1)
        self.assertEqual(len(other.rows(approved_only=True)), 1)

    def test_edit_revokes_review(self):
        self.approve()
        row = self.store.get('R001')
        self.store.edit('R001', row['revision'], row['facts'])
        self.assertEqual(self.store.rows(approved_only=True), [])

    def test_stale_write_rejected(self):
        with self.assertRaises(ValueError):
            self.store.edit('R001', 0, self.store.get('R001')['facts'])

    def test_source_update_revokes_review_and_old_image(self):
        self.approve()
        self.store.import_rows([{**self.source, 'description': 'Changed source'}])
        self.assertEqual(self.store.get('R001')['state'], 'pending')
        self.assertFalse(self.store.rows(approved_only=True))

    def test_review_requires_identity_note_and_ack(self):
        row = self.store.get('R001')
        for name, note, ack in [('', 'x', True), ('Huni', '', True), ('Huni', 'x', False)]:
            with self.assertRaises(ValueError):
                self.store.approve('R001', row['revision'], name, note, ack)

    def test_export_only_reviewed_and_keeps_zero_bedrooms(self):
        self.approve()
        studio = fixtures()[0][-1]
        self.store.import_rows([studio, fixtures()[0][2]])
        facts, issues = local_extract(studio)
        self.store.save_result('R016', 0, facts, issues, facts, 'test_rules')
        self.store.approve('R016', 1, 'Huni', 'Studio has explicit zero bedrooms', True)
        with zipfile.ZipFile(io.BytesIO(self.store.export())) as archive:
            self.assertEqual(set(archive.namelist()), {'reviewed-listings.csv', 'audit.json'})
            records = json.loads(archive.read('audit.json'))['records']
            self.assertEqual([r['id'] for r in records], ['R001', 'R016'])
            import csv
            csv_rows = list(csv.DictReader(io.StringIO(archive.read('reviewed-listings.csv').decode('utf-8-sig'))))
            self.assertEqual(csv_rows[-1]['bedrooms'], '0')

    def test_provider_failure_revokes_approval(self):
        self.approve()
        row = self.store.get('R001')
        self.store.fail('R001', row['revision'], 'Groq HTTP 429')
        self.assertFalse(self.store.rows(approved_only=True))


class ProviderTests(unittest.TestCase):
    def test_fake_response_is_evidence_validated(self):
        source = fixtures()[0][0]
        facts = local_extract(source)[0]
        body = {'choices': [{'message': {'content': json.dumps(facts)}}]}
        result, _, _ = extract(source, 'fake-key', opener=lambda *a, **k: io.BytesIO(json.dumps(body).encode()))
        self.assertEqual(result['price_vnd'], '2500000000')

    def test_http_error_keeps_secrets_out(self):
        def fail(*args, **kwargs):
            raise HTTPError('https://example.test', 429, 'private-body', {}, io.BytesIO())
        with self.assertRaises(ProviderError) as result:
            request_json('text', {}, 'instruction', 'private-key', 'model', fail)
        self.assertIn('429', str(result.exception))
        self.assertNotIn('private-key', str(result.exception))
        self.assertNotIn('private-body', str(result.exception))

    def test_hash_collision_is_only_candidate_not_identity(self):
        def solid(color):
            output = io.BytesIO()
            Image.new('RGB', (20, 20), color).save(output, format='PNG')
            return image_hash(output.getvalue())
        self.assertEqual(solid('white'), solid('black'))
