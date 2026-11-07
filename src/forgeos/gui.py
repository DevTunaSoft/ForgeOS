"""Dependency-free beginner-oriented Tk desktop UI."""
import threading
from queue import Empty, Queue
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from .core import Store, Ollama, run_test

PRESETS=('python-unittest','python-pytest','npm-test','cargo-test','go-test','dotnet-test')

def launch(db_path):
    store=Store(db_path)
    store.recover()
    root=tk.Tk(); root.title('ForgeOS — Development Manager (Prototype)'); root.geometry('960x690')
    frame=ttk.Frame(root,padding=16); frame.pack(fill='both',expand=True)
    ttk.Label(frame,text='ForgeOS',font=('Segoe UI',22,'bold')).pack(anchor='w')
    ttk.Label(frame,text='Local-first prototype • Planning and test execution only • No automatic code changes').pack(anchor='w')
    project=tk.StringVar(); model=tk.StringVar(value='qwen2.5-coder:3b'); preset=tk.StringVar(value='python-unittest')
    line=ttk.Frame(frame); line.pack(fill='x',pady=8)
    ttk.Entry(line,textvariable=project).pack(side='left',fill='x',expand=True)
    ttk.Button(line,text='Choose Git repository',command=lambda:project.set(filedialog.askdirectory())).pack(side='left',padx=5)
    ttk.Label(frame,text='Describe the task:').pack(anchor='w')
    description=tk.Text(frame,height=4); description.pack(fill='x')
    controls=ttk.Frame(frame); controls.pack(fill='x',pady=8)
    ttk.Label(controls,text='Model:').pack(side='left'); ttk.Entry(controls,textvariable=model,width=24).pack(side='left',padx=5)
    ttk.Label(controls,text='Test preset:').pack(side='left',padx=6)
    ttk.Combobox(controls,textvariable=preset,values=PRESETS,state='readonly',width=20).pack(side='left')
    tree=ttk.Treeview(frame,columns=('id','status','task'),show='headings',height=8)
    for col,width in [('id',60),('status',120),('task',640)]: tree.heading(col,text=col.title()); tree.column(col,width=width)
    tree.pack(fill='both',expand=True)
    ttk.Label(frame,text='Result / diagnostics:').pack(anchor='w')
    output=tk.Text(frame,height=9,wrap='word'); output.pack(fill='both',expand=True)
    busy={'value':False}
    events=Queue()
    closing={'value':False}
    def poll_events():
        # All Tk operations, including after(), are owned by the GUI thread.
        try:
            while True:
                task_id, value = events.get_nowait()
                busy['value']=False
                refresh()
                output.delete('1.0','end')
                output.insert('end',value)
        except Empty:
            pass
        if not closing['value']:
            root.after(100,poll_events)
    def refresh():
        tree.delete(*tree.get_children())
        for task in store.list(): tree.insert('', 'end', iid=str(task['id']),values=(task['id'],task['status'],task['description'][:100]))
    def selected():
        selection=tree.selection()
        if not selection: raise ValueError('Select a task first')
        return int(selection[0])
    def add():
        try:
            store.add(project.get(),description.get('1.0','end').strip()); refresh()
        except Exception as exc: messagebox.showerror('Cannot add task',str(exc))
    def background(operation):
        if busy['value']: return
        try: task_id=selected()
        except ValueError as exc: messagebox.showinfo('Select a task',str(exc)); return
        # Capture Tk variables before starting a worker: Tk is not thread-safe.
        chosen_model=model.get()
        chosen_preset=preset.get()
        busy['value']=True
        def worker():
            worker_store=Store(db_path)
            try:
                task=worker_store.get(task_id)
                if operation=='plan':
                    worker_store.update(task_id,'planning',increment=True)
                    value=Ollama(model=chosen_model).plan(task['description'])
                    worker_store.update(task_id,'planned',result=value)
                else:
                    worker_store.update(task_id,'testing',increment=True)
                    result=run_test(task['project'],chosen_preset)
                    value=result['output']
                    worker_store.update(task_id,'passed' if result['passed'] else 'failed',
                                 result=value,error='' if result['passed'] else value)
            except Exception as exc:
                value=f'{type(exc).__name__}: {exc}'
                worker_store.update(task_id,'blocked',error=value)
            finally:
                worker_store.close()
            events.put((task_id,value))
        threading.Thread(target=worker,daemon=True).start()
    buttons=ttk.Frame(frame); buttons.pack(fill='x',pady=8)
    ttk.Button(buttons,text='Add task',command=add).pack(side='left',padx=3)
    ttk.Button(buttons,text='Generate plan with Ollama',command=lambda:background('plan')).pack(side='left',padx=3)
    ttk.Button(buttons,text='Run selected test',command=lambda:background('test')).pack(side='left',padx=3)
    ttk.Button(buttons,text='Refresh',command=refresh).pack(side='left',padx=3)
    def close():
        if busy['value']:
            messagebox.showinfo('Task running','Wait for the current task to finish before closing.')
            return
        closing['value']=True
        store.close(); root.destroy()
    root.protocol('WM_DELETE_WINDOW',close); refresh(); poll_events(); root.mainloop()

