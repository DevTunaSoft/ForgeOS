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


def main(argv=None):
    parser=argparse.ArgumentParser(description='ForgeOS local-first development manager')
    parser.add_argument('--db',default=str(default_db()))
    sub=parser.add_subparsers(dest='action',required=True)
    sub.add_parser('gui'); sub.add_parser('list'); sub.add_parser('recover')
    sub.add_parser('memory', help='One-shot local ForgeOS RAM/working-set report')
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
    if args.action == 'memory':
        from .model_routing import memory_report
        print(json.dumps(memory_report(), indent=2))
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

