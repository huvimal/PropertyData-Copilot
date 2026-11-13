import csv
import io
import json
import sqlite3
import zipfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from domain import Facts, digest, derived, quality


def now():
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path='private/property.sqlite3'):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS listings (
                id TEXT PRIMARY KEY, source TEXT, facts TEXT, extraction TEXT DEFAULT '{}', issues TEXT DEFAULT '[]',
                state TEXT DEFAULT 'pending', revision INTEGER DEFAULT 0, approval TEXT,
                image BLOB, image_hash TEXT, error TEXT);
                CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, listing_id TEXT, at TEXT, action TEXT, details TEXT);''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=20)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def event(self, db, identifier, action, data):
        db.execute('INSERT INTO events(listing_id,at,action,details) VALUES(?,?,?,?)',
                   (identifier, now(), action, json.dumps(data, ensure_ascii=False)))

    def import_rows(self, rows):
        result = {'added': 0, 'unchanged': 0, 'updated': 0}
        with self.connect() as db:
            for row in rows:
                source = json.dumps(row, ensure_ascii=False, sort_keys=True)
                existing = db.execute('SELECT source FROM listings WHERE id=?', (row['id'],)).fetchone()
                if existing and existing['source'] == source:
                    result['unchanged'] += 1
                    continue
                if existing:
                    db.execute("UPDATE listings SET source=?,facts=?,extraction='{}',issues='[]',state='pending',revision=revision+1,approval=NULL,error=NULL,image=NULL,image_hash=NULL WHERE id=?",
                               (source, json.dumps(Facts().model_dump()), row['id']))
                    result['updated'] += 1
                else:
                    db.execute('INSERT INTO listings(id,source,facts) VALUES(?,?,?)', (row['id'], source, json.dumps(Facts().model_dump())))
                    result['added'] += 1
                self.event(db, row['id'], 'import', {'source_hash': digest(row)})
        return result

    def rows(self, approved_only=False):
        with self.connect() as db:
            rows = [dict(r) for r in db.execute('SELECT * FROM listings ORDER BY id')]
        output = []
        for row in rows:
            for field in ('source', 'facts', 'extraction', 'issues', 'approval'):
                row[field] = json.loads(row[field]) if row[field] else None
            valid = row['approval'] and row['state'] == 'approved' and row['approval']['revision'] == row['revision'] and row['approval']['facts_hash'] == digest(row['facts']) and row['approval']['source_hash'] == digest(row['source'])
            if not valid:
                row['approval'] = None
            if not approved_only or valid:
                output.append(row)
        return output

    def get(self, identifier):
        return next(r for r in self.rows() if r['id'] == identifier)

    def save_result(self, identifier, revision, raw, issues, extraction, mode):
        facts = Facts.model_validate(raw).model_dump()
        with self.connect() as db:
            changed = db.execute("UPDATE listings SET facts=?,extraction=?,issues=?,state='review',revision=revision+1,approval=NULL,error=NULL WHERE id=? AND revision=?",
                (json.dumps(facts, ensure_ascii=False), json.dumps(extraction, ensure_ascii=False), json.dumps(issues, ensure_ascii=False), identifier, revision)).rowcount
            if not changed:
                raise ValueError('Tin đã thay đổi; tải lại trước khi lưu.')
            self.event(db, identifier, 'extraction', {'mode': mode, 'raw': extraction, 'validated': facts, 'issues': issues})

    def edit(self, identifier, revision, raw):
        facts = Facts.model_validate(raw).model_dump()
        with self.connect() as db:
            changed = db.execute("UPDATE listings SET facts=?,state='review',revision=revision+1,approval=NULL,error=NULL WHERE id=? AND revision=?",
                                 (json.dumps(facts, ensure_ascii=False), identifier, revision)).rowcount
            if not changed:
                raise ValueError('Tin đã thay đổi; tải lại trước khi lưu.')
            self.event(db, identifier, 'manual_edit', {'facts': facts})

    def attach(self, identifier, revision, data):
        from provider import image_hash
        hashed = image_hash(data)
        with self.connect() as db:
            changed = db.execute("UPDATE listings SET image=?,image_hash=?,revision=revision+1,approval=NULL,state='review' WHERE id=? AND revision=?",
                                 (data, hashed, identifier, revision)).rowcount
            if not changed:
                raise ValueError('Tin đã thay đổi.')
            self.event(db, identifier, 'attach_image', {'dhash': hashed})

    def fail(self, identifier, revision, error):
        with self.connect() as db:
            changed = db.execute("UPDATE listings SET state='error',approval=NULL,error=?,revision=revision+1 WHERE id=? AND revision=?",
                                 (str(error), identifier, revision)).rowcount
            if changed:
                self.event(db, identifier, 'provider_error', {'error': str(error)})

    def approve(self, identifier, revision, reviewer, note, checked):
        if not reviewer.strip() or not note.strip() or not checked:
            raise ValueError('Cần tên, ghi chú và xác nhận đối chiếu nguồn/đơn vị/ứng viên trùng.')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM listings WHERE id=?', (identifier,)).fetchone()
            if row is None or row['revision'] != revision or row['state'] != 'review':
                raise ValueError('Cần bản chờ rà soát đúng phiên bản.')
            facts = json.loads(row['facts'])
            problems = quality(facts)
            if problems:
                raise ValueError(' '.join(problems))
            approval = {'reviewer': reviewer.strip()[:100], 'note': note.strip()[:1000], 'at': now(),
                'revision': revision, 'source_hash': digest(json.loads(row['source'])), 'facts_hash': digest(facts)}
            db.execute("UPDATE listings SET state='approved',approval=? WHERE id=?", (json.dumps(approval, ensure_ascii=False), identifier))
            self.event(db, identifier, 'approve', approval)

    def export(self):
        rows = self.rows(approved_only=True)
        if not rows:
            raise ValueError('Chưa có tin đã duyệt.')
        out = io.StringIO(newline='')
        fields = ['id', 'title', 'purpose', 'property_type', 'city', 'area_name', 'bedrooms', 'area_m2', 'price_vnd',
                  'price_basis', 'sale_total_vnd', 'monthly_rent_vnd', 'price_is_derived']
        writer = csv.DictWriter(out, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        audit = []
        for row in rows:
            data = {'id': row['id'], 'title': row['source']['title'], **row['facts'], **derived(row['facts'])}
            safe = {}
            for field in fields:
                value = '' if data.get(field) is None else str(data[field])
                safe[field] = "'" + value if value.lstrip().startswith(('=', '+', '-', '@')) or value.startswith(('\t', '\r')) else value
            writer.writerow(safe)
            audit.append({k: row[k] for k in ('id', 'source', 'facts', 'extraction', 'issues', 'approval', 'image_hash')})
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('reviewed-listings.csv', out.getvalue().encode('utf-8-sig'))
            archive.writestr('audit.json', json.dumps({'exported_at': now(), 'records': audit}, ensure_ascii=False, indent=2))
        return buffer.getvalue()
