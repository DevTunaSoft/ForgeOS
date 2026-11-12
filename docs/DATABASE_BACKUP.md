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
```

The backup destination must not exist. Repeated backups need a *new name*.
The command refuses an active SQLite transaction, a missing destination
directory, an existing destination, or a symlink at the backup path.

The source integrity check uses `PRAGMA integrity_check(1)`; the online
copy is produced using SQLite's `Connection.backup(pages=64)`. The completed
temporary file is checked independently and flushed before exclusive
same-directory hard-link publication. If publishing fails, no partial
destination is made visible. A filesystem that does not support hard links
will cause a deliberate failure. Python does not read the full database into
RAM. The operation may still take time and disk space proportional to the
database's real size.

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

**This is a verified offline copy, not automatic activation.** It does not
stop ForgeOS, switch the GUI's active database, merge task histories, or
replace an existing database. The new copy is for inspection or a deliberately
separate ForgeOS launch. If you need to replace your active database, do not
overwrite files while ForgeOS is running; the safe guided replacement workflow
is not yet implemented.

## Security and recovery limitations

- Backups are **not encrypted**. Task text, diagnostics or project paths may
  be sensitive. Use access-controlled, encrypted storage and handle exports
  as confidential.
- The restore-copy operation does **not** merge, activate, or overwrite a running database.
  Never replace `tasks.sqlite3` while ForgeOS is open. A guided activation
  and restoration workflow remains planned.
- This task database does **not** contain all external evidence archives,
  Git worktrees, model weights or your original project repository. Those
  must be backed up separately if needed for complete disaster recovery.
- The integrity check reports SQLite structural integrity, not correctness
  of every task's semantics, archived evidence or external file.
- No background schedule, automatic retention or destructive cleanup exists.
- Native SQLite/page-cache disk and memory behavior, failures on removable
  media, power-loss cases and physical Windows restart recovery still require
  additional acceptance testing.

## Verification

The isolated prototype ran locally on Python 3 with committed WAL data,
exclusive publication, refused overwrite and active transaction refusal.
The actual repository has automated regressions in
`tests/test_db_backup.py`; only its exact-commit GitHub CI status can
establish whether those tests pass across Linux and Windows. A CI green
result does not replace full physical Windows/restore testing.

## Read-only preflight: missing or incompatible task databases

`db-check` and `db-backup` now validate that the source is an existing,
regular (not symlinked) SQLite file with the essential ForgeOS task and
lease schema **before opening the normal Store**. A missing `--db` path is
refused rather than silently initialized as an empty task database.
Unrelated SQLite databases cannot be migrated just by running a check or
backup. The checks use read-only SQLite, without creating Python copies of
task rows. Database creation for normal first-time ForgeOS use is unchanged.

## Cross-platform test fixture hardening

Database regression tests use filenames legal on both NTFS and POSIX.
A filename containing `#` or spaces is a useful SQLite URI-escaping test,
but `?` is a forbidden Windows filename character and must not be used
for an actual test file. Python's `with sqlite3.connect(...) as db`
commits/rolls back a transaction but **does not close the connection**.
The test suite now explicitly closes all of its temporary SQLite
connections so Windows can delete the test directory reliably.
