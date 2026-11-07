"""ForgeOS command-line entry point; GUI is optional."""
import argparse
import json
from pathlib import Path
from .core import Store, Ollama, run_test

def default_db():
    return Path.home()/'.forgeos'/'tasks.sqlite3'

def main(argv=None):
    parser=argparse.ArgumentParser(description='ForgeOS local-first development manager')
    parser.add_argument('--db',default=str(default_db()))
    sub=parser.add_subparsers(dest='action',required=True)
    sub.add_parser('gui'); sub.add_parser('list'); sub.add_parser('recover')
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
    run=sub.add_parser('run',help='Generate, test and repair edits in a Git worktree')
    run.add_argument('id',type=int)
    run.add_argument('--workspace-parent',required=True)
    run.add_argument('--preset',default='python-unittest',choices=['python-unittest','python-pytest','npm-test','cargo-test','go-test','dotnet-test'])
    run.add_argument('--model',default='qwen2.5-coder:3b')
    run.add_argument('--max-repairs',type=int,default=2)
    run.add_argument('--allow-host-tests',action='store_true',help='Explicitly permit running repository code on this machine')
    run.add_argument('--resume',action='store_true',help='Resume an interrupted test-pending or repair-pending checkpoint')
    resume_all=sub.add_parser('resume-safe',help='Recover and resume eligible interrupted tasks (host execution opt-in required)')
    resume_all.add_argument('--workspace-parent',required=True)
    resume_all.add_argument('--allow-host-tests',action='store_true')
    resume_all.add_argument('--preset',choices=['python-unittest','python-pytest','npm-test','cargo-test','go-test','dotnet-test'],help='Optional safety override; must match each saved checkpoint')
    resume_all.add_argument('--max-repairs',type=int,help='Optional safety override; must match each saved checkpoint')
    args=parser.parse_args(argv)
    if args.action=='gui':
        from .gui import launch
        return launch(args.db)
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
            print(json.dumps(store.list(),indent=2))
        elif args.action=='recover':
            print(f'Inspected {store.recover()} interrupted tasks; editing/testing tasks blocked for inspection')
        elif args.action=='recover-edits':
            from .transactions import recover_edits
            changed = recover_edits(Path(args.workspace))
            print('Recovered edit journal' if changed else 'No pending edit journal')
        elif args.action=='plan':
            task=store.get(args.id)
            store.update(args.id,'planning',increment=True)
            try:
                output=Ollama(model=args.model).plan(task['description'])
                store.update(args.id,'planned',result=output)
                print(output)
            except Exception as exc:
                store.update(args.id,'blocked',error=str(exc))
                raise
        elif args.action=='resume-safe':
            from .recovery import resume_safe
            report=resume_safe(store,Path(args.workspace_parent),preset=args.preset,
                               max_repairs=args.max_repairs,allow_host_tests=args.allow_host_tests)
            print(json.dumps(report,indent=2))
            if report['failed']:
                return 1
        elif args.action=='run':
            from .engine import execute_task
            result=execute_task(store,args.id,Path(args.workspace_parent),preset=args.preset,
                                model=Ollama(model=args.model),max_repairs=args.max_repairs,
                                allow_host_tests=args.allow_host_tests,resume=args.resume)
            print(json.dumps(result,indent=2))
            if not result['passed']:
                return 1
        elif args.action=='test':
            task=store.get(args.id)
            store.update(args.id,'testing',increment=True)
            result=run_test(task['project'],args.preset)
            store.update(args.id,'passed' if result['passed'] else 'failed',
                         error='' if result['passed'] else result['output'],result=json.dumps(result))
            print(json.dumps(result,indent=2))
            if not result['passed']:
                return 1
    finally:
        store.close()
    return 0

if __name__=='__main__':
    raise SystemExit(main())

