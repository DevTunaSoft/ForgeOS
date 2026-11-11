import json
from pathlib import Path
import subprocess
import sys
import tempfile
import random
import string
import threading
import time
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
    def test_task_description_utf8_limit_rejects_invalid_without_db_changes(self):
        from forgeos.core import MAX_TASK_DESCRIPTION_BYTES
        limit = MAX_TASK_DESCRIPTION_BYTES
        accepted = self.store.add(str(self.repo), "x" * limit)
        self.assertEqual(len(self.store.get(accepted)["description"]), limit)
        accepted_unicode = self.store.add(str(self.repo), "界" * (limit // 3))
        self.assertEqual(
            len(self.store.get(accepted_unicode)["description"].encode("utf-8")),
            (limit // 3) * 3)
        before = self.store.counts()["total"]
        for invalid in (
                "x" * (limit + 1), "界" * (limit // 3 + 1),
                " " * (limit + 1), None, 17, ""):
            with self.subTest(value_type=type(invalid).__name__,
                              size=len(invalid) if isinstance(invalid, str) else -1):
                with self.assertRaises(ValueError):
                    self.store.add(str(self.repo), invalid)
        self.assertEqual(self.store.counts()["total"], before)

    def test_direct_ollama_plan_rejects_large_prompt_without_network(self):
        from forgeos.core import MAX_TASK_DESCRIPTION_BYTES
        with patch("forgeos.core.urlopen") as request:
            with self.assertRaisesRegex(ValueError, "16 KiB"):
                Ollama().plan("z" * (MAX_TASK_DESCRIPTION_BYTES + 1))
            request.assert_not_called()

    def test_brief_rows_omit_large_diagnostic_blobs(self):
        task=self.store.add(str(self.repo),'Memory lightweight activity view')
        large='z'*(2*1024*1024)
        self.store.update(task,'passed',result=large,error=large)
        brief=self.store.list(brief=True)
        self.assertEqual(len(brief),1)
        self.assertEqual(set(brief[0]),{'id','status','description'})
        self.assertEqual(brief[0]['id'],task)
        self.assertEqual(self.store.get(task)['result'],large)
        with self.assertRaises(ValueError):
            self.store.list(brief=1)

    def test_paged_brief_tasks_are_bounded_and_ordered(self):
        ids = [self.store.add(str(self.repo), "x" * 10000 + str(i))
               for i in range(510)]
        first = self.store.list(brief=True, limit=150)
        middle = self.store.list(brief=True, limit=150, offset=150)
        last = self.store.list(brief=True, limit=150, offset=450)
        self.assertEqual(len(first), 150)
        self.assertEqual(len(middle), 150)
        self.assertEqual(len(last), 60)
        self.assertEqual(first[0]["id"], ids[-1])
        self.assertEqual(middle[0]["id"], ids[-151])
        self.assertEqual(last[-1]["id"], ids[0])
        self.assertLessEqual(len(first[0]["description"]), 140)
        self.assertEqual(len({row["id"] for row in first + middle + last}), 360)
        self.assertEqual(self.store.counts()["total"], 510)

    def test_blocked_checkpoint_scan_bounded_by_bytes_and_uses_multiple_pages(self):
        import json
        blocked = []
        for index in range(45):
            task = self.store.add(str(self.repo), f"checkpoint {index}")
            self.store.update(task, "blocked", result=json.dumps({"n": index}))
            blocked.append(task)
            noise = self.store.add(str(self.repo), f"completed {index}")
            self.store.update(noise, "passed", result="Y" * 90000,
                              error="Z" * 90000)
        huge = self.store.add(str(self.repo), "damaged huge checkpoint")
        payload = "🛠" * 17000
        self.store.update(huge, "blocked", result=payload)
        records = list(self.store.iter_blocked_checkpoints())
        self.assertEqual([r["id"] for r in records],
                         [huge] + list(reversed(blocked)))
        self.assertEqual(set(records[0]),
                         {"id", "status", "result", "checkpoint_bytes"})
        self.assertIsNone(records[0]["result"])
        self.assertEqual(records[0]["checkpoint_bytes"],
                         len(payload.encode("utf-8")))
        self.assertTrue(all(r["checkpoint_bytes"] <= 64 * 1024
                            for r in records[1:]))
        self.assertTrue(all(r["status"] == "blocked" for r in records))

    def test_streaming_task_export_preserves_order_and_full_content(self):
        ids = []
        for index in range(200):
            task = self.store.add(str(self.repo), "streaming " + str(index))
            self.store.update(task, "passed" if index % 2 else "blocked",
                              result="payload:" + str(index) + "💾")
            ids.append(task)
        stream = self.store.iter_tasks()
        first = next(stream)
        self.assertEqual(first["id"], ids[-1])
        self.assertEqual(first["result"], "payload:199💾")
        stream.close()
        rows = list(self.store.iter_tasks())
        self.assertEqual(len(rows), 200)
        self.assertEqual([r["id"] for r in rows], list(reversed(ids)))
        self.assertEqual(rows[-1]["result"], "payload:0💾")

    def test_paged_task_counts_and_invalid_inputs(self):
        task = self.store.add(str(self.repo), "first")
        self.store.update(task, "passed")
        second = self.store.add(str(self.repo), "second")
        self.store.update(second, "blocked")
        third = self.store.add(str(self.repo), "third")
        self.store.update(third, "failed")
        totals = self.store.counts()
        self.assertEqual(totals, {"total": 3, "passed": 1, "failed": 1,
                                  "blocked": 1})
        self.assertEqual(self.store.list(brief=True, limit=2, offset=3), [])
        self.assertEqual(len(self.store.list()), 3)
        for kwargs in ({"limit": 0}, {"limit": 501}, {"limit": True},
                       {"limit": "8"}, {"limit": 1, "offset": -1},
                       {"limit": 2, "offset": 1.5}, {"limit": None, "offset": 1}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.store.list(**kwargs)

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
        self.assertIn(result['containment'], {'none','windows-job-object','windows-restricted-token+job-object'})
    def test_unittest_preset_failure(self):
        (self.repo/'test_example.py').write_text('import unittest\nclass T(unittest.TestCase):\n def test_bad(self): self.assertTrue(False)\n')
        result=run_test(str(self.repo),'python-unittest')
        self.assertFalse(result['passed'])
    def test_ollama_rejects_unbounded_generated_prompts_before_network(self):
        from forgeos.core import MAX_LOCAL_PROMPT_BYTES
        with patch("forgeos.core.urlopen") as request:
            with self.assertRaisesRegex(ValueError, "64 KiB"):
                Ollama().generate_edits("x" * (MAX_LOCAL_PROMPT_BYTES + 1))
            with self.assertRaisesRegex(ValueError, "64 KiB"):
                Ollama().generate_edits("界" * (MAX_LOCAL_PROMPT_BYTES // 3 + 1))
            request.assert_not_called()

    def test_local_only_ollama(self):
        with self.assertRaises(ValueError): Ollama(base='https://example.org')
    def test_ollama_request_limits_local_response_and_unloads_small_ram(self):
        import io
        from forgeos.model_routing import MemorySnapshot
        import json
        def response(data):
            class Resp(io.BytesIO):
                def __enter__(self): return self
                def __exit__(self, *args): self.close()
            return Resp(data)
        with patch("forgeos.model_routing.system_memory",
                   return_value=MemorySnapshot(4*1024**3, 3*1024**3)), \
             patch("forgeos.core.urlopen",
                   return_value=response(json.dumps({"response":"plan ready"}).encode())) as mocked:
            value = Ollama().plan("make an app")
            self.assertEqual(value, "plan ready")
            body = json.loads(mocked.call_args.args[0].data)
            self.assertEqual(body["keep_alive"], 0)
            self.assertEqual(body["options"]["num_ctx"], 1024)
            self.assertEqual(body["options"]["num_predict"], 900)
        with patch("forgeos.model_routing.system_memory",
                   return_value=MemorySnapshot(4*1024**3, 3*1024**3)), \
             patch("forgeos.core.urlopen",
                   return_value=response(b"z" * (8*1024*1024+1))):
            with self.assertRaisesRegex(RuntimeError, "memory limit"):
                Ollama().generate_edits("test")

    def test_ollama_slow_trickle_hits_total_deadline(self):
        import io
        from forgeos.model_routing import MemorySnapshot

        class Trickle(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *args): self.close()
            def read1(self, size=-1):
                return super().read(1)

        ticks = iter([0, 1, 100, 179, 180, 181])
        with (patch("forgeos.model_routing.system_memory",
                    return_value=MemorySnapshot(4*1024**3, 3*1024**3)),
              patch("forgeos.core.urlopen", return_value=Trickle(b"abcdef")),
              patch("forgeos.core.time.monotonic", side_effect=lambda: next(ticks))):
            with self.assertRaisesRegex(TimeoutError, "total deadline"):
                Ollama().plan("slow model")

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
        with patch('forgeos.core._execute_test_process',side_effect=FileNotFoundError('missing')):
            result=run_test(str(self.repo),'npm-test')
        self.assertFalse(result['passed'])
        self.assertIn('unavailable',result['output'])

    def test_timeout_is_reported(self):
        with patch('forgeos.core._execute_test_process',
                   side_effect=subprocess.TimeoutExpired('python',1)):
            result=run_test(str(self.repo),'python-unittest',timeout=1)
        self.assertFalse(result['passed'])
        self.assertIn('timed out',result['output'])

    def test_bad_status(self):
        a=self.store.add(str(self.repo),'one')
        with self.assertRaises(ValueError): self.store.update(a,'invented')


    def test_task_lease_exclusion_release_and_transition(self):
        task=self.store.add(str(self.repo),'lease test')
        self.store.update(task,'blocked',result='checkpoint')
        self.assertTrue(self.store.acquire_lease(task,'owner-a'))
        other=Store(self.root/'state'/'tasks.sqlite3')
        try:
            self.assertFalse(other.acquire_lease(task,'owner-b'))
            self.assertFalse(other.release_lease(task,'owner-b'))
            self.assertTrue(self.store.transition(task,{'blocked'},'editing',increment=True))
            self.assertEqual(self.store.get(task)['result'],'checkpoint')
            self.assertFalse(other.transition(task,{'blocked'},'testing'))
            self.assertTrue(self.store.release_lease(task,'owner-a'))
            self.assertTrue(other.acquire_lease(task,'owner-b'))
            self.assertTrue(other.release_lease(task,'owner-b'))
        finally:
            other.close()

    def test_recover_preserves_live_lease_then_reclaims_stale(self):
        task=self.store.add(str(self.repo),'stale lease')
        self.store.update(task,'testing',result='checkpoint')
        self.assertTrue(self.store.acquire_lease(task,'worker'))
        self.assertEqual(self.store.recover(stale_after=30),0)
        self.assertEqual(self.store.get(task)['status'],'testing')
        with self.store.db:
            self.store.db.execute('UPDATE task_leases SET heartbeat=? WHERE task_id=?',
                                  (time.time()-120,task))
        self.assertEqual(self.store.recover(stale_after=30),1)
        self.assertEqual(self.store.get(task)['status'],'blocked')
        self.assertEqual(self.store.get(task)['result'],'checkpoint')
        self.assertTrue(self.store.acquire_lease(task,'replacement'))
        self.assertTrue(self.store.release_lease(task,'replacement'))


    def test_planning_lease_prevents_recovery_until_stale(self):
        task = self.store.add(str(self.repo), "long planning")
        self.assertTrue(self.store.acquire_lease(task, "planner"))
        self.assertTrue(self.store.transition(task, {"queued"}, "planning",
                                              lease_owner="planner", increment=True))
        self.assertEqual(self.store.recover(stale_after=30), 0)
        self.assertEqual(self.store.get(task)["status"], "planning")
        self.assertFalse(self.store.transition(
            task, {"planning"}, "planned", result="stolen", lease_owner="impostor"))
        self.assertEqual(self.store.get(task)["status"], "planning")
        with self.store.db:
            self.store.db.execute("UPDATE task_leases SET heartbeat=? WHERE task_id=?",
                                  (time.time() - 100, task))
        self.assertEqual(self.store.recover(stale_after=30), 1)
        self.assertEqual(self.store.get(task)["status"], "queued")
        self.assertFalse(self.store.transition(
            task, {"planning"}, "planned", result="stale", lease_owner="planner"))

    def test_lease_guarded_transition_writes_result_atomically(self):
        task = self.store.add(str(self.repo), "plan")
        self.assertTrue(self.store.acquire_lease(task, "worker"))
        self.assertTrue(self.store.transition(task, {"queued"}, "planning",
                                              lease_owner="worker"))
        self.assertTrue(self.store.transition(task, {"planning"}, "planned",
                                              result="planned text", lease_owner="worker"))
        self.assertEqual(self.store.get(task)["result"], "planned text")
        self.assertFalse(self.store.transition(task, {"planned"}, "blocked",
                                               error="bad", lease_owner="wrong"))
        self.assertEqual(self.store.get(task)["status"], "planned")
        with self.assertRaisesRegex(ValueError, "lease owner"):
            self.store.transition(task, {"planned"}, "blocked", lease_owner="")
        self.assertTrue(self.store.release_lease(task, "worker"))

    def test_expired_lease_cannot_heartbeat_or_publish_before_reclaim(self):
        from forgeos.core import DEFAULT_LEASE_SECONDS
        task = self.store.add(str(self.repo), "expiry fence")
        self.assertTrue(self.store.acquire_lease(task, "old"))
        self.assertTrue(self.store.transition(task, {"queued"}, "editing",
                                              lease_owner="old"))
        with self.store.db:
            self.store.db.execute(
                "UPDATE task_leases SET heartbeat=? WHERE task_id=?",
                (time.time() - DEFAULT_LEASE_SECONDS - 5, task))
        self.assertFalse(self.store.heartbeat_lease(task, "old"))
        self.assertFalse(self.store.transition(
            task, {"editing"}, "passed", result="stale", lease_owner="old"))
        self.assertEqual(self.store.get(task)["result"], "")
        self.assertTrue(self.store.acquire_lease(task, "new"))
        self.assertTrue(self.store.transition(
            task, {"editing"}, "testing", result="replacement",
            lease_owner="new"))
        self.assertFalse(self.store.transition(
            task, {"testing"}, "passed", result="stale", lease_owner="old"))
        self.assertEqual(self.store.get(task)["result"], "replacement")
        self.assertTrue(self.store.release_lease(task, "new"))

    def test_unknown_task_cannot_receive_lease(self):
        with self.assertRaisesRegex(ValueError,'No task'):
            self.store.acquire_lease(999,'ghost')
        self.assertEqual(self.store.db.execute(
            'SELECT COUNT(*) FROM task_leases').fetchone()[0],0)

if __name__=='__main__': unittest.main()

