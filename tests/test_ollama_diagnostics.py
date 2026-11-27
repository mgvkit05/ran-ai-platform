import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('ollama_diagnostics', Path(__file__).parents[1] / 'scripts' / '17_diagnose_ollama.py')
diagnostics = importlib.util.module_from_spec(spec)
spec.loader.exec_module(diagnostics)


class DiagnosticsTests(unittest.TestCase):
    def test_linux_stat_field_offsets(self):
        fields = ['S'] + ['0'] * 30
        fields[1], fields[2], fields[9], fields[11], fields[12], fields[19] = '1', '123', '7', '200', '30', '98765'
        result = diagnostics.parse_stat('456 (runner (worker)) ' + ' '.join(fields))
        self.assertEqual(result['pgrp'], 123)
        self.assertEqual(result['ticks'], 230)
        self.assertEqual(result['major_faults'], 7)
        self.assertEqual(result['start'], '98765')

    def test_log_output_does_not_expose_log_bodies(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'private.log'
            path.write_text('PRIVATE_PATH warming up the model\nPRIVATE_VALUE client connection closed before loading\ncontext canceled PRIVATE_IDENTIFIER\n')
            events = diagnostics.log_events(path)
            self.assertEqual(events, ['warm-up began', 'client disconnected during load', 'request context canceled'])
            self.assertNotIn('PRIVATE', str(events))


if __name__ == '__main__':
    unittest.main()
