import json
from pathlib import Path
import subprocess
import sys
import tempfile
import random
import string
import threading
import unittest
from unittest.mock import patch
from forgeos.core import Store, Ollama, run_test

class ForgeOSTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name)
        self.repo=self.root/'repo'; self.repo.mkdir(); (self.repo/'.git').mkdir()
        self.store=Store(self.root/'state'/'tasks.sqlite3')
    def tearDown(self):
        self.store.close(); self.temp.cleanup()
    def test_create_persist(self):
        task=self.store.add(str(self.repo),'Improve startup')
        self.assertEqual(self.store.get(task)['status'],'queued')
        self.store.close(); self.store=Store(self.root/'state'/'tasks.sqlite3')
        self.assertEqual(self.store.get(task)['description'],'Improve startup')
    def test_empty_and_non_repo_rejected(self):
        with self.assertRaises(ValueError): self.store.add(str(self.repo),'  ')
        with self.assertRaises(ValueError): self.store.add(str(self.root),'something')
    def test_recover_interrupted_only(self):
        a=self.store.add(str(self.repo),'one'); b=self.store.add(str(self.repo),'two')
        self.store.update(a,'planning'); self.store.update(b,'passed')
        self.assertEqual(self.store.recover(),1)
        self.assertEqual(self.store.get(a)['status'],'queued')
        self.assertEqual(self.store.get(b)['status'],'passed')
    def test_separate_thread_connection(self):
        task=self.store.add(str(self.repo),'thread test')
        errors=[]
        def worker():
            try:
                other=Store(self.root/'state'/'tasks.sqlite3')
                other.update(task,'planned',result='ok')
                other.close()
            except Exception as exc: errors.append(exc)
        t=threading.Thread(target=worker); t.start(); t.join(5)
        self.assertFalse(t.is_alive())
        self.assertFalse(errors)
        self.assertEqual(self.store.get(task)['status'],'planned')
    def test_unknown_task(self):
        with self.assertRaises(ValueError): self.store.get(999)
    def test_allowlist_rejects_injection(self):
        with self.assertRaises(ValueError): run_test(str(self.repo),'npm test; echo hacked')
    def test_unittest_preset_passes(self):
        (self.repo/'test_example.py').write_text('import unittest\nclass T(unittest.TestCase):\n def test_ok(self): self.assertTrue(True)\n')
        result=run_test(str(self.repo),'python-unittest')
        self.assertTrue(result['passed'],result['output'])
    def test_unittest_preset_failure(self):
        (self.repo/'test_example.py').write_text('import unittest\nclass T(unittest.TestCase):\n def test_bad(self): self.assertTrue(False)\n')
        result=run_test(str(self.repo),'python-unittest')
        self.assertFalse(result['passed'])
    def test_local_only_ollama(self):
        with self.assertRaises(ValueError): Ollama(base='https://example.org')
    def test_fuzz_task_descriptions_round_trip(self):
        rng=random.Random(20261107)
        alphabet=string.ascii_letters+string.digits+" \\n\\tΩ漢字'\\\";--"
        for _ in range(150):
            description=''.join(rng.choice(alphabet) for _ in range(rng.randint(1,180)))
            if not description.strip():
                continue
            task=self.store.add(str(self.repo),description)
            self.assertEqual(self.store.get(task)['description'],description.strip())
        self.assertEqual(self.store.db.execute('PRAGMA integrity_check').fetchone()[0],'ok')

    def test_recovery_idempotent_under_repeated_interruptions(self):
        tasks=[self.store.add(str(self.repo),f'job {i}') for i in range(75)]
        for i,task in enumerate(tasks):
            self.store.update(task,('planning','testing','passed','failed')[i%4],increment=True)
        self.assertEqual(self.store.recover(),38)
        self.assertEqual(self.store.recover(),0)
        self.assertEqual(sum(t['status']=='queued' for t in self.store.list()),19)
        self.assertEqual(sum(t['status']=='blocked' for t in self.store.list()),19)

    def test_fuzz_invalid_test_presets(self):
        rng=random.Random(8124)
        alphabet=string.ascii_letters+string.digits+" ;&|\\n\\x00"
        for _ in range(100):
            preset=''.join(rng.choice(alphabet) for _ in range(rng.randint(1,50)))
            if preset in {'python-unittest','python-pytest','npm-test','cargo-test','go-test','dotnet-test'}:
                continue
            with self.assertRaises(ValueError):
                run_test(str(self.repo),preset)

    def test_missing_test_executable_is_reported(self):
        with patch('forgeos.core.subprocess.run',side_effect=FileNotFoundError('missing')):
            result=run_test(str(self.repo),'npm-test')
        self.assertFalse(result['passed'])
        self.assertIn('unavailable',result['output'])

    def test_timeout_is_reported(self):
        with patch('forgeos.core.subprocess.run',side_effect=subprocess.TimeoutExpired('python',1)):
            result=run_test(str(self.repo),'python-unittest',timeout=1)
        self.assertFalse(result['passed'])
        self.assertIn('timed out',result['output'])

    def test_bad_status(self):
        a=self.store.add(str(self.repo),'one')
        with self.assertRaises(ValueError): self.store.update(a,'invented')

if __name__=='__main__': unittest.main()

