"""Review screens for installation/restore, plus batch/cache preferences."""
import copy,threading,tkinter as tk
from pathlib import Path
from tkinter import ttk,filedialog,messagebox
from installation import build_plan,apply_plan,review_restore,restore_backup,inside



def center_dialog(window,parent):
    """Keep modal reviews/preferences near the application that owns them."""
    window.update_idletasks()
    x=parent.winfo_rootx()+max(0,(parent.winfo_width()-window.winfo_width())//2)
    y=parent.winfo_rooty()+max(0,(parent.winfo_height()-window.winfo_height())//2)
    window.geometry(f'+{x}+{y}')


def run_job(app,kind,text,work,done):
    """Run filesystem work off the GUI thread and marshal its result to poll().
    """
    app.load_cancel.set(); app.pending_load=None; app.render_id+=1
    app.job_kind=kind; app.last_progress=None; app.set_busy(True); app.status.set(text)
    def worker():
        try: app.events.put(('workflow_result',(done,work(),None)))
        except Exception as error: app.events.put(('workflow_result',(done,None,str(error))))
    threading.Thread(target=worker,daemon=True).start()


def review_install(app):
    """Start a non-mutating review for the scanned installation and export.
    """
    if app.busy or not app.library: app.status.set('Scan your LaunchBox installation first.'); return
    if Path(app.launchbox.get()).resolve() not in (app.library.root,app.library.root.parent):
        app.status.set('The LaunchBox folder changed. Scan systems again.'); return
    library=app.library; output=Path(app.output.get()); platforms=app.selected() or list(library.platform_files)
    run_job(app,'review','Verifying generated replacements and matching installed images...',
            lambda:build_plan(library,output,platforms),lambda plan:ReviewWindow(app,plan,'install'))


def review_previous_backup(app):
    """Ask for a dated backup and inspect restore conflicts before any write.
    """
    if app.busy or not app.library: app.status.set('Scan your LaunchBox installation first.'); return
    if Path(app.launchbox.get()).resolve() not in (app.library.root,app.library.root.parent):
        app.status.set('The LaunchBox folder changed. Scan systems again.'); return
    folder=filedialog.askdirectory(parent=app.root,title='Choose a dated marquee backup folder containing restore-manifest.json')
    if not folder: return
    library=app.library
    run_job(app,'review','Checking backups and looking for newer artwork...',lambda:review_restore(library,folder),lambda plan:ReviewWindow(app,plan,'restore'))

class ReviewWindow:
    """Present explicit selected actions; never install merely by opening a review.

    Install stays disabled until a backup location is chosen for this review.
    The final button freezes selected ready rows and delegates revalidation
    and mutations to the worker. Blocked rows are never upgraded implicitly.
    """
    def __init__(self,app,plan,mode):
        self.app=app; self.plan=plan; self.mode=mode; self.backup_parent=None
        self.window=tk.Toplevel(app.root); self.window.title('Review marquee '+('installation' if mode=='install' else 'restore')); self.window.geometry('1050x650'); self.window.minsize(850,530); self.window.transient(app.root)
        self.window.protocol('WM_DELETE_WINDOW',self.close); self.window.bind('<Escape>',lambda event:self.close())
        frame=ttk.Frame(self.window,padding=14); frame.pack(fill='both',expand=True)
        ready=sum(e.get('ready',False) for e in plan['entries']); blocked=len(plan['entries'])-ready
        ttk.Label(frame,text=f'{ready} ready; {blocked} unchanged or blocked.',font=('Segoe UI',13,'bold')).pack(anchor='w')
        ttk.Label(frame,text='Select the rows to process (Ctrl/Shift selects several). Only ready rows can change. Close LaunchBox and Big Box before installing.').pack(anchor='w',pady=(4,8))
        table=ttk.Frame(frame); table.pack(fill='both',expand=True)
        self.tree=ttk.Treeview(table,columns=('system','game','status','older'),show='headings',selectmode='extended')
        for name,label,width in [('system','System',180),('game','Game',360),('status','Action',130),('older','Older images',90)]:
            self.tree.heading(name,text=label); self.tree.column(name,width=width,minwidth=70)
        self.tree.pack(side='left',fill='both',expand=True); scrollbar=ttk.Scrollbar(table,orient='vertical',command=self.tree.yview); scrollbar.pack(side='right',fill='y'); self.tree.configure(yscrollcommand=scrollbar.set)
        ready_ids=[]
        for n,entry in enumerate(plan['entries']):
            system=entry.get('platform','')
            if mode=='restore': system=plan['journal']['entries'][entry['number']].get('platform','')
            older=entry.get('older',[]) if mode=='install' else plan['journal']['entries'][entry['number']]['older']
            status='Ready' if entry.get('ready') else entry.get('status','Blocked')
            self.tree.insert('', 'end',iid=str(n),values=(system,entry.get('title',''),status,len(older)))
            if entry.get('ready'): ready_ids.append(str(n))
        self.tree.selection_set(ready_ids); self.tree.bind('<<TreeviewSelect>>',self.selected)
        self.details=tk.Text(frame,height=7,wrap='word',font=('Segoe UI',9)); self.details.pack(fill='x',pady=8); self.details.configure(state='disabled')
        ttk.Label(frame,text='Unnumbered title images get the next available number; existing numbered alternatives stay unchanged. Originals are also backed up.' if mode=='install' else 'Generated artwork is preserved in place before original filenames are restored.',wraplength=990).pack(anchor='w',pady=(0,5))
        self.location=tk.StringVar(value='Choose a backup folder outside LaunchBox before installing.' if mode=='install' else 'Restoration skips files changed since installation.')
        ttk.Label(frame,textvariable=self.location,wraplength=990).pack(anchor='w')
        buttons=ttk.Frame(frame); buttons.pack(fill='x',pady=(10,0))
        if mode=='install': ttk.Button(buttons,text='Choose backup folder...',command=self.choose_backup).pack(side='left')
        ttk.Button(buttons,text='Close',command=self.close).pack(side='right')
        self.action=ttk.Button(buttons,text='Install selected and back up' if mode=='install' else 'Restore selected images',command=self.apply,state='disabled')
        self.action.pack(side='right',padx=8); self.selected(); center_dialog(self.window,app.root); self.window.grab_set()

    def selected(self,event=None):
        entries=[self.plan['entries'][int(n)] for n in self.tree.selection()]
        ready=[e for e in entries if e.get('ready')]
        label='Back up and install selected' if self.mode=='install' else 'Restore selected images'
        self.action.configure(text=f'{label} ({len(ready)})',state='normal' if ready and (self.mode=='restore' or self.backup_parent) else 'disabled')
        text=[]
        for entry in entries[:3]:
            text.append(entry.get('title','')+': '+entry.get('reason',''))
            item=entry if self.mode=='install' else self.plan['journal']['entries'][entry['number']]
            if item.get('source'): text.append('Generated image: '+item['source'])
            if item.get('destination'): text.append('Marquee destination: '+item['destination'])
            generated=entry.get('generated_preserved') if self.mode=='restore' else None
            if generated: text.append('Preserve generated artwork as: '+generated)
            for old in item.get('older',[]):
                text.append('Original: '+old['path'])
                if old.get('preserved'): text.append('Rename in the same folder to: '+old['preserved'])
            retained=item.get('retained',[])
            if retained: text.append(f'Keep {len(retained)} other matching image(s) unchanged.')
        if len(entries)>3: text.append(f'...and {len(entries)-3} other selected rows.')
        self.details.configure(state='normal'); self.details.delete('1.0','end'); self.details.insert('1.0','\n'.join(text)); self.details.configure(state='disabled')

    def choose_backup(self):
        """Choose and validate a new backup location for this installation review.
        """
        folder=filedialog.askdirectory(parent=self.window,title='Choose where to create this installation backup')
        if not folder: return
        if inside(folder,self.app.library.root): messagebox.showerror('Choose another folder','Backups must be outside LaunchBox.',parent=self.window); return
        for entry in self.plan['entries']:
            if entry.get('ready') and (inside(folder,entry['folder']) or inside(folder,Path(entry['source']).parent)):
                messagebox.showerror('Choose another folder','Backups must be outside installed and generated marquee image folders.',parent=self.window); return
        self.backup_parent=folder; self.location.set('A dated backup will be created in: '+folder); self.selected()

    def close(self):
        self.window.grab_release(); self.window.destroy()

    def apply(self):
        chosen={int(n) for n in self.tree.selection()}; plan=copy.deepcopy(self.plan)
        for n,entry in enumerate(plan['entries']): entry['ready']=bool(entry.get('ready') and n in chosen)
        if not any(e['ready'] for e in plan['entries']): return
        app=self.app; library=app.library; output=app.output.get(); backup=self.backup_parent; mode=self.mode
        if mode=='install' and not backup: return
        self.close()
        def progress(done,total,title): app.events.put(('operation_progress',{'done':done,'total':total,'title':title,'mode':mode}))
        if mode=='install':
            run_job(app,'install','Backing up reviewed artwork before installation...',lambda:apply_plan(library,output,plan,backup,progress),
                    lambda path:app.status.set('Installation complete. Backup copies and restore manifest: '+str(path)))
        else:
            run_job(app,'restore','Restoring reviewed original artwork...',lambda:restore_backup(library,plan,progress),
                    lambda count:app.status.set(f'Restored {count} reviewed game entries. Conflicts were left unchanged.'))


def preferences(app):
    """Edit validated checkpoint/cache budgets; changes affect future work.
    """
    if app.busy: return
    window=tk.Toplevel(app.root); window.title('Batch and memory settings'); window.transient(app.root); window.resizable(False,False)
    frame=ttk.Frame(window,padding=16); frame.pack(fill='both',expand=True)
    values={}
    for row,(key,label,low,high) in enumerate([('checkpoint_games','Save progress every (games)',1,10000),('source_cache_mb','Artwork memory cache (MB)',16,1024),('layer_cache_mb','Live preview memory cache (MB)',16,512)]):
        ttk.Label(frame,text=label).grid(row=row,column=0,sticky='w',padx=(0,16),pady=5)
        variable=tk.StringVar(window,value=str(app.options[key])); values[key]=(variable,low,high)
        ttk.Spinbox(frame,from_=low,to=high,textvariable=variable,width=8).grid(row=row,column=1)
    ttk.Label(frame,text='Progress is also saved after a completed game when 30 seconds have passed,\nat system boundaries, and on Stop. Working images need extra memory.').grid(row=3,column=0,columnspan=2,sticky='w',pady=10)
    message=tk.StringVar(window); ttk.Label(frame,textvariable=message,foreground='#A00000').grid(row=4,column=0,columnspan=2,sticky='w')
    def close(): window.grab_release(); window.destroy()
    def apply():
        try:
            chosen={key:int(variable.get()) for key,(variable,low,high) in values.items()}
            if any(not low<=chosen[key]<=high for key,(_,low,high) in values.items()): raise ValueError()
        except ValueError: message.set('Enter whole numbers within the shown ranges.'); return
        app.options.update(chosen); app.apply_memory_settings(); app.save(); close()
    buttons=ttk.Frame(frame); buttons.grid(row=5,column=0,columnspan=2,sticky='e')
    ttk.Button(buttons,text='Apply',command=apply).pack(side='left',padx=6); ttk.Button(buttons,text='Cancel',command=close).pack(side='left')
    window.protocol('WM_DELETE_WINDOW',close); window.bind('<Escape>',lambda event:close()); center_dialog(window,app.root); window.grab_set()
