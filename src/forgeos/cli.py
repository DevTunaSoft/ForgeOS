"""ForgeOS command-line entry point; GUI is optional."""
import argparse
import json
import sys
from pathlib import Path
from .core import Store, Ollama
from .local_models import BACKENDS, create_model

def default_db():
    return Path.home()/'.forgeos'/'tasks.sqlite3'

def _cli_model(*, backend: str, model: str, endpoint: str):
    """Keep the original Ollama integration patchable and backwards-compatible."""
    if backend == "ollama" and not endpoint:
        return Ollama(model=model)
    return create_model(backend=backend, model=model, endpoint=endpoint)


def _add_recovery_auth_gate(parser):
    parser.add_argument(
        '--authentication', default='',
        help='Optional detached HMAC authentication record')
    parser.add_argument(
        '--key', default='',
        help='Shared-secret key required with --authentication')


def _recovery_auth_gate(parser, args):
    authentication = args.authentication or None
    key = args.key or None
    if (authentication is None) != (key is None):
        parser.error('--authentication and --key must be provided together')
    return authentication, key


def main(argv=None):
    parser=argparse.ArgumentParser(description='ForgeOS local-first development manager')
    parser.add_argument('--db',default=str(default_db()))
    sub=parser.add_subparsers(dest='action',required=True)
    sub.add_parser('gui'); sub.add_parser('list'); sub.add_parser('recover')
    sub.add_parser('db-check', help='Run an explicit SQLite database integrity check')
    journal = sub.add_parser(
        'journal', help='Show a bounded append-only task lifecycle journal page')
    journal.add_argument('--task-id', type=int, default=None)
    journal.add_argument('--limit', type=int, default=100)
    sub.add_parser(
        'journal-check', help='Stream-check lifecycle journal integrity')
    journal_export = sub.add_parser(
        'journal-export', help='Export bounded lifecycle rows as JSONL')
    journal_export.add_argument('destination', help='New JSONL file path')
    journal_export.add_argument('--task-id', type=int, default=None)
    journal_handoff = sub.add_parser(
        'journal-handoff', help='Export one task as a bounded replay plan')
    journal_handoff.add_argument('destination', help='New JSONL file path')
    journal_handoff.add_argument('--task-id', type=int, required=True)
    journal_handoff.add_argument('--manifest', default='',
                                help='Optional new JSON digest manifest path')
    journal_handoff_check = sub.add_parser(
        'journal-handoff-check', help='Verify a bounded journal handoff plan')
    journal_handoff_check.add_argument('source', help='Existing JSONL file path')
    journal_handoff_check.add_argument('--manifest', default='',
                                       help='Optional manifest required to match the source')
    journal_handoff_check.add_argument(
        '--evidence-directory', default='',
        help='Optional existing evidence directory to verify referenced archives')
    worktree_manifest = sub.add_parser(
        'worktree-manifest',
        help='Export bounded SHA-256 metadata for one Git worktree')
    worktree_manifest.add_argument('root', help='Existing Git worktree directory')
    worktree_manifest.add_argument(
        'destination', help='New JSONL manifest path outside the worktree')
    worktree_manifest_check = sub.add_parser(
        'worktree-manifest-check',
        help='Verify a worktree against a bounded integrity manifest')
    worktree_manifest_check.add_argument('source', help='Existing JSONL manifest path')
    worktree_manifest_check.add_argument('root', help='Existing Git worktree directory')
    worktree_manifest_check.add_argument('--max-issues', type=int, default=32)
    worktree_archive = sub.add_parser(
        'worktree-archive',
        help='Export a bounded source-only ZIP archive for one Git worktree')
    worktree_archive.add_argument('root', help='Existing Git worktree directory')
    worktree_archive.add_argument(
        'destination', help='New ZIP archive path outside the worktree')
    worktree_archive_check = sub.add_parser(
        'worktree-archive-check',
        help='Verify a source-only worktree ZIP archive')
    worktree_archive_check.add_argument('source', help='Existing ZIP archive path')
    worktree_archive_check.add_argument('--max-issues', type=int, default=32)
    bundle_index = sub.add_parser(
        'recovery-bundle-index',
        help='Bind recovery artifacts and an optional database backup')
    bundle_index.add_argument('handoff', help='Existing JSONL handoff path')
    bundle_index.add_argument('worktree_archive', help='Existing source ZIP archive')
    bundle_index.add_argument('destination', help='New JSONL index path')
    bundle_index.add_argument('--manifest', default='',
                              help='Optional existing handoff digest manifest')
    bundle_index.add_argument('--evidence-directory', default='',
                              help='Optional existing evidence directory')
    bundle_index.add_argument('--database', default='',
                              help='Optional existing verified SQLite backup')
    bundle_index.add_argument('--database-manifest', default='',
                              help='Optional SQLite backup manifest')
    bundle_index_check = sub.add_parser(
        'recovery-bundle-index-check',
        help='Verify a recovery bundle index and its referenced artifacts')
    bundle_index_check.add_argument('source', help='Existing JSONL index path')
    bundle_index_check.add_argument('--handoff', required=True,
                                    help='Existing JSONL handoff path')
    bundle_index_check.add_argument('--worktree-archive', required=True,
                                    help='Existing source ZIP archive')
    bundle_index_check.add_argument('--manifest', default='',
                                    help='Optional existing handoff digest manifest')
    bundle_index_check.add_argument('--evidence-directory', default='',
                                    help='Optional existing evidence directory')
    bundle_index_check.add_argument('--database', default='',
                                    help='Optional existing verified SQLite backup')
    bundle_index_check.add_argument('--database-manifest', default='',
                                    help='Optional SQLite backup manifest')
    bundle_index_check.add_argument('--max-issues', type=int, default=32)
    recovery_bundle = sub.add_parser(
        'recovery-bundle',
        help='Package validated recovery artifacts into one bounded ZIP')
    recovery_bundle.add_argument('handoff', help='Existing JSONL handoff path')
    recovery_bundle.add_argument('worktree_archive', help='Existing source ZIP archive')
    recovery_bundle.add_argument('destination', help='New recovery bundle ZIP path')
    recovery_bundle.add_argument('--manifest', default='',
                                 help='Optional existing handoff digest manifest')
    recovery_bundle.add_argument('--evidence-directory', default='',
                                 help='Optional existing evidence directory')
    recovery_bundle.add_argument('--database', default='',
                                 help='Optional existing verified SQLite backup')
    recovery_bundle.add_argument('--database-manifest', default='',
                                 help='Optional SQLite backup manifest')
    recovery_bundle_db_restore = sub.add_parser(
        'recovery-bundle-db-restore-copy',
        help='Restore a bundled database only to a new SQLite copy')
    recovery_bundle_db_restore.add_argument(
        'source', help='Existing recovery bundle ZIP path')
    recovery_bundle_db_restore.add_argument(
        'destination', help='New SQLite database path; never overwrites')
    _add_recovery_auth_gate(recovery_bundle_db_restore)
    recovery_bundle_db_activate = sub.add_parser(
        'recovery-bundle-db-restore-activate',
        help='Activate a bundled database offline with rollback')
    recovery_bundle_db_activate.add_argument(
        'source', help='Existing recovery bundle ZIP path')
    recovery_bundle_db_activate.add_argument(
        'destination', help='Active database path; ForgeOS must be stopped')
    recovery_bundle_db_activate.add_argument(
        'rollback', help='New path that retains the old active database')
    recovery_bundle_db_activate.add_argument(
        '--confirm', action='store_true',
        help='Confirm ForgeOS is stopped and activation is intended')
    _add_recovery_auth_gate(recovery_bundle_db_activate)
    recovery_bundle_check = sub.add_parser(
        'recovery-bundle-check',
        help='Verify one bounded recovery bundle ZIP')
    recovery_bundle_check.add_argument('source', help='Existing recovery bundle ZIP path')
    recovery_bundle_check.add_argument('--max-issues', type=int, default=32)
    _add_recovery_auth_gate(recovery_bundle_check)
    recovery_extract = sub.add_parser(
        'recovery-bundle-extract',
        help='Materialize a verified recovery bundle into a new directory')
    recovery_extract.add_argument('source', help='Existing recovery bundle ZIP path')
    recovery_extract.add_argument(
        'destination', help='New recovery directory path; never overwrites')
    _add_recovery_auth_gate(recovery_extract)
    recovery_extract_check = sub.add_parser(
        'recovery-bundle-extract-check',
        help='Verify a completed materialized recovery bundle')
    recovery_extract_check.add_argument(
        'source', help='Existing materialized recovery directory')
    recovery_extract_check.add_argument('--max-issues', type=int, default=32)
    recovery_extract_resume = sub.add_parser(
        'recovery-bundle-extract-resume',
        help='Resume a failed or interrupted recovery bundle extraction')
    recovery_extract_resume.add_argument(
        'source', help='Existing recovery bundle ZIP path')
    recovery_extract_resume.add_argument(
        'destination', help='Existing incomplete recovery directory')
    _add_recovery_auth_gate(recovery_extract_resume)
    recovery_auth = sub.add_parser(
        'recovery-bundle-auth',
        help='Create a detached HMAC authentication record for a recovery ZIP')
    recovery_auth.add_argument('source', help='Existing recovery bundle ZIP path')
    recovery_auth.add_argument('key', help='Existing shared-secret key file')
    recovery_auth.add_argument('destination', help='New authentication record path')
    recovery_auth_check = sub.add_parser(
        'recovery-bundle-auth-check',
        help='Verify a recovery ZIP with a detached HMAC record')
    recovery_auth_check.add_argument('source', help='Existing recovery bundle ZIP path')
    recovery_auth_check.add_argument(
        'authentication', help='Existing detached authentication record')
    recovery_auth_check.add_argument('key', help='Existing shared-secret key file')
    recovery_auth_keygen = sub.add_parser(
        'recovery-bundle-auth-keygen',
        help='Create a bounded OS-random key for recovery package authentication')
    recovery_auth_keygen.add_argument(
        'destination', help='New shared-secret key file; never overwrites')
    recovery_auth_keygen.add_argument(
        '--bytes', dest='key_bytes', type=int, default=32,
        help='Key size in bytes, from 32 through 4096 (default: 32)')
    db_backup=sub.add_parser('db-backup', help='Make a verified non-overwriting SQLite backup')
    db_backup.add_argument('destination', help='New file inside an existing folder')
    db_backup.add_argument('--manifest', default='',
                           help='Optional new JSON digest manifest path')
    restore_copy=sub.add_parser(
        'db-restore-copy',
        help='Validate an SQLite task backup and restore only to a brand-new file')
    restore_copy.add_argument('source', help='Existing SQLite task backup file')
    restore_copy.add_argument('destination', help='New database path; never overwrites')
    restore_copy.add_argument('--manifest', default='',
                              help='Optional manifest required to match the source')
    restore_activate = sub.add_parser(
        'db-restore-activate',
        help='Activate a verified backup offline and retain the current database')
    restore_activate.add_argument('source', help='Existing SQLite task backup file')
    restore_activate.add_argument(
        'destination', help='Active database path; ForgeOS must be stopped')
    restore_activate.add_argument(
        'rollback', help='New path that will retain the old active database')
    restore_activate.add_argument('--manifest', default='',
                                  help='Optional manifest required to match the source')
    restore_activate.add_argument(
        '--confirm', action='store_true',
        help='Confirm ForgeOS is stopped and activation is intended')
    activation_status = sub.add_parser(
        'db-activation-status',
        help='Inspect a bounded offline database activation marker')
    activation_status.add_argument('destination', help='Active database path')
    activation_rollback = sub.add_parser(
        'db-activation-rollback',
        help='Roll back an interrupted offline database activation')
    activation_rollback.add_argument('destination', help='Active database path')
    activation_rollback.add_argument(
        '--confirm', action='store_true',
        help='Confirm ForgeOS is stopped and rollback is intended')
    activation_rollback.add_argument(
        '--discard-staged', action='store_true',
        help='After validation, discard a crashed activation staging file')
    sub.add_parser('memory', help='One-shot local ForgeOS RAM/working-set report')
    sub.add_parser('acceptance', help='Write one bounded local acceptance snapshot')
    models=sub.add_parser('models',help='List models reported by one local runtime')
    models.add_argument('--backend',choices=BACKENDS,default='ollama')
    models.add_argument('--endpoint',default='')
    catalog=sub.add_parser('model-catalog',help='Opt-in Hugging Face public GGUF discovery')
    catalog.add_argument('operation',choices=('search','files'))
    catalog.add_argument('query',help='Search phrase or repository ID')
    local_list=sub.add_parser(
        'models-local', help='List bounded local GGUF downloads (metadata only)')
    local_list.add_argument('--directory',default='')
    local_verify=sub.add_parser(
        'model-verify-local',help='Explicit SHA-256 recheck of one downloaded GGUF')
    local_verify.add_argument('filename')
    local_verify.add_argument('--directory',default='')
    retired=sub.add_parser('models-retired',help='List retired GGUF models')
    retired.add_argument('--directory',default='')
    retire=sub.add_parser('model-retire-local',help='Retire verified GGUF without erasing it')
    retire.add_argument('filename')
    retire.add_argument('--directory',default='')
    retire.add_argument('--confirm',action='store_true')
    restore=sub.add_parser('model-restore-local',help='Restore retired GGUF by ID')
    restore.add_argument('identifier')
    restore.add_argument('--directory',default='')
    download=sub.add_parser('model-download',
                            help='Explicitly download one GGUF with pinned SHA-256')
    download.add_argument('repository',help='Public Hugging Face owner/model')
    download.add_argument('filename',help='Exact GGUF filename listed by model-catalog')
    download.add_argument('--directory',default='',help='Optional local model folder')
    evidence=sub.add_parser('diagnostics',help='Restore complete command diagnostic output')
    evidence.add_argument('operation',choices=['show','stats'])
    evidence.add_argument('reference',nargs='?')
    memory=sub.add_parser('learning',help='Show local verified repair learning statistics')
    memory.add_argument('operation',choices=['stats'])
    filter_parser=sub.add_parser('filters',help='Inspect or trust user output filters')
    filter_parser.add_argument('operation',choices=['status','trust'])
    filter_parser.add_argument('config',nargs='?')
    recover_edits=sub.add_parser('recover-edits',help='Safely roll back a pending edit journal')
    recover_edits.add_argument('workspace')
    add=sub.add_parser('add'); add.add_argument('project'); add.add_argument('description')
    plan=sub.add_parser('plan'); plan.add_argument('id',type=int); plan.add_argument('--model',default='qwen2.5-coder:3b')
    test=sub.add_parser('test'); test.add_argument('id',type=int); test.add_argument('--preset',required=True,
         choices=['python-unittest','python-pytest','npm-test','cargo-test','go-test','dotnet-test'])
    test.add_argument('--allow-host-tests',action='store_true',
                      help='Explicitly authorize tests in a trusted project; packaged Windows remains isolated')
    run=sub.add_parser('run',help='Generate, test and repair edits in a Git worktree')
    run.add_argument('id',type=int)
    run.add_argument('--workspace-parent',required=True)
    run.add_argument('--preset',default='python-unittest',choices=['python-unittest','python-pytest','npm-test','cargo-test','go-test','dotnet-test'])
    run.add_argument('--model',default='qwen2.5-coder:3b',
                     help='Model name; qualified-auto requires final verified local evidence')
    run.add_argument('--role',default='implementation',
                     choices=('planning','implementation','review_refactor','debugging',
                              'test_generation','security_review','documentation',
                              'context_summarization'))
    run.add_argument('--qualification-directory',default='',
                     help='Optional local final-qualification evidence directory')
    for command in (plan, run):
        command.add_argument('--backend', choices=BACKENDS, default='ollama',
                             help='Local inference runtime; defaults to Ollama')
        command.add_argument('--endpoint', default='',
                             help='Optional literal 127.0.0.1 or [::1] local API endpoint')
    run.add_argument('--max-repairs',type=int,default=2)
    run.add_argument('--allow-host-tests',action='store_true',help='Explicitly permit running repository code on this machine')
    run.add_argument('--resume',action='store_true',help='Resume an interrupted test-pending or repair-pending checkpoint')
    resume_all=sub.add_parser('resume-safe',help='Recover and resume eligible interrupted tasks (host execution opt-in required)')
    resume_all.add_argument('--workspace-parent',required=True)
    resume_all.add_argument('--allow-host-tests',action='store_true')
    resume_all.add_argument('--preset',choices=['python-unittest','python-pytest','npm-test','cargo-test','go-test','dotnet-test'],help='Optional safety override; must match each saved checkpoint')
    resume_all.add_argument('--max-repairs',type=int,help='Optional safety override; must match each saved checkpoint')
    resume_all.add_argument('--stream-report',action='store_true',
                            help='Emit one JSON event per task and a final count summary, with bounded result RAM')
    args=parser.parse_args(argv)
    if args.action=='gui':
        from .gui import launch
        return launch(args.db)
    if args.action == 'models':
        if args.backend == 'ollama':
            from .model_routing import installed_models
            if args.endpoint not in ('', 'http://127.0.0.1:11434'):
                raise ValueError('Ollama uses its fixed local endpoint')
            values = installed_models()
        else:
            values = _cli_model(backend=args.backend, model='auto',
                                  endpoint=args.endpoint).installed()
        print(json.dumps(values, indent=2))
        return 0
    if args.action == 'models-local':
        from .model_inventory import list_downloads
        print(json.dumps(list_downloads(directory=args.directory or None), indent=2))
        return 0
    if args.action == 'model-verify-local':
        from .model_inventory import verify_download
        result = verify_download(args.filename, directory=args.directory or None)
        print(json.dumps(result, indent=2))
        return 0
    if args.action == 'models-retired':
        from .model_retirement import list_retired
        print(json.dumps(list_retired(directory=args.directory or None), indent=2))
        return 0
    if args.action == 'model-retire-local':
        from .model_retirement import retire_download
        if not args.confirm:
            raise ValueError('Explicit --confirm required to retire GGUF')
        print(json.dumps(retire_download(
            args.filename, directory=args.directory or None), indent=2))
        return 0
    if args.action == 'model-restore-local':
        from .model_retirement import restore_download
        print(json.dumps(restore_download(
            args.identifier, directory=args.directory or None), indent=2))
        return 0
    if args.action == 'model-download':
        from .gguf_download import download_gguf
        result = download_gguf(
            args.repository, args.filename,
            directory=args.directory or None)
        print(json.dumps(result, indent=2))
        return 0
    if args.action == 'model-catalog':
        from .huggingface_catalog import search_gguf, list_gguf_files
        values = (search_gguf(args.query) if args.operation == 'search'
                  else list_gguf_files(args.query))
        print(json.dumps(values, indent=2))
        return 0
    if args.action == 'db-restore-copy':
        # Do not open or initialize the active task database for a restore copy.
        restored = Store.restore_backup_to(
            args.source, args.destination, manifest=args.manifest or None)
        print(f'Verified restore copy: {restored}')
        return 0
    if args.action == 'db-restore-activate':
        # Activation is an explicit offline operation; do not initialize the
        # active Store or create a missing --db path during this dispatch.
        activated = Store.activate_backup_to(
            args.source, args.destination, args.rollback,
            manifest=args.manifest or None, confirm=args.confirm)
        print(f'Activated database: {activated}')
        return 0
    if args.action == 'db-activation-status':
        status = Store.activation_status(args.destination)
        print(json.dumps(status, indent=2))
        return 0
    if args.action == 'db-activation-rollback':
        restored = Store.rollback_activation(
            args.destination, confirm=args.confirm,
            discard_staged=args.discard_staged)
        print(f'Rolled back database: {restored}')
        return 0
    if args.action == 'journal':
        store = Store(args.db)
        try:
            print(json.dumps(list(store.iter_events(
                task_id=args.task_id, limit=args.limit)), indent=2))
        finally:
            store.close()
        return 0
    if args.action == 'journal-check':
        store = Store(args.db)
        try:
            report = store.check_journal()
            print(json.dumps(report, indent=2))
            return 1 if report['issues'] else 0
        finally:
            store.close()
    if args.action == 'journal-export':
        store = Store(args.db)
        try:
            exported = store.export_events(args.destination, task_id=args.task_id)
        finally:
            store.close()
        print(f'Exported journal: {exported}')
        return 0
    if args.action == 'journal-handoff':
        store = Store(args.db)
        try:
            exported = store.export_handoff(
                args.destination, task_id=args.task_id,
                manifest=args.manifest or None)
        finally:
            store.close()
        print(f'Exported journal handoff: {exported}')
        return 0
    if args.action == 'journal-handoff-check':
        report = Store.check_handoff(
            args.source, manifest=args.manifest or None,
            evidence_directory=args.evidence_directory or None)
        print(json.dumps(report, indent=2))
        return 0 if report['valid'] else 1
    if args.action == 'worktree-manifest':
        from .worktree_manifest import export_worktree_manifest
        exported = export_worktree_manifest(args.root, args.destination)
        print(f'Exported worktree manifest: {exported}')
        return 0
    if args.action == 'worktree-manifest-check':
        from .worktree_manifest import check_worktree_manifest
        report = check_worktree_manifest(
            args.source, args.root, max_issues=args.max_issues)
        print(json.dumps(report, indent=2))
        return 0 if report['valid'] else 1
    if args.action == 'worktree-archive':
        from .worktree_archive import export_worktree_archive
        exported = export_worktree_archive(args.root, args.destination)
        print(f'Exported worktree archive: {exported}')
        return 0
    if args.action == 'worktree-archive-check':
        from .worktree_archive import check_worktree_archive
        report = check_worktree_archive(
            args.source, max_issues=args.max_issues)
        print(json.dumps(report, indent=2))
        return 0 if report['valid'] else 1
    if args.action == 'recovery-bundle-index':
        from .recovery_bundle import export_recovery_bundle_index
        exported = export_recovery_bundle_index(
            args.handoff, args.worktree_archive, args.destination,
            manifest=args.manifest or None,
            evidence_directory=args.evidence_directory or None,
            database=args.database or None,
            database_manifest=args.database_manifest or None)
        print(f'Exported recovery bundle index: {exported}')
        return 0
    if args.action == 'recovery-bundle-index-check':
        from .recovery_bundle import check_recovery_bundle_index
        report = check_recovery_bundle_index(
            args.source, args.handoff, args.worktree_archive,
            manifest=args.manifest or None,
            evidence_directory=args.evidence_directory or None,
            database=args.database or None,
            database_manifest=args.database_manifest or None,
            max_issues=args.max_issues)
        print(json.dumps(report, indent=2))
        return 0 if report['valid'] else 1
    if args.action == 'recovery-bundle':
        from .recovery_package import export_recovery_bundle
        exported = export_recovery_bundle(
            args.handoff, args.worktree_archive, args.destination,
            manifest=args.manifest or None,
            evidence_directory=args.evidence_directory or None,
            database=args.database or None,
            database_manifest=args.database_manifest or None)
        print(f'Exported recovery bundle: {exported}')
        return 0
    if args.action == 'recovery-bundle-db-restore-copy':
        from .recovery_database import restore_recovery_bundle_database_to
        authentication, key = _recovery_auth_gate(parser, args)
        restored = restore_recovery_bundle_database_to(
            args.source, args.destination,
            authentication=authentication, key=key)
        print(f'Restored recovery bundle database copy: {restored}')
        return 0
    if args.action == 'recovery-bundle-db-restore-activate':
        from .recovery_database import activate_recovery_bundle_database_to
        authentication, key = _recovery_auth_gate(parser, args)
        activated = activate_recovery_bundle_database_to(
            args.source, args.destination, args.rollback,
            authentication=authentication, key=key, confirm=args.confirm)
        print(f'Activated recovery bundle database: {activated}')
        return 0
    if args.action == 'recovery-bundle-check':
        authentication, key = _recovery_auth_gate(parser, args)
        if authentication is None:
            from .recovery_package import check_recovery_bundle
            report = check_recovery_bundle(
                args.source, max_issues=args.max_issues)
        else:
            from .recovery_auth import check_recovery_bundle_authenticated
            report = check_recovery_bundle_authenticated(
                args.source, authentication, key,
                max_issues=args.max_issues)
        print(json.dumps(report, indent=2))
        return 0 if report['valid'] else 1
    if args.action == 'recovery-bundle-extract':
        from .recovery_extract import extract_recovery_bundle
        authentication, key = _recovery_auth_gate(parser, args)
        extracted = extract_recovery_bundle(
            args.source, args.destination,
            authentication=authentication, key=key)
        print(f'Extracted recovery bundle: {extracted}')
        return 0
    if args.action == 'recovery-bundle-extract-check':
        from .recovery_extract import check_recovery_bundle_extraction
        report = check_recovery_bundle_extraction(
            args.source, max_issues=args.max_issues)
        print(json.dumps(report, indent=2))
        return 0 if report['valid'] else 1
    if args.action == 'recovery-bundle-extract-resume':
        from .recovery_extract import resume_recovery_bundle_extraction
        authentication, key = _recovery_auth_gate(parser, args)
        resumed = resume_recovery_bundle_extraction(
            args.source, args.destination,
            authentication=authentication, key=key)
        print(f'Resumed recovery bundle extraction: {resumed}')
        return 0
    if args.action == 'recovery-bundle-auth':
        from .recovery_auth import export_recovery_bundle_auth
        exported = export_recovery_bundle_auth(
            args.source, args.key, args.destination)
        print(f'Exported recovery bundle authentication: {exported}')
        return 0
    if args.action == 'recovery-bundle-auth-check':
        from .recovery_auth import check_recovery_bundle_auth
        report = check_recovery_bundle_auth(
            args.source, args.authentication, args.key)
        print(json.dumps(report, indent=2))
        return 0 if report['valid'] else 1
    if args.action == 'recovery-bundle-auth-keygen':
        from .recovery_auth import generate_recovery_bundle_auth_key
        generated = generate_recovery_bundle_auth_key(
            args.destination, size=args.key_bytes)
        print(f'Generated recovery authentication key: {generated}')
        return 0
    if args.action == 'memory':
        from .model_routing import memory_report
        print(json.dumps(memory_report(), indent=2))
        return 0
    if args.action == 'acceptance':
        from .acceptance import write_acceptance_snapshot
        print(json.dumps(write_acceptance_snapshot(), indent=2))
        return 0
    if args.action == 'diagnostics':
        if args.operation == 'show':
            from .diagnostics import EvidenceStore
            if args.reference is None:
                parser.error('diagnostics show requires a 64-character reference')
            print(EvidenceStore().get(args.reference),end='')
        else:
            from .optimizer_metrics import OptimizationMetrics
            print(json.dumps(OptimizationMetrics().stats(),indent=2))
        return 0
    if args.action == 'learning':
        from .error_learning import RepairMemory
        path=Path(args.db).expanduser().resolve().with_name('repair_memory.sqlite3')
        print(json.dumps(RepairMemory(path).stats(),indent=2))
        return 0
    if args.action == 'filters':
        from .output_rules import TrustedOutputRules
        profile = TrustedOutputRules(config=args.config)
        print(profile.status() if args.operation == 'status' else
              'Trusted filter SHA-256: ' + profile.trust_config())
        return 0
    if args.action in ('db-check', 'db-backup'):
        # Store.__init__ creates tables and even missing databases. Do not
        # initialize or migrate anything until a read-only preflight passes.
        Store.inspect_existing_database(args.db)
        if args.action == 'db-check':
            print('SQLite integrity: ok')
            return 0
    store=Store(args.db)
    try:
        if args.action=='add':
            print(f'Created task #{store.add(args.project,args.description)}')
        elif args.action=='list':
            # Stream one SQLite row at a time. Do not construct a giant
            # list of result/error payloads just to emit a JSON array.
            sys.stdout.write("[")
            first = True
            for task in store.iter_tasks():
                sys.stdout.write("\n" if first else ",\n")
                json.dump(task, sys.stdout)
                first = False
            sys.stdout.write("\n]\n" if not first else "]\n")
        elif args.action=='db-backup':
            backup = store.backup_to(
                args.destination, manifest=args.manifest or None)
            print(f'Verified backup: {backup}')
        elif args.action=='recover':
            print(f'Inspected {store.recover()} interrupted tasks; editing/testing tasks blocked for inspection')
        elif args.action=='recover-edits':
            from .transactions import recover_edits
            changed = recover_edits(Path(args.workspace))
            print('Recovered edit journal' if changed else 'No pending edit journal')
        elif args.action=='plan':
            from .task_actions import plan_task
            output = plan_task(store, args.id,
                               model_factory=lambda: _cli_model(backend=args.backend,
                                                                 model=args.model,
                                                                 endpoint=args.endpoint))
            print(output)
        elif args.action=='resume-safe':
            from .recovery import resume_safe
            sink = (lambda event: print(json.dumps(event, ensure_ascii=False), flush=True)
                    ) if args.stream_report else None
            report=resume_safe(store,Path(args.workspace_parent),preset=args.preset,
                               max_repairs=args.max_repairs,allow_host_tests=args.allow_host_tests,
                               on_event=sink,collect_results=not args.stream_report)
            if args.stream_report:
                print(json.dumps({"event": "summary", "interrupted": report["interrupted"],
                                  **report["counts"]}))
                if report["counts"]["failed"]:
                    return 1
            else:
                print(json.dumps(report,indent=2))
                if report["failed"]:
                    return 1
        elif args.action=='run':
            from .engine import execute_task
            result=execute_task(store,args.id,Path(args.workspace_parent),preset=args.preset,
                                model=_cli_model(backend=args.backend, model=args.model,
                                                   endpoint=args.endpoint),max_repairs=args.max_repairs,
                                allow_host_tests=args.allow_host_tests,resume=args.resume,
                                role=args.role,
                                qualification_directory=(Path(args.qualification_directory)
                                                         if args.qualification_directory else None))
            print(json.dumps(result,indent=2))
            if not result['passed']:
                return 1
        elif args.action=='test':
            from .task_actions import test_task
            result=test_task(store,args.id,args.preset,
                             allow_host_tests=args.allow_host_tests)
            print(json.dumps(result,indent=2))
            if not result['passed']:
                return 1
    finally:
        store.close()
    return 0

if __name__=='__main__':
    raise SystemExit(main())

