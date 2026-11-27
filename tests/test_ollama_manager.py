import importlib.util
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('ollama_manager', Path(__file__).parents[1] / 'scripts' / '07_manage_ollama.py')
manager = importlib.util.module_from_spec(spec)
spec.loader.exec_module(manager)


class ManagerTests(unittest.TestCase):
    def test_identity_handles_spaces_and_parentheses_in_process_name(self):
        with tempfile.TemporaryDirectory() as directory:
            proc = Path(directory)
            (proc / '123').mkdir()
            fields = ['S'] + ['0'] * 18 + ['987654'] + ['0'] * 10
            (proc / '123' / 'stat').write_text('123 (a tricky (process)) ' + ' '.join(fields))
            self.assertEqual(manager.identity(123, proc), '987654')
            fields[0] = 'Z'
            (proc / '123' / 'stat').write_text('123 (dead) ' + ' '.join(fields))
            self.assertIsNone(manager.identity(123, proc))
            self.assertIsNone(manager.identity(999, proc))

    def test_reused_pid_not_owned(self):
        with patch.object(manager, 'identity', return_value='new-start'):
            self.assertFalse(manager.owned({'pid': 123, 'start_ticks': 'old-start', 'binary': '/old/ollama'}))

    def test_stop_refuses_unowned_process(self):
        with patch.object(manager, 'owned', return_value=False), patch.object(manager.os, 'killpg', create=True) as signal_group:
            with self.assertRaisesRegex(RuntimeError, 'no process signalled'):
                manager.stop({'pid': 123})
            signal_group.assert_not_called()

    def test_warmup_and_questions_keep_model_resident(self):
        warmup = manager.generation(32)
        question = manager.generation(32, 'Explain handover success rate.')
        self.assertEqual(warmup['prompt'], '')
        self.assertEqual(question['prompt'], 'Explain handover success rate.')
        self.assertEqual(question['keep_alive'], -1)
        self.assertEqual(question['model'], 'gpt-oss:20b')
        self.assertEqual(question['options']['num_thread'], 32)
        self.assertEqual(question['options']['num_gpu'], 0)
        self.assertEqual(question['options']['num_ctx'], 2048)

    def test_recovery_batch_is_preserved_for_questions(self):
        warmup = manager.generation(8, batch_size=32)
        question = manager.generation(8, 'Reply OK', 32)
        self.assertEqual(warmup['options']['num_batch'], 32)
        self.assertEqual(question['options']['num_batch'], 32)
        self.assertEqual(question['options']['num_thread'], 8)

    def test_question_stream_prints_answer_and_timings_without_reasoning(self):
        events = [{'thinking': 'PRIVATE_REASONING'}, {'response': 'Unknown counter definition.'},
                  {'done': True, 'done_reason': 'stop', 'eval_count': 5}]
        class Opener:
            def open(self, request, timeout):
                return io.BytesIO(('\n'.join(json.dumps(event) for event in events) + '\n').encode())
        output = io.StringIO()
        with patch.object(manager.urllib.request, 'build_opener', return_value=Opener()), contextlib.redirect_stdout(output):
            result = manager.stream_question({'stream': True})
        self.assertEqual(result['response'], 'Unknown counter definition.')
        self.assertIn('Timings:', output.getvalue())
        self.assertNotIn('PRIVATE_REASONING', output.getvalue())


if __name__ == '__main__':
    unittest.main()
