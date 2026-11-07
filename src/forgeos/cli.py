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
    args=parser.parse_args(argv)
    if args.action=='gui':
        from .gui import launch
        return launch(args.db)
    store=Store(args.db)
    try:
        if args.action=='add':
            print(f'Created task #{store.add(args.project,args.description)}')
        elif args.action=='list':
            print(json.dumps(store.list(),indent=2))
        elif args.action=='recover':
            print(f'Requeued {store.recover()} interrupted tasks')
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
        elif args.action=='run':
            from .engine import execute_task
            result=execute_task(store,args.id,Path(args.workspace_parent),preset=args.preset,
                                model=Ollama(model=args.model),max_repairs=args.max_repairs,
                                allow_host_tests=args.allow_host_tests)
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

