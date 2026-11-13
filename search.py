import math
import re
from collections import Counter
from decimal import Decimal
from difflib import SequenceMatcher
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from domain import decimal_string, norm, plain, parse_price, PRICE_PATTERN, CITIES, TYPES, derived


class Query(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    purpose: Literal['sale', 'rent'] | None = None
    property_type: Literal['apartment', 'house', 'land'] | None = None
    city: str | None = Field(default=None, max_length=100)
    area_name: str | None = Field(default=None, max_length=100)
    min_bedrooms: int | None = Field(default=None, ge=0, le=30)
    max_bedrooms: int | None = Field(default=None, ge=0, le=30)
    min_area_m2: str | None = None
    area_operator: Literal['gte', 'gt'] = 'gte'
    max_budget_vnd: str | None = None
    budget_operator: Literal['lte', 'lt'] = 'lte'
    keywords: str = Field(default='', max_length=500)

    @field_validator('city', 'area_name')
    @classmethod
    def locality(cls, value, info):
        if value is None:
            return None
        value = norm(value)
        if not value:
            return None
        return CITIES.get(value, value) if info.field_name == 'city' else value

    @field_validator('min_area_m2', 'max_budget_vnd', mode='before')
    @classmethod
    def numeric(cls, value):
        return decimal_string(value)


def propose(text, known_areas):
    if len(text) > 1000:
        raise ValueError('Câu tìm kiếm tối đa 1000 ký tự.')
    p, n = plain(text), norm(text)
    values, warnings = {'keywords': text[:500]}, []
    sale = bool(re.search(r'\b(mua|ban)\b', p))
    rent = bool(re.search(r'\bthue\b', p))
    if sale != rent:
        values['purpose'] = 'sale' if sale else 'rent'
    elif sale:
        warnings.append('Câu hỏi chứa cả mua/bán và thuê; hãy chọn purpose.')
    for field, mapping in [('property_type', TYPES), ('city', CITIES)]:
        found = {canonical for alias, canonical in mapping.items() if re.search(r'(?<!\w)' + re.escape(plain(alias)) + r'(?!\w)', p)}
        if len(found) == 1:
            values[field] = next(iter(found))
        elif found:
            warnings.append(field + ': có nhiều giá trị; cần xác nhận.')
    locations = {norm(a) for a in known_areas if a and re.search(r'(?<!\w)' + re.escape(plain(a)) + r'(?!\w)', p)}
    if len(locations) == 1:
        values['area_name'] = next(iter(locations))
    elif locations:
        warnings.append('Có nhiều khu vực trong câu hỏi.')
    bed = re.search(r'(it nhat|tu|toi da|khong qua)?\s*(\d{1,2})\s*(?:phong ngu|pn)\b', p)
    if bed:
        count = int(bed[2])
        if bed[1] in ('it nhat', 'tu'):
            values['min_bedrooms'] = count
        elif bed[1] in ('toi da', 'khong qua'):
            values['max_bedrooms'] = count
        else:
            values.update(min_bedrooms=count, max_bedrooms=count)
    area = re.search(r'(tu|it nhat|tren)\s*(\d+(?:[.,]\d{1,2})?)\s*m(?:2|²)(?!\w)', p)
    if area:
        values.update(min_area_m2=area[2].replace(',', '.'), area_operator='gt' if area[1] == 'tren' else 'gte')
    prices = list(PRICE_PATTERN.finditer(n))
    if len(prices) == 1:
        before = plain(n[:prices[0].start()])
        qualifier = re.search(r'(duoi|toi da|khong qua)\s*$', before)
        if qualifier:
            try:
                price, basis = parse_price(prices[0].group())
                if basis == 'per_m2':
                    warnings.append('Chưa hỗ trợ ngân sách theo m² trong câu tìm kiếm.')
                else:
                    values.update(max_budget_vnd=price, budget_operator='lt' if qualifier[1] == 'duoi' else 'lte')
            except ValueError:
                warnings.append('Ngân sách mơ hồ; cần nhập lại bằng VND.')
        else:
            warnings.append('Có giá nhưng chưa rõ giới hạn ngân sách; cần xác nhận.')
    elif prices:
        warnings.append('Có nhiều mức giá; không tự chọn ngân sách.')
    return Query(**values).model_dump(), warnings


def tokens(text):
    p = plain(text)
    for alias, canonical in [('can ho', 'apartment'), ('chung cu', 'apartment'), ('nha pho', 'house'), ('nha rieng', 'house')]:
        p = p.replace(alias, canonical)
    return re.findall(r'[a-z0-9]+', p)


def rank(documents, text):
    counts = [Counter(tokens(value)) for value in documents]
    query = Counter(tokens(text))
    vocab = set().union(*(set(count) for count in counts), set(query))
    idf = {word: math.log((1 + len(counts)) / (1 + sum(word in c for c in counts))) + 1 for word in vocab}
    def vector(count):
        vec = {w: (1 + math.log(c)) * idf[w] for w, c in count.items()}
        length = math.sqrt(sum(v * v for v in vec.values())) or 1
        return {w: v / length for w, v in vec.items()}
    q = vector(query)
    return [sum(value * q.get(word, 0) for word, value in vector(count).items()) for count in counts]


def search(rows, raw_query):
    rows = [r for r in rows if r.get('approval')]
    query = Query.model_validate(raw_query).model_dump()
    if query['max_budget_vnd'] is not None and query['purpose'] is None:
        raise ValueError('Ngân sách cần chọn sale hoặc rent để không trộn giá bán và tiền thuê tháng.')
    if query['min_bedrooms'] is not None and query['max_bedrooms'] is not None and query['min_bedrooms'] > query['max_bedrooms']:
        raise ValueError('Số phòng tối thiểu lớn hơn tối đa.')
    scores = rank([r['source']['title'] + ' ' + r['source']['description'] for r in rows], query['keywords'])
    result = []
    for row, score in zip(rows, scores):
        if not row.get('approval'):
            continue
        facts = row['facts']
        if any(query[field] is not None and (facts[field] is None or norm(facts[field]) != norm(query[field])) for field in ('purpose', 'property_type', 'city', 'area_name')):
            continue
        if query['min_bedrooms'] is not None and (facts['bedrooms'] is None or facts['bedrooms'] < query['min_bedrooms']):
            continue
        if query['max_bedrooms'] is not None and (facts['bedrooms'] is None or facts['bedrooms'] > query['max_bedrooms']):
            continue
        if query['min_area_m2'] is not None:
            if facts['area_m2'] is None:
                continue
            area, minimum = Decimal(facts['area_m2']), Decimal(query['min_area_m2'])
            if area < minimum or (query['area_operator'] == 'gt' and area == minimum):
                continue
        values = derived(facts)
        if query['max_budget_vnd'] is not None:
            price = values['sale_total_vnd'] if query['purpose'] == 'sale' else values['monthly_rent_vnd']
            if price is None:
                continue
            if Decimal(price) > Decimal(query['max_budget_vnd']) or (query['budget_operator'] == 'lt' and Decimal(price) == Decimal(query['max_budget_vnd'])):
                continue
        result.append({'id': row['id'], 'title': row['source']['title'], 'rank_score': round(score, 4),
                       **{k: v for k, v in facts.items() if k != 'evidence'}, **values})
    return sorted(result, key=lambda r: (-r['rank_score'], r['id']))


def duplicates(rows):
    output = []
    for i, a in enumerate(rows):
        for b in rows[i + 1:]:
            text_a = norm(a['source']['title'] + ' ' + a['source']['description'])
            text_b = norm(b['source']['title'] + ' ' + b['source']['description'])
            similarity = SequenceMatcher(None, text_a, text_b).ratio()
            same_context = all(a['facts'].get(k) is not None and a['facts'].get(k) == b['facts'].get(k)
                               for k in ('purpose', 'property_type', 'city', 'area_name'))
            image_distance = (int(a['image_hash'], 16) ^ int(b['image_hash'], 16)).bit_count() if a.get('image_hash') and b.get('image_hash') else None
            if text_a == text_b or (same_context and similarity >= .94) or (image_distance is not None and image_distance <= 4):
                output.append({'id_a': a['id'], 'id_b': b['id'], 'text_similarity': round(similarity, 3),
                               'image_hash_distance': image_distance, 'action': 'review_only; not property identity proof'})
    return output
