# Task database: integrity check and online backup

**Status:** experimental source feature; physical Windows backup-and-restore
acceptance is not complete. This is the user-initiated portion of [issue #4](https://github.com/DevTunaSoft/ForgeOS/issues/4).

## Why it exists

ForgeOS task descriptions, statuses, results, checkpoints and active task
leases reside in a local SQLite database normally at
`~/.forgeos/tasks.sqlite3`. SQLite uses Write-Ahead Logging (WAL), so
copying `tasks.sqlite3` alone while the application is active is not a
reliable snapshot. The built-in SQLite backup API makes a transactionally
consistent copy including committed WAL data without loading database rows
and diagnostics into Python memory.

## How to use it

Create a trusted backup folder outside the project checkout, then in Windows
PowerShell run:

```powershell
py -m forgeos db-check
py -m forgeos db-backup "C:\\ForgeOS-backups\\tasks-backup-2026-11-11.sqlite3"
# Optional: publish a separate bounded SHA-256/size manifest
py -m forgeos db-backup "C:\\ForgeOS-backups\\tasks-backup-2026-11-11.sqlite3" --manifest "C:\\ForgeOS-backups\\tasks-backup-2026-11-11.json"
```

The backup destination must not exist. Repeated backups need a *new name*.
The command refuses an active SQLite transaction, a missing destination
directory, an existing destination, or a symlink at the backup path.

The source integrity check uses `PRAGMA integrity_check(1)`; the online
copy is produced using SQLite's `Connection.backup(pages=64)`. The completed
temporary file is checked independently and flushed before exclusive
same-directory publication. On Windows, ForgeOS uses an exclusive rename;
on POSIX it uses a same-volume hard link followed by a parent-directory flush.
If the link or POSIX directory flush fails, the operation reports failure; the
publication path is never overwritten, and any visible destination must be
inspected before retrying. Python does not read the full database into RAM.
Windows flushes the staged file before rename but physical NTFS interruption
acceptance remains outstanding. The operation may still take time and disk
space proportional to the database's real size.

To inspect a different database, place the top-level `--db` option
before the subcommand:

```powershell
py -m forgeos --db "C:\\MyFolder\\tasks.sqlite3" db-check
```

## Inspect a saved backup without overwriting anything

The following creates a **new, separately named** SQLite database copy:

```powershell
py -m forgeos db-restore-copy "C:\\ForgeOS-backups\\tasks-backup-2026-11-11.sqlite3" "C:\\ForgeOS-backups\\restored-check.sqlite3"
py -m forgeos --db "C:\\ForgeOS-backups\\restored-check.sqlite3" db-check
```

Both files are preserved. The restore-copy command opens the source read-only,
checks SQLite integrity and the required ForgeOS task/lease schema, then uses
SQLite's online snapshot API to write a staged copy. It verifies that copy
and publishes it only if its destination is new and no other process already
created the file. Wrong-schema and corrupt sources fail closed without a
partial output. File paths containing spaces or special URI characters are
supported.

For transfer or storage workflows, `db-backup --manifest PATH` also publishes
a new small JSON manifest containing the backup byte size and streamed
SHA-256. A restore can require that exact digest with
`db-restore-copy SOURCE DESTINATION --manifest PATH`. The manifest is checked
before SQLite opens the source, and mismatches fail without creating the
destination. It detects accidental corruption, but is not a signature or
encryption and does not protect against an attacker who can replace both
files.

## Include a verified backup in a passive recovery bundle

After creating a new `db-backup`, it can be bound to the existing handoff,
source archive and diagnostic evidence during transfer:

```powershell
py -m forgeos recovery-bundle-index `
  "C:\\ForgeOS-backups\\task-1-handoff.jsonl" `
  "C:\\ForgeOS-backups\\task-1-worktree.zip" `
  "C:\\ForgeOS-backups\\task-1-index.jsonl" `
  --database "C:\\ForgeOS-backups\\tasks-backup.sqlite3" `
  --database-manifest "C:\\ForgeOS-backups\\tasks-backup.json"

py -m forgeos recovery-bundle `
  "C:\\ForgeOS-backups\\task-1-handoff.jsonl" `
  "C:\\ForgeOS-backups\\task-1-worktree.zip" `
  "C:\\ForgeOS-backups\\task-1-recovery.zip" `
  --database "C:\\ForgeOS-backups\\tasks-backup.sqlite3" `
  --database-manifest "C:\\ForgeOS-backups\\tasks-backup.json"
```

The index and package checker inspect the supplied database read-only, stream
its digest without loading it into Python memory, reject `-wal`, `-shm` and
rollback-journal sidecars, and cap the database entry at 512 MiB. Receiving
with `recovery-bundle-extract` materializes the file under `database/` for
inspection or a later explicit `db-restore-copy`/`db-restore-activate` step;
it never activates the file or overwrites the running task database. The
bundle remains unencrypted and does not provide physical power-loss recovery.

**This is a verified offline copy, not automatic activation.** It does not
stop ForgeOS, switch the GUI's active database, merge task histories, or
replace an existing database. The new copy is for inspection or a deliberately
separate ForgeOS launch.

## Explicit offline activation with rollback

When a deliberate replacement of the active database is required, first exit
ForgeOS and stop every process that can open the task database. Then provide a
new rollback filename and explicitly confirm the operation:

```powershell
py -m forgeos db-restore-activate `
  "C:\\ForgeOS-backups\\tasks-backup-2026-11-11.sqlite3" `
  "$env:USERPROFILE\\.forgeos\\tasks.sqlite3" `
  "C:\\ForgeOS-backups\\tasks-before-activation.sqlite3" `
  --manifest "C:\\ForgeOS-backups\\tasks-backup-2026-11-11.json" `
  --confirm
```

This command is offline and does not merge histories. It verifies the source
into a staged database, refuses symlinks, occupied rollback or marker paths,
and refuses existing `-wal`, `-shm` or rollback-journal sidecars. The active
database and rollback path must be in the same existing, non-symlinked folder.
The old active file is moved to the new rollback path before the verified
backup is published. A flushed marker named
`tasks.sqlite3.forgeos-activation.json` records the `prepared`, `old_moved`
or `published` phase while the operation is in progress.

If a Windows open handle or another publication error prevents replacement,
ForgeOS fails closed. After the old file has moved, it attempts to put that
file back. If that recovery move also fails, preserve both the rollback file
and the activation marker for diagnosis; do not delete or overwrite either
file. Inspect the bounded state with
`py -m forgeos db-activation-status ACTIVE`. When it reports `old_moved`,
ForgeOS is stopped, the destination is still absent, and the retained rollback
is trusted, use `py -m forgeos db-activation-rollback ACTIVE --confirm` to
restore the old database without overwriting an occupied path. A `prepared` or
`published` marker is inspection-only until its paths are understood. The
marker is evidence of an interrupted operation, not a guarantee of physical
power-loss recovery.

## Security and recovery limitations

- Backups are **not encrypted**. Task text, diagnostics or project paths may
  be sensitive. Use access-controlled, encrypted storage and handle exports
  as confidential.
- The restore-copy operation does **not** merge or overwrite a running database.
  The activation command is an explicit offline replacement and still
  requires the operator to stop ForgeOS; it is not a background failover.
- This task database does **not** contain all external evidence archives,
  Git worktrees, model weights or your original project repository. Those
  must be backed up separately if needed for complete disaster recovery.
- The integrity check reports SQLite structural integrity, not correctness
  of every task's semantics, archived evidence or external file.
- No background schedule, automatic retention or destructive cleanup exists.
- Native SQLite/page-cache disk and memory behavior, failures on removable
  media, power-loss cases, marker replay and physical Windows restart
  recovery still require additional acceptance testing.

## Verification

The repository includes automated backup, restore-copy and activation
regressions in `tests/test_db_backup.py`, `tests/test_db_restore.py`,
`tests/test_db_activation.py` and `tests/test_cli_database.py`. Exact-head
GitHub CI is required for Linux and Windows. A CI green result does not
replace full physical Windows/restore, open-handle, disk-full, interruption
or power-loss testing.

## Read-only preflight: missing or incompatible task databases

`db-check` and `db-backup` now validate that the source is an existing,
regular (not symlinked) SQLite file with the essential ForgeOS task and
lease schema **before opening the normal Store**. A missing `--db` path is
refused rather than silently initialized as an empty task database.
Unrelated SQLite databases cannot be migrated just by running a check or
backup. The checks use read-only SQLite, without creating Python copies of
task rows. Database creation for normal first-time ForgeOS use is unchanged.
When no SQLite sidecar exists, the read-only preflight uses SQLite's immutable
URI mode so inspection itself does not create a `-shm` file. A present WAL or
rollback journal uses the normal read-only URI so committed journal state is
included and the sidecar remains visible to the safety checks.

## Cross-platform test fixture hardening

Database regression tests use filenames legal on both NTFS and POSIX.
A filename containing `#` or spaces is a useful SQLite URI-escaping test,
but `?` is a forbidden Windows filename character and must not be used
for an actual test file. Python's `with sqlite3.connect(...) as db`
commits/rolls back a transaction but **does not close the connection**.
The test suite now explicitly closes all of its temporary SQLite
connections so Windows can delete the test directory reliably.
