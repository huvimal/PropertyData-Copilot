import argparse
import json
from pathlib import Path
from domain import read_csv, local_extract, quality, digest
from search import Query, search

ROOT = Path(__file__).resolve().parent


def evaluate():
    rows = read_csv((ROOT / 'data' / 'listings.csv').read_bytes())
    gold = json.loads((ROOT / 'data' / 'references.json').read_text(encoding='utf-8'))
    cases, indexed = [], []
    for row in rows:
        facts, issues = local_extract(row)
        differences = {k: {'actual': facts[k], 'expected': v} for k, v in gold[row['id']].items() if facts[k] != v}
        cases.append({'id': row['id'], 'exact': not differences, 'differences': differences, 'issues': issues})
        indexed.append({'id': row['id'], 'source': row, 'facts': facts,
            'approval': {'mode': 'synthetic_test_fixture_not_real_human_review'} if not quality(facts) else None})
    common = {'purpose': 'sale', 'property_type': 'apartment', 'city': 'ho_chi_minh', 'area_name': 'an phú',
              'min_bedrooms': 2, 'min_area_m2': '65', 'max_budget_vnd': '3000000000'}
    query_cases = [
        ('strict_budget', {**common, 'budget_operator': 'lt'}, ['R001', 'R002']),
        ('inclusive_budget', {**common, 'budget_operator': 'lte'}, ['R001', 'R002', 'R003']),
        ('rent_not_sale', {'purpose': 'rent', 'city': 'ho_chi_minh', 'area_name': 'an phú', 'max_budget_vnd': '20000000'}, ['R005']),
        ('derived_per_m2', {**common, 'area_name': 'bình an', 'min_area_m2': '80'}, ['R007'])]
    checked = []
    for name, query, expected in query_cases:
        actual = sorted(r['id'] for r in search(indexed, Query(**query).model_dump()))
        checked.append({'name': name, 'actual': actual, 'expected': expected, 'exact': actual == expected})
    return {'mode': 'local_rules_and_filter_fixtures', 'dataset_hash': digest({'rows': rows, 'gold': gold}),
        'scope': 'Authored synthetic source fields and query fixtures; not live AI extraction or property-market accuracy.',
        'cases': cases, 'queries': checked,
        'summary': {'listing_cases': len(cases), 'exact_listing_cases': sum(c['exact'] for c in cases),
                    'query_cases': len(checked), 'exact_query_cases': sum(c['exact'] for c in checked)}}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', default='reports/evaluation-v1.json')
    args = parser.parse_args()
    path = Path(args.output)
    if path.exists():
        parser.error('File đã có; chọn --output mới.')
    result = evaluate()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result['summary'], indent=2))
    print('Report:', path)
    summary = result['summary']
    raise SystemExit(0 if summary['listing_cases'] == summary['exact_listing_cases'] and summary['query_cases'] == summary['exact_query_cases'] else 1)
