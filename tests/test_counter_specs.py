import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
from xml.sax.saxutils import escape

spec = importlib.util.spec_from_file_location('counter_specs', Path(__file__).parents[1] / 'scripts/counter_specs.py')
specs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(specs)


def workbook(path):
    ns = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    rows = [['Family Name', 'Type Name', 'Type Description', 'NE Type', 'Unit', 'Period', 'Formula'],
            ['RRC', '', '', '', '', '', ''],
            ['', 'ConnEstabAtt', 'Number of attempts', 'ACPF', 'count', 'event', 'sum(ConnEstabAtt)'],
            ['', 'ConnEstabSucc', 'Number of successes', 'ACPF', 'count', 'event', '']]
    xml = '<worksheet xmlns="%s"><sheetData>' % ns
    for row_number, values in enumerate(rows, 1):
        xml += '<row r="%d">' % row_number
        for column, value in enumerate(values):
            if value:
                xml += '<c r="%s%d" t="inlineStr"><is><t>%s</t></is></c>' % (chr(65 + column), row_number, escape(value))
        xml += '</row>'
    xml += '</sheetData><mergeCells><mergeCell ref="A2:A4"/></mergeCells></worksheet>'
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('xl/workbook.xml', '<workbook xmlns="%s" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="counter" sheetId="1" r:id="rId1"/></sheets></workbook>' % ns)
        archive.writestr('xl/_rels/workbook.xml.rels', '<Relationships><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>')
        archive.writestr('xl/worksheets/sheet1.xml', xml)


class CounterSpecsTests(unittest.TestCase):
    def test_shared_strings_rich_text_and_formula_text(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'ACPF.xlsx'
            workbook(path)
            with zipfile.ZipFile(path) as archive:
                members = {name: archive.read(name) for name in archive.namelist()}
            ns = specs.NS['s']
            members['xl/sharedStrings.xml'] = ('<sst xmlns="%s"><si><r><t>Number of </t></r><r><t>attempts</t></r></si></sst>' % ns).encode()
            xml = members['xl/worksheets/sheet1.xml'].decode()
            xml = xml.replace('<c r="C3" t="inlineStr"><is><t>Number of attempts</t></is></c>', '<c r="C3" t="s"><v>0</v></c>')
            xml = xml.replace('<c r="G3" t="inlineStr"><is><t>sum(ConnEstabAtt)</t></is></c>', '<c r="G3"><f>SUM(A1:A2)</f><v>0</v></c>')
            members['xl/worksheets/sheet1.xml'] = xml.encode()
            with zipfile.ZipFile(path, 'w') as archive:
                for name, content in members.items():
                    archive.writestr(name, content)
            fields = specs.workbook_records(path)[0]['fields']
            self.assertEqual(fields['Type Description'], 'Number of attempts')
            self.assertEqual(fields['Formula'], '=SUM(A1:A2)')

    def test_ne_types_prefix_suffix_and_uadpf_are_distinct(self):
        self.assertEqual(specs.ne_type('ADPF_300'), 'ADPF')
        self.assertEqual(specs.ne_type('300_uADPF.'), 'uADPF')
        self.assertIsNone(specs.ne_type('xADPF'))
        self.assertIsNone(specs.ne_type('ADPF_ACPF'))

    def test_reads_counter_rows_explicit_merges_blanks_and_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'ACPF.xlsx'
            workbook(path)
            original = path.read_bytes()
            records = specs.workbook_records(path)
            self.assertEqual(len(records), 2)
            self.assertEqual(records[0]['fields']['Family Name'], 'RRC')
            self.assertEqual(records[1]['fields']['Formula'], '')
            self.assertEqual(records[0]['source']['row'], 3)
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(len(specs.find_definitions(records, 'ConnEstabAtt(count)', 'ACPF', 'RRC')), 1)
            self.assertEqual(specs.find_definitions(records, 'ConnEstabAtt', 'ADPF', 'RRC'), [])
            self.assertEqual(specs.find_definitions(records, 'ConnEstabAtt', 'ACPF', 'Other'), [])

    def test_ambiguous_and_stale_definitions_are_not_supplied(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = root / 'knowledge/counter-spec'
            directory.mkdir(parents=True)
            path = directory / 'ACPF.xlsx'
            workbook(path)
            records = specs.workbook_records(path)
            index = directory / 'index.json'
            item = {'counter_label': 'ConnEstabAtt(count)', 'family': 'RRC'}
            observations = [{'ne_id': 'ACPF_6'}]
            index.write_text(json.dumps({'records': records}))
            self.assertEqual(len(specs.definitions_for(root, item, observations)[0]), 1)
            index.write_text(json.dumps({'records': records + records}))
            self.assertEqual(specs.definitions_for(root, item, observations)[0], [])
            index.write_text(json.dumps({'records': records}))
            path.write_bytes(b'changed')
            self.assertEqual(specs.definitions_for(root, item, observations)[0], [])


if __name__ == '__main__':
    unittest.main()
