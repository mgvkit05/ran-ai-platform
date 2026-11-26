import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('inference_test', Path(__file__).parents[1] / 'scripts' / '06_test_ai_inference.py')
inference = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inference)


class InferenceConfigurationTests(unittest.TestCase):
    def test_threads_count_physical_cores_across_sockets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for cpu, package, core in ((0, 0, 0), (56, 0, 0), (28, 1, 0), (84, 1, 0)):
                location = root / ('cpu%d' % cpu) / 'topology'
                location.mkdir(parents=True)
                (location / 'physical_package_id').write_text(str(package))
                (location / 'core_id').write_text(str(core))
            self.assertEqual(inference.physical_threads({0, 56, 28, 84}, root), 2)
            self.assertEqual(inference.physical_threads({0, 56}, root), 1)

    def test_missing_topology_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(inference.physical_threads({0, 1}, Path(directory)), 2)

    def test_request_uses_local_model_and_bounded_cpu_options(self):
        request = inference.payload(56)
        self.assertEqual(request['model'], 'gpt-oss:20b')
        self.assertEqual(request['options']['num_thread'], 56)
        self.assertEqual(request['options']['num_gpu'], 0)
        self.assertEqual(request['options']['num_ctx'], 2048)
        self.assertEqual(request['options']['num_predict'], 256)
        self.assertEqual(request['keep_alive'], 0)
        self.assertFalse(request['stream'])


if __name__ == '__main__':
    unittest.main()
