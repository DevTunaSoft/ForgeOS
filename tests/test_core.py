import json
from pathlib import Path
import subprocess
import sys
import tempfile
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
    def test_bad_status(self):
        a=self.store.add(str(self.repo),'one')
        with self.assertRaises(ValueError): self.store.update(a,'invented')

if __name__=='__main__': unittest.main()

