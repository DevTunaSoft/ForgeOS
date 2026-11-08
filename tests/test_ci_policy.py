from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / '.github' / 'workflows'

class CIWorkflowPolicyTests(unittest.TestCase):
    def read(self, name):
        return (WORKFLOWS / name).read_text(encoding='utf-8')

    def test_pr_validation_runs_on_new_and_updated_pull_requests(self):
        for name in ('ci.yml', 'docs-sync.yml', 'windows-exe.yml'):
            body = self.read(name)
            with self.subTest(workflow=name):
                self.assertIn('types: [opened, reopened, synchronize, ready_for_review]', body)
                self.assertIn('cancel-in-progress: true', body)
                self.assertIn('pull_request.number || github.ref_name', body)

    def test_draft_pr_jobs_are_fail_closed(self):
        for name in ('ci.yml', 'docs-sync.yml', 'windows-exe.yml'):
            with self.subTest(workflow=name):
                self.assertIn('github.event.pull_request.draft == false', self.read(name))

    def test_required_workflows_cover_documentation_only_prs(self):
        for name in ('ci.yml', 'windows-exe.yml'):
            body = self.read(name)
            with self.subTest(workflow=name):
                self.assertNotIn('paths-ignore:', body)

    def test_feature_branch_pushes_do_not_start_heavy_workflows(self):
        for name in ('ci.yml', 'windows-exe.yml'):
            body = self.read(name)
            with self.subTest(workflow=name):
                self.assertIn('push:\n    branches: [main]', body)

if __name__ == '__main__':
    unittest.main()
