#!/usr/bin/env python3
"""Index private XLSX counter worksheets using only Python's standard library.

Save as $HOME/ran-ai/app/counter_specs.py and run without sudo:
  "$HOME/ran-ai/venv/bin/python" "$HOME/ran-ai/app/counter_specs.py" build
  "$HOME/ran-ai/venv/bin/python" "$HOME/ran-ai/app/counter_specs.py" lookup ConnEstabAtt --ne-type ACPF
Source: $HOME/ran-ai/knowledge/counter-spec/*.xlsx (never modified).
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import posixpath
import re
import xml.etree.ElementTree as ET
import zipfile

NS = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
NE_TYPES = ('ADPF', 'uADPF', 'ACPF', 'AUPF')


def normalized(value):
    return ' '.join(value.split()).casefold()


def ne_type(value):
    matches = re.findall(r'(?<![A-Za-z0-9])(uADPF|ADPF|ACPF|AUPF)(?![A-Za-z0-9])', value, re.I)
    found = {kind for kind in NE_TYPES if any(kind.casefold() == x.casefold() for x in matches)}
    return next(iter(found)) if len(found) == 1 else None


def coordinate(reference):
    match = re.fullmatch(r'([A-Z]+)([0-9]+)', reference)
    if not match:
        raise ValueError('Unsupported worksheet coordinate')
    column = 0
    for char in match[1]:
        column = column * 26 + ord(char) - 64
    return int(match[2]), column


def read_counter_sheet(path):
    with zipfile.ZipFile(path) as archive:
        shared = []
        if 'xl/sharedStrings.xml' in archive.namelist():
            for item in ET.fromstring(archive.read('xl/sharedStrings.xml')).findall('s:si', NS):
                shared.append(''.join(t.text or '' for t in item.findall('.//s:t', NS)))
        workbook = ET.fromstring(archive.read('xl/workbook.xml'))
        sheets = [s for s in workbook.findall('s:sheets/s:sheet', NS)
                  if normalized(s.attrib['name']) == 'counter']
        if len(sheets) != 1:
            raise ValueError('%s: expected one counter worksheet' % path.name)
        sheet = sheets[0]
        relation = sheet.attrib['{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id']
        relations = ET.fromstring(archive.read('xl/_rels/workbook.xml.rels'))
        target = next(r.attrib['Target'] for r in relations if r.attrib['Id'] == relation)
        target = posixpath.normpath(target.lstrip('/') if target.startswith('/') else 'xl/' + target)
        xml = ET.fromstring(archive.read(target))
        cells = {}
        for cell in xml.findall('s:sheetData/s:row/s:c', NS):
            position = coordinate(cell.attrib['r'])
            value = cell.findtext('s:v', default='', namespaces=NS)
            if cell.attrib.get('t') == 's':
                value = shared[int(value)]
            elif cell.attrib.get('t') == 'inlineStr':
                value = ''.join(t.text or '' for t in cell.findall('.//s:t', NS))
            formula = cell.findtext('s:f', namespaces=NS)
            cells[position] = '=' + formula if formula is not None else value
        # Only explicit Excel merges propagate values. Ordinary blanks remain blank.
        for merge in xml.findall('s:mergeCells/s:mergeCell', NS):
            reference = merge.attrib.get('ref', '').strip()
            parts = reference.split(':')
            if len(parts) == 1:
                start = end = coordinate(parts[0])
            elif len(parts) == 2:
                start, end = (coordinate(x) for x in parts)
            else:
                raise ValueError('%s: unsupported merge reference %r' % (path.name, reference))
            if end[0] < start[0] or end[1] < start[1]:
                raise ValueError('%s: reversed merge reference %r' % (path.name, reference))
            if (end[0] - start[0] + 1) * (end[1] - start[1] + 1) > 1000000:
                raise ValueError('Worksheet merge too large')
            value = cells.get(start, '')
            for row in range(start[0], end[0] + 1):
                for column in range(start[1], end[1] + 1):
                    if not cells.get((row, column)):
                        cells[row, column] = value
        return sheet.attrib['name'], cells


def workbook_records(path):
    sheet, cells = read_counter_sheet(path)
    rows = {}
    for (row, column), value in cells.items():
        rows.setdefault(row, {})[column] = value
    header_rows = {row for (row, col), value in cells.items() if normalized(value) == 'type name'}
    headers = None
    for row in sorted(header_rows):
        candidate = {col: ' '.join(value.split()) for col, value in rows[row].items() if value.strip()}
        if {'type name', 'type description', 'ne type'} <= {normalized(v) for v in candidate.values()}:
            header_row, headers = row, candidate
            break
    if headers is None:
        raise ValueError('%s: counter headings not found' % path.name)
    if len(set(normalized(v) for v in headers.values())) != len(headers):
        raise ValueError('%s: duplicate headings require review' % path.name)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    records = []
    for row in sorted(r for r in rows if r > header_row):
        fields = {name: rows[row].get(col, '') for col, name in headers.items()}
        lookup = {normalized(k): v for k, v in fields.items()}
        name = lookup['type name'].strip()
        if not name or normalized(name) == 'type name':
            continue
        kind = ne_type(lookup['ne type']) or ne_type(path.stem)
        if not kind:
            raise ValueError('%s: row %d needs an unambiguous NE type in its cell or filename' % (path.name, row))
        if lookup['ne type'].strip() and ne_type(lookup['ne type']) != kind:
            raise ValueError('%s: row %d has an unsupported or ambiguous NE type' % (path.name, row))
        records.append({'ne_type': kind, 'counter': name, 'fields': fields,
                        'source': {'workbook': path.name, 'sha256': digest, 'sheet': sheet, 'row': row}})
    if not records:
        raise ValueError('%s: no counter definitions found' % path.name)
    return records


def find_definitions(records, counter, kind, family=None):
    # Only remove the known CSV unit suffix, never approximate a counter name.
    names = {normalized(counter), normalized(re.sub(r'\([^()]*\)$', '', counter).strip())}
    matches = [r for r in records if r['ne_type'] == kind and normalized(r['counter']) in names]
    if family:
        scoped = [r for r in matches if normalized(family) in
                  {normalized(v) for k, v in r['fields'].items()
                   if normalized(k) in ('family name', 'family display name')}]
        matches = scoped
    return matches


def definitions_for(root, item, observations):
    path = root / 'knowledge/counter-spec/index.json'
    if not path.exists():
        return [], 'No counter specification index. Run counter_specs.py build.'
    kinds = {ne_type(row.get('ne_id', '')) for row in observations}
    if None in kinds or len(kinds) != 1:
        return [], 'Documentation lookup needs one identifiable NE type in the sample.'
    index = json.loads(path.read_text(encoding='utf-8'))
    matches = find_definitions(index['records'], item['counter_label'], next(iter(kinds)), item['family'])
    if len(matches) != 1:
        return [], 'Documentation: %d exact NE type/family/counter matches; no definition selected.' % len(matches)
    selected = matches[0]
    source = root / 'knowledge/counter-spec' / selected['source']['workbook']
    if not source.exists() or hashlib.sha256(source.read_bytes()).hexdigest() != selected['source']['sha256']:
        return [], 'Specification changed or removed. Rebuild the documentation index.'
    return matches, 'Exact documentation match [D1].'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest='action', required=True)
    actions.add_parser('build')
    lookup = actions.add_parser('lookup')
    lookup.add_argument('counter')
    lookup.add_argument('--ne-type', required=True, choices=NE_TYPES)
    args = parser.parse_args()
    os.umask(0o077)
    directory = Path.home() / 'ran-ai/knowledge/counter-spec'
    try:
        if args.action == 'build':
            paths = sorted(p for p in directory.iterdir() if p.suffix.lower() == '.xlsx' and not p.name.startswith('~$'))
            if not paths:
                raise ValueError('No XLSX documents in ' + str(directory))
            records = []
            for path in paths:
                print('Reading workbook %d of %d...' % (len({r['source']['workbook'] for r in records}) + 1, len(paths)), flush=True)
                records.extend(workbook_records(path))
            temporary = directory / 'index.json.tmp'
            temporary.write_text(json.dumps({'version': 1, 'records': records}, ensure_ascii=False), encoding='utf-8')
            temporary.chmod(0o600)
            temporary.replace(directory / 'index.json')
            print('SUCCESS: %d workbooks; %d counter definition rows. Original documents retained.' % (len(paths), len(records)))
        else:
            records = json.loads((directory / 'index.json').read_text(encoding='utf-8'))['records']
            matches = find_definitions(records, args.counter, args.ne_type)
            print(json.dumps(matches, indent=2, ensure_ascii=True))
            print('Matching definitions: %d. Output is private; keep it on the server.' % len(matches))
        return 0
    except (OSError, ValueError, KeyError, StopIteration, zipfile.BadZipFile, ET.ParseError) as error:
        print('FAILED: %s. Existing index and source documents retained.' % error)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
