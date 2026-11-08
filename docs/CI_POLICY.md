# ForgeOS CI checkpoint policy

ForgeOS uses checkpoint CI, not CI-per-commit, during active development.

## Required workflow

1. Create engineering pull requests as Draft.
2. Batch related source, tests, and documentation into one logical commit whenever practical.
3. Draft branch updates do not run the full Foundation, Windows EXE, or Documentation gates.
4. When a logical checkpoint is complete, mark the pull request Ready for review. That transition runs one complete validation set against the latest head.
5. If a gate fails, convert the PR back to Draft, batch all related corrections, then mark it Ready again for one new validation cycle.
6. Merge only a latest head that has the required green gates.

## Root cause addressed

PR #51 accumulated 102 commits in roughly 15 hours. Before the first de-duplication correction, Foundation also ran on both feature-branch push and pull_request, producing genuine same-SHA duplicates. After that was fixed, rapid small commits still launched three workflows per commit.

The repository no longer treats ordinary PR branch synchronization as a full-validation trigger. Full PR validation is tied to explicit review checkpoints.

## Automated commit discipline

Automated changes should use one Git tree/commit for a coherent change set. Avoid a sequence of contents-API writes that produces one commit per file. Documentation and tests belonging to the same implementation checkpoint belong in the same commit.

## Coverage retained

- main pushes still run Foundation and Windows EXE validation.
- Ready-for-review PR checkpoints run Foundation, Documentation integrity, and Windows EXE validation.
- Documentation-only PRs skip the two heavy code/build workflows.
- Concurrency cancellation remains enabled as a secondary guard.
- Windows EXE packaging remains separate from the Foundation regression matrix.
