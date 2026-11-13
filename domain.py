import csv
import hashlib
import io
import json
import re
import unicodedata
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

TYPES = {'căn hộ': 'apartment', 'chung cư': 'apartment', 'apartment': 'apartment',
         'nhà phố': 'house', 'nhà riêng': 'house', 'house': 'house', 'đất': 'land', 'land': 'land'}
PURPOSES = {'bán': 'sale', 'mua': 'sale', 'sale': 'sale', 'cho thuê': 'rent', 'thuê': 'rent', 'rent': 'rent'}
CITIES = {'tp.hcm': 'ho_chi_minh', 'tp hcm': 'ho_chi_minh', 'hồ chí minh': 'ho_chi_minh',
          'ho chi minh': 'ho_chi_minh', 'ho_chi_minh': 'ho_chi_minh', 'hà nội': 'ha_noi',
          'ha noi': 'ha_noi', 'ha_noi': 'ha_noi'}


def norm(value):
    return ' '.join(unicodedata.normalize('NFC', str(value)).casefold().split())


def plain(value):
    return ''.join(c for c in unicodedata.normalize('NFD', norm(value).replace('đ', 'd')) if unicodedata.category(c) != 'Mn')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def decimal_string(value):
    if value is None:
        return None
    if not isinstance(value, str) or not re.fullmatch(r'\d+(?:\.\d{1,4})?', value) or Decimal(value) > Decimal('1000000000000000'):
        raise ValueError('Dùng chuỗi số không âm, dấu chấm thập phân, không phân cách nghìn.')
    return format(Decimal(value).normalize(), 'f')


class Facts(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    purpose: Literal['sale', 'rent'] | None = None
    property_type: Literal['apartment', 'house', 'land'] | None = None
    city: str | None = Field(default=None, max_length=100)
    area_name: str | None = Field(default=None, max_length=100)
    bedrooms: int | None = Field(default=None, ge=0, le=30)
    area_m2: str | None = None
    price_vnd: str | None = None
    price_basis: Literal['total', 'per_m2', 'per_month'] | None = None
    evidence: dict[str, str] = Field(default_factory=dict, max_length=10)

    @field_validator('city', 'area_name')
    @classmethod
    def locality(cls, value, info):
        if value is None:
            return None
        value = norm(value)
        if not value:
            raise ValueError('Dùng null cho khu vực/thành phố chưa biết.')
        return CITIES.get(value, value) if info.field_name == 'city' else value

    @field_validator('area_m2', 'price_vnd', mode='before')
    @classmethod
    def amount(cls, value):
        return decimal_string(value)

    @field_validator('evidence')
    @classmethod
    def quotes(cls, values):
        if any(key not in cls.model_fields or key == 'evidence' or not value.strip() or len(value) > 300 for key, value in values.items()):
            raise ValueError('Evidence cần tên trường hợp lệ và trích đoạn tối đa 300 ký tự.')
        return values


def read_csv(data):
    if len(data) > 2_000_000:
        raise ValueError('CSV tối đa 2 MB.')
    try:
        reader = csv.DictReader(io.StringIO(data.decode('utf-8-sig')), strict=True)
        if reader.fieldnames != ['id', 'title', 'description']:
            raise ValueError('CSV cần đúng cột id,title,description.')
        rows, seen = [], set()
        for row in reader:
            if len(rows) >= 200 or None in row or any(v is None for v in row.values()):
                raise ValueError('CSV sai số cột hoặc quá 200 tin.')
            row = {k: unicodedata.normalize('NFC', v).strip() for k, v in row.items()}
            if not re.fullmatch(r'[A-Za-z0-9_-]{1,50}', row['id']) or row['id'] in seen:
                raise ValueError('ID cần duy nhất, 1–50 chữ Latin/số/_/-.')
            if not row['title'] or len(row['title']) > 300 or len(row['description']) > 6000:
                raise ValueError('Tên cần 1–300 ký tự; mô tả tối đa 6000 ký tự.')
            seen.add(row['id'])
            rows.append(row)
        if not rows:
            raise ValueError('CSV rỗng.')
        return rows
    except (UnicodeDecodeError, csv.Error):
        raise ValueError('Cần CSV UTF-8 hợp lệ.') from None


def source_text(row):
    return row['title'] + '\n' + row['description']


def label_values(text, label):
    return [m.strip() for m in re.findall(r'(?im)(?:^|[;\n])\s*' + label + r'\s*:\s*([^;\n]+)', text)]


def source_number(text):
    text = text.strip()
    # A lone separator followed by three digits is ambiguous in scaled Vietnamese prices.
    if not re.fullmatch(r'\d+(?:[.,]\d{1,2})?', text):
        raise ValueError('Số nguồn mơ hồ; cần người dùng xác minh cách viết.')
    return Decimal(text.replace(',', '.'))


PRICE_PATTERN = re.compile(r'(?<![\w.,+\-])(?P<number>\d+(?:[.,]\d+)?)\s*(?P<unit>tỷ|ty|triệu|trieu|tr|vnd|đồng|dong|đ)(?!\w)(?:\s*/\s*(?P<basis>m²|m2|tháng|thang)(?!\w))?', re.I)
AREA_PATTERN = re.compile(r'(?<![\w.,+\-])(\d+(?:[.,]\d+)?)\s*(?:m²|m2)(?!\w)', re.I)


def parse_price(quote):
    match = PRICE_PATTERN.fullmatch(norm(quote))
    if not match:
        raise ValueError('Không đọc được giá/đơn vị trong trích đoạn.')
    multiplier = Decimal('1000000000') if plain(match['unit']) in ('ty',) else Decimal('1000000') if plain(match['unit']) in ('trieu', 'tr') else Decimal(1)
    value = source_number(match['number']) * multiplier
    basis = plain(match['basis']) if match['basis'] else None
    return format(value.normalize(), 'f'), 'per_m2' if basis in ('m2', 'm²') else 'per_month' if basis == 'thang' else 'total'


def parse_area(quote):
    match = AREA_PATTERN.fullmatch(norm(quote))
    if not match:
        raise ValueError('Diện tích cần m² hoặc m2 rõ ràng.')
    value = source_number(match[1])
    if value <= 0:
        raise ValueError('Diện tích phải > 0.')
    return format(value.normalize(), 'f')


def local_extract(row):
    text = source_text(row)
    data, evidence, issues = {}, {}, []
    mappings = [('purpose', 'Giao dịch', PURPOSES), ('property_type', 'Loại', TYPES), ('city', 'Thành phố', CITIES)]
    for field, label, mapping in mappings:
        values = label_values(text, label)
        canonical = {mapping.get(norm(v), norm(v) if field == 'city' else None) for v in values}
        if len(canonical) == 1 and None not in canonical:
            data[field] = next(iter(canonical))
            evidence[field] = values[0]
        elif values:
            issues.append(field + ': nguồn không rõ hoặc mâu thuẫn.')
    for field, label in [('area_name', 'Khu vực'), ('bedrooms', 'Phòng ngủ')]:
        values = label_values(text, label)
        if len({norm(v) for v in values}) == 1 and values:
            if field == 'bedrooms':
                if not re.fullmatch(r'\d{1,2}', values[0]) or int(values[0]) > 30:
                    issues.append('bedrooms: không đọc được số phòng rõ ràng.')
                    continue
                data[field] = int(values[0])
            else:
                data[field] = norm(values[0])
            evidence[field] = values[0]
        elif values:
            issues.append(field + ': nguồn mâu thuẫn.')
    # Parse explicit price/area labels, and compare multiple occurrences rather than taking the first.
    for label, field, parser in [('Giá', 'price_vnd', parse_price), ('Diện tích', 'area_m2', parse_area)]:
        quotes = label_values(text, label)
        pattern = PRICE_PATTERN if field == 'price_vnd' else AREA_PATTERN
        quotes += [m.group().strip() for m in pattern.finditer(row['title'])]
        if not quotes:
            quotes = [m.group().strip() for m in pattern.finditer(text)]
        candidates = []
        for quote in quotes:
            try:
                candidates.append(parser(quote))
            except ValueError:
                issues.append(field + ': số/đơn vị không rõ; đã để trống.')
        if candidates and len(candidates) == len(quotes) and len(set(candidates)) == 1:
            value = candidates[0]
            if field == 'price_vnd':
                data[field], data['price_basis'] = value
                evidence['price_basis'] = quotes[0]
            else:
                data[field] = value
            evidence[field] = quotes[0]
        elif quotes:
            issues.append(field + ': chưa có giá trị duy nhất được xác minh.')
    facts = Facts(**data, evidence=evidence)
    return facts.model_dump(), issues


def validate_ai(raw, row):
    facts = Facts.model_validate(raw).model_dump()
    text = norm(source_text(row))
    issues = []
    for field in ('purpose', 'property_type', 'city', 'area_name', 'bedrooms', 'area_m2', 'price_vnd', 'price_basis'):
        if facts[field] is None:
            continue
        quote = facts['evidence'].get(field, '')
        supported = bool(quote) and norm(quote) in text
        try:
            if field in ('purpose', 'property_type', 'city'):
                mapping = {'purpose': PURPOSES, 'property_type': TYPES, 'city': CITIES}[field]
                supported = supported and mapping.get(norm(quote), norm(quote) if field == 'city' else None) == facts[field]
            elif field == 'area_name':
                supported = supported and norm(quote) == norm(facts[field])
            elif field == 'bedrooms':
                supported = supported and bool(re.fullmatch(r'\d{1,2}', quote.strip())) and int(quote.strip()) == facts[field]
            elif field == 'area_m2':
                supported = supported and Decimal(parse_area(quote)) == Decimal(facts[field])
            elif field in ('price_vnd', 'price_basis'):
                value, basis = parse_price(quote)
                supported = supported and (Decimal(value) == Decimal(facts[field]) if field == 'price_vnd' else basis == facts[field])
        except ValueError:
            supported = False
        if not supported:
            facts[field] = None
            issues.append(field + ': bằng chứng không khớp, đã bỏ giá trị AI.')
    # Explicit conflicting labeled values require review even if AI picks one of them.
    conservative, conservative_issues = local_extract(row)
    if facts['bedrooms'] is not None:
        count = facts['bedrooms']
        if conservative['bedrooms'] != count and not re.search(r'(?<!\d)' + str(count) + r'\s*(?:pn|phòng ngủ)\b', text):
            facts['bedrooms'] = None
            issues.append('bedrooms: con số chưa có ngữ cảnh phòng ngủ; đã bỏ giá trị.')
    for field in ('purpose', 'property_type', 'city', 'area_name', 'bedrooms', 'area_m2', 'price_vnd'):
        if any(message.startswith(field + ':') for message in conservative_issues):
            facts[field] = None
            if field == 'price_vnd':
                facts['price_basis'] = None
            issues.append(field + ': nguồn có giá trị mơ hồ/mâu thuẫn; cần xác minh.')
    return Facts.model_validate(facts).model_dump(), issues


def derived(facts):
    result = {'sale_total_vnd': None, 'monthly_rent_vnd': None, 'price_is_derived': False}
    if facts['price_vnd'] is not None:
        price = Decimal(facts['price_vnd'])
        if facts['purpose'] == 'sale' and facts['price_basis'] == 'total':
            result['sale_total_vnd'] = format(price, 'f')
        elif facts['purpose'] == 'sale' and facts['price_basis'] == 'per_m2' and facts['area_m2'] is not None:
            result.update(sale_total_vnd=format(price * Decimal(facts['area_m2']), 'f'), price_is_derived=True)
        elif facts['purpose'] == 'rent' and facts['price_basis'] == 'per_month':
            result['monthly_rent_vnd'] = format(price, 'f')
    return result


def quality(facts):
    issues = []
    if not facts['purpose'] or not facts['property_type']:
        issues.append('Cần purpose và property_type trước khi duyệt.')
    if (facts['price_vnd'] is None) != (facts['price_basis'] is None):
        issues.append('Giá và cơ sở giá phải cùng có hoặc cùng để trống.')
    if facts['area_m2'] is not None and Decimal(facts['area_m2']) <= 0:
        issues.append('Diện tích phải > 0.')
    if facts['price_vnd'] is not None and (Decimal(facts['price_vnd']) <= 0 or Decimal(facts['price_vnd']) != Decimal(facts['price_vnd']).to_integral_value()):
        issues.append('Giá VND phải là số nguyên dương hoặc để trống khi chưa xác minh.')
    if facts['purpose'] == 'sale' and facts['price_basis'] == 'per_month':
        issues.append('Tin bán không được dùng giá theo tháng.')
    if facts['purpose'] == 'rent' and facts['price_vnd'] is not None and facts['price_basis'] != 'per_month':
        issues.append('Giá thuê cần đơn vị theo tháng rõ ràng; chưa hỗ trợ thuê theo m².')
    return issues
