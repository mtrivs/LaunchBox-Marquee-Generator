"""Desktop controls and cached, asynchronous random-game live preview."""
import copy,json,os,queue,threading,tkinter as tk
from pathlib import Path
from tkinter import ttk,filedialog,messagebox
from PIL import Image,ImageTk,ImageDraw
from marquee import HOME,Library,load_profiles,profile_for,generate,render,select_sources,crop_percentage,page_color_for,page_back_color_for,shade_color,PreviewLayerCache,DECODED_CACHE
from image_cache import ImageCache
from system_list import SystemList,Tooltip
from logo_packs import packs,AUTO,find_logo
from color_picker import ColorPicker,FIELDS,hex_color
from live_preview import PreviewMediaIndex,PreviewCancelled,random_game

class App:
    """Own Tk widgets and coordinate asynchronous preview/batch workflows.

    All Tk calls run on the main thread. Workers communicate through events;
    preview revisions suppress stale results. Batch progress is authoritative
    completed-work data rather than an elapsed-time estimate.
    """
    def __init__(self,root):
        self.root=root; root.title('Marquee Generator 2.4.1'); root.geometry('1120x890'); root.minsize(1040,820)
        self.events=queue.Queue(); self.cancelled=threading.Event(); self.library=None; self.busy=False; self.photo=None
        self.context=None; self.loading=False; self.rendering=False; self.load_id=0; self.render_id=0
        self.pending_load=None; self.pending_render=False; self.render_after=None; self.load_cancel=threading.Event()
        self.game_cache={}; self.folder_cache={}; self.requested_platform=None; self.closed=False; self.job_kind=None; self.last_progress=None
        self.profiles=load_profiles(); self.picker=None; self.invalid_colors=set(); self.color_sync=False; self.save_after=None
        self.settings=HOME/'settings.json'
        try: settings=json.loads(self.settings.read_text(encoding='utf-8'))
        except (OSError,ValueError): settings={}
        if not isinstance(settings,dict): settings={}
        try: initial_crop=crop_percentage(settings.get('background_position',50))
        except ValueError: initial_crop=50
        self.launchbox=tk.StringVar(value=settings.get('launchbox',''))
        self.output=tk.StringVar(value=settings.get('output',str(HOME/'Generated')))
        self.palettes={}
        stored=settings.get('palettes',{})
        if isinstance(stored,dict):
            for platform,values in stored.items():
                if not isinstance(values,dict): continue
                for field in ['main','page_color','page_back_color']:
                    try: value=hex_color(values[field])
                    except (KeyError,ValueError): continue
                    self.palettes.setdefault(platform,{})[field]=value
        custom=settings.get('custom_colors',[]); self.custom_colors=[]
        if isinstance(custom,list):
            for value in custom:
                try: value=hex_color(value)
                except ValueError: continue
                if value not in self.custom_colors: self.custom_colors.append(value)
        self.custom_colors=self.custom_colors[:12]
        self.options={'checkpoint_games':25,'source_cache_mb':128,'layer_cache_mb':64}
        stored_options=settings.get('options',{})
        if isinstance(stored_options,dict):
            for key,low,high in [('checkpoint_games',1,10000),('source_cache_mb',16,1024),('layer_cache_mb',16,512)]:
                value=stored_options.get(key)
                if type(value) is int and low<=value<=high: self.options[key]=value
        self.logo_choices=settings.get('logo_choices',{})
        if not isinstance(self.logo_choices,dict): self.logo_choices={}
        self.logo_choices={k:v for k,v in self.logo_choices.items() if isinstance(v,str)}
        self.available_packs={}; self.logo_var=tk.StringVar(root,value=AUTO)
        DECODED_CACHE.set_budget(self.options['source_cache_mb']*1024*1024)
        self.preview_layers=PreviewLayerCache(self.options['layer_cache_mb']*1024*1024)

        self.crop_text=tk.StringVar(value=f'{initial_crop:g}'); self.crop_scale_value=tk.DoubleVar(value=initial_crop)
        self.title_filter=tk.StringVar(); self.rebuild=tk.BooleanVar(value=False)
        self.status=tk.StringVar(value='Choose your LaunchBox folder and scan systems.')
        self.preview_title=tk.StringVar(value='Click a system name to preview its artwork.')
        self.export_status=tk.StringVar(value='Output folder: '+self.output.get())
        self.output.trace_add('write',lambda *args:self.export_status.set('Output folder: '+self.output.get()))
        self.checked_count=tk.StringVar(value='0 systems checked')
        outer=ttk.Frame(root,padding=10); outer.pack(fill='both',expand=True)
        ttk.Label(outer,text='Marquee Generator',font=('Segoe UI',20,'bold')).pack(anchor='w')
        ttk.Label(outer,text='1920 x 360 artwork from existing media - hidden and explicitly marked non-game entries excluded').pack(anchor='w',pady=(0,8))
        self.path_row(outer,'LaunchBox folder',self.launchbox,self.browse_library)
        self.path_row(outer,'Save artwork to',self.output,self.browse_output)
        controls=ttk.Frame(outer); controls.pack(fill='x',pady=6)
        self.scan_button=ttk.Button(controls,text='Scan systems',command=self.scan); self.scan_button.pack(side='left')
        self.settings_button=ttk.Button(controls,text='Batch settings...',command=self.preferences); self.settings_button.pack(side='right')
        ttk.Label(controls,text='Click a system name to preview. Check systems to generate their artwork.').pack(side='left',padx=12)
        split=ttk.Frame(outer); split.pack(fill='both',expand=True)
        left=ttk.Frame(split); left.pack(side='left',fill='both',expand=True)
        ttk.Label(left,text='Systems').pack(anchor='w')
        self.systems=SystemList(left,on_preview=self.selection_changed,on_checked=self.checked_changed)
        self.systems.pack(fill='both',expand=True,pady=4)
        row=ttk.Frame(left); row.pack(fill='x')
        self.select_all_button=ttk.Button(row,text='Check all',command=self.select_all); self.select_all_button.pack(side='left')
        self.clear_button=ttk.Button(row,text='Uncheck all',command=self.clear_selection); self.clear_button.pack(side='left',padx=6)
        right=ttk.LabelFrame(split,text='Live adjustments',padding=8); right.pack(side='right',fill='y',padx=(16,0))
        self.adjustments=ttk.Notebook(right); self.adjustments.pack(fill='both',expand=True)
        self.color_tab=ttk.Frame(self.adjustments,padding=4); self.image_tab=ttk.Frame(self.adjustments,padding=4)
        self.adjustments.add(self.color_tab,text='Colors'); self.adjustments.add(self.image_tab,text='Background & logo')
        right=self.image_tab
        ttk.Label(right,text='Background crop - global').pack(anchor='w')
        crop_row=ttk.Frame(right); crop_row.pack(fill='x',pady=(4,0))
        self.crop_slider=ttk.Scale(crop_row,from_=0,to=100,variable=self.crop_scale_value,command=self.crop_from_slider,length=190)
        self.crop_slider.pack(side='left',fill='x',expand=True)
        self.crop_box=ttk.Spinbox(crop_row,from_=0,to=100,increment=1,textvariable=self.crop_text,width=5,command=self.crop_from_text)
        self.crop_box.pack(side='left',padx=(8,0)); ttk.Label(crop_row,text='%').pack(side='left')
        self.crop_box.bind('<KeyRelease>',self.crop_from_text); self.crop_box.bind('<FocusOut>',self.crop_from_text)
        self.crop_box.bind('<Return>',self.crop_from_text)
        labels=ttk.Frame(right); labels.pack(fill='x')
        ttk.Label(labels,text='0 top       50 center       100 bottom').pack(side='left')
        self.center_button=ttk.Button(labels,text='Center',command=lambda:self.set_crop(50)); self.center_button.pack(side='right',padx=(6,0))
        ttk.Label(right,text='Game title contains (Enter to apply)').pack(anchor='w',pady=(8,2))
        self.filter_entry=ttk.Entry(right,textvariable=self.title_filter,width=35); self.filter_entry.pack(fill='x')
        self.filter_entry.bind('<Return>',lambda event:self.request_random())
        ttk.Label(right,text='Console logo source').pack(anchor='w',pady=(7,2))
        self.logo_picker=ttk.Combobox(right,textvariable=self.logo_var,values=[AUTO],state='disabled',width=35)
        self.logo_picker.pack(fill='x'); self.logo_picker.bind('<<ComboboxSelected>>',self.logo_changed)
        right=self.color_tab
        self.palette_label=ttk.Label(right,text='Click a system name to edit its corner colors.',wraplength=300)
        self.palette_label.pack(anchor='w',pady=(8,3))
        colors=ttk.Frame(right); colors.pack(fill='x'); self.color_buttons=[]; self.hex_entries={}; self.hex_vars={}; self.swatches={}
        ttk.Style(root).configure('InvalidColor.TEntry',fieldbackground='#FFE1E1',foreground='#A00000')
        for row,(label,field) in enumerate(FIELDS):
            ttk.Label(colors,text=label,width=8).grid(row=row,column=0,sticky='w',pady=3)
            button=tk.Button(colors,bg='#444444',activebackground='#444444',width=3,height=1,relief='solid',borderwidth=1,
                             command=lambda field=field:self.choose_color(field),state='disabled',cursor='hand2')
            button.grid(row=row,column=1,padx=(0,10),pady=3); self.color_buttons.append(button); self.swatches[field]=button
            value=tk.StringVar(root); self.hex_vars[field]=value
            entry=ttk.Entry(colors,textvariable=value,width=10,font=('Consolas',10),state='disabled')
            entry.grid(row=row,column=2,sticky='w',pady=3); self.hex_entries[field]=entry
            value.trace_add('write',lambda *args,field=field:self.hex_changed(field))
            entry.bind('<Return>',lambda event,field=field:self.finish_hex(field))
            entry.bind('<FocusOut>',lambda event,field=field:self.finish_hex(field))
        ttk.Label(colors,text='Click a square to mix a color, or enter #RRGGBB.',wraplength=300).grid(row=3,column=0,columnspan=3,sticky='w',pady=(2,0))
        self.reset_palette_button=ttk.Button(colors,text='Reset console colors',command=self.reset_palette,state='disabled')
        self.reset_palette_button.grid(row=4,column=0,columnspan=3,sticky='w',pady=(4,0))
        self.preview_header=ttk.Frame(outer); self.preview_header.pack(fill='x',pady=(8,0))
        ttk.Label(self.preview_header,textvariable=self.preview_title,font=('Segoe UI',10,'bold'),wraplength=850).pack(side='left',fill='x',expand=True)
        self.preview_frame=ttk.Frame(outer)
        self.preview_frame.pack(fill='x',pady=4)
        self.preview=ttk.Label(self.preview_frame,text='Live previews do not write files.',anchor='center')
        self.preview.pack(fill='x')
        # Reserve the full preview height so asynchronous loading never shifts
        # the generation controls. Refresh lives in a separate header so no
        # controls can cover the artwork during color/crop fine tuning.
        self.preview.configure(padding=(0,75))
        refresh=Image.new('RGBA',(20,20)); draw=ImageDraw.Draw(refresh)
        draw.arc((3,3,17,17),40,310,fill='#24597B',width=2)
        draw.polygon([(17,1),(17,8),(11,5)],fill='#24597B')
        self.refresh_icon=ImageTk.PhotoImage(refresh,master=root)
        self.random_button=ttk.Button(self.preview_header,text='New preview',image=self.refresh_icon,compound='left',command=self.request_random,state='disabled')
        self.random_button.pack(side='right',padx=(10,0))
        Tooltip(self.random_button,'Preview another game — no artwork files are saved')
        actions=ttk.Frame(outer); actions.pack(fill='x',pady=(7,3))
        self.generate_button=ttk.Button(actions,text='Generate checked systems',command=self.start_generation,state='disabled')
        self.generate_button.pack(side='left')
        ttk.Label(actions,textvariable=self.checked_count).pack(side='left',padx=10)
        self.restore_button=ttk.Button(actions,text='Review restore...',command=self.review_restore); self.restore_button.pack(side='right')
        self.install_button=ttk.Button(actions,text='Review installation...',command=self.review_install); self.install_button.pack(side='right',padx=8)
        options=ttk.Frame(outer); options.pack(fill='x',pady=(0,5))
        self.rebuild_check=ttk.Checkbutton(options,text='Regenerate existing exports',variable=self.rebuild)
        self.rebuild_check.pack(side='left')
        ttk.Label(options,text='Replaces generated files in the export folder only.').pack(side='left',padx=10)
        self.progress_text=tk.StringVar(root,value='Overall progress: ready')
        self.system_progress_text=tk.StringVar(root,value='System progress: ready')
        self.progress_panel=ttk.Frame(outer); self.progress_panel.pack(fill='x')
        self.progress_panel.columnconfigure(0,weight=1)
        bars=ttk.Frame(self.progress_panel); bars.grid(row=0,column=0,sticky='ew')
        self.progress_label=ttk.Label(bars,textvariable=self.progress_text); self.progress_label.pack(fill='x')
        self.progress=ttk.Progressbar(bars,mode='indeterminate'); self.progress.pack(fill='x',pady=(3,2))
        self.system_progress_frame=ttk.Frame(bars); self.system_progress_frame.pack(fill='x')
        ttk.Label(self.system_progress_frame,textvariable=self.system_progress_text).pack(fill='x')
        self.system_progress=ttk.Progressbar(self.system_progress_frame,mode='determinate',maximum=100)
        self.system_progress.pack(fill='x',pady=(2,3))
        self.stop_icon=tk.PhotoImage(master=root,width=14,height=14)
        self.stop_icon.put('#BF3434',to=(2,2,12,12))
        self.stop_button=ttk.Button(self.progress_panel,text='Stop Processing',image=self.stop_icon,compound='left',
                                    command=self.stop_processing,state='disabled')
        self.stop_button.grid(row=0,column=1,padx=(12,0))
        Tooltip(self.stop_button,'Finish the current game, save progress, then stop')
        self.status_label=ttk.Label(outer,textvariable=self.status,wraplength=1000); self.status_label.pack(fill='x',pady=(3,0))
        footer=ttk.Frame(outer); footer.pack(fill='x',pady=(5,0))
        ttk.Button(footer,text='Open output folder',command=self.open_output).pack(side='right')
        ttk.Label(footer,textvariable=self.export_status,wraplength=920).pack(side='left',fill='x',expand=True)
        self.poll_after=root.after(80,self.poll); root.protocol('WM_DELETE_WINDOW',self.close)

    def path_row(self,parent,label,variable,command):
        row=ttk.Frame(parent); row.pack(fill='x',pady=3)
        ttk.Label(row,text=label,width=17).pack(side='left')
        ttk.Entry(row,textvariable=variable).pack(side='left',fill='x',expand=True)
        ttk.Button(row,text='Browse...',command=command).pack(side='left',padx=(8,0))

    def browse_library(self):
        path=filedialog.askdirectory(title='Select the LaunchBox installation folder')
        if path: self.launchbox.set(path)

    def browse_output(self):
        path=filedialog.askdirectory(title='Select a separate output folder')
        if path: self.output.set(path)

    def save(self):
        if self.picker: return
        if self.save_after: self.root.after_cancel(self.save_after)
        self.save_after=self.root.after(350,self.flush_settings)

    def flush_settings(self):
        if self.save_after: self.root.after_cancel(self.save_after); self.save_after=None
        values={'launchbox':self.launchbox.get(),'output':self.output.get(),'palettes':self.palettes,
                'background_position':self.crop_scale_value.get(),'custom_colors':self.custom_colors,'logo_choices':self.logo_choices,'options':self.options}
        try:
            temporary=self.settings.with_suffix('.tmp')
            temporary.write_text(json.dumps(values,indent=2),encoding='utf-8'); temporary.replace(self.settings)
        except OSError as error: self.status.set(f'Could not save preferences: {error}')

    def selected(self): return self.systems.checked()

    def preview_selection(self):
        """Return the preview focus independently of checked batch systems."""
        platform=self.systems.active_system()
        return [platform] if platform else []

    def select_all(self):
        self.systems.select_set(0,'end'); self.checked_changed()

    def clear_selection(self):
        self.systems.selection_clear(0,'end'); self.checked_changed()

    def checked_changed(self):
        """Update batch choices without discarding a preview/color edit."""
        focused=self.systems.active_system()
        if focused and self.requested_platform!=focused: self.selection_changed()
        else: self.update_activity()

    def set_busy(self,value):
        self.busy=value
        if not value: self.job_kind=None
        state='disabled' if value else 'normal'
        for widget in [self.scan_button,self.generate_button,self.crop_slider,self.crop_box,self.center_button,self.filter_entry,self.settings_button,self.install_button,self.restore_button,self.select_all_button,self.clear_button,self.rebuild_check]+self.color_buttons:
            widget.configure(state=state)
        self.systems.set_enabled(not value)
        self.stop_button.configure(text='Stop Processing',state='normal' if value and self.job_kind=='batch' else 'disabled')
        self.show_palette()
        self.update_activity()

    def update_activity(self):
        """Animate uncounted work while keeping both progress bars visible.

        Current-system counts apply to batch generation. Outside a batch the
        second bar stays at zero with a waiting/ready label, not a hidden row.
        """
        active=self.busy or self.loading or self.rendering
        measured=self.last_progress is not None and self.last_progress.get('phase')!='preparing'
        if (self.busy and measured) or (not active and measured):
            # ttk.stop() resets its value to zero, even in determinate mode.
            # Counted progress has already stopped the animation before setting
            # its value, so preserve that value through later activity events.
            pass
        elif active:
            self.progress.configure(mode='indeterminate'); self.progress.start(15)
            self.system_progress.configure(mode='determinate',value=0)
            self.system_progress_text.set('System progress: waiting for game totals' if self.job_kind=='batch' else 'System progress: waiting for generation')
            self.progress_text.set('Overall progress: preparing game totals...' if self.job_kind=='batch' else 'Overall progress: working...' if self.busy else 'Overall progress: generating live preview...')
        else:
            self.progress.stop(); self.progress.configure(mode='determinate',maximum=100,value=0)
            self.system_progress.configure(mode='determinate',value=0)
            self.progress_text.set('Overall progress: ready'); self.system_progress_text.set('System progress: ready')
        self.random_button.configure(state='normal' if not self.busy and not self.loading and self.library and self.preview_selection() else 'disabled')
        checked=len(self.selected()); self.checked_count.set(f'{checked} system{"s" if checked!=1 else ""} checked')
        self.generate_button.configure(state='normal' if checked and self.library and not self.busy else 'disabled')

    def show_batch_progress(self,info):
        """Display independent overall and per-system completion percentages.

        An empty system is a completed zero-work system. A stopped batch retains
        its actual fraction; it must never be promoted to 100 percent.
        """
        self.last_progress=info
        if info['phase']=='preparing': self.update_activity(); return
        done,total=info['overall_done'],info['overall_total']; percent=100*done/total if total else (100 if info['phase']=='complete' else 0)
        self.progress.stop(); self.progress.configure(mode='determinate',maximum=100,value=percent)
        self.progress_text.set(f'Overall progress: {done:,} / {total:,} games processed ({percent:.1f}%) - system {info["system_index"]} of {info["system_count"]}')
        current,count=info['system_done'],info['system_total']; fraction=100*current/count if count else (100 if info['platform'] else 0)
        self.system_progress.configure(value=fraction)
        self.system_progress_text.set(f'System progress - {info["platform"] or "Waiting"}: {current:,} / {count:,} processed ({fraction:.1f}%)')
        if not self.system_progress_frame.winfo_manager(): self.system_progress_frame.pack(fill='x')
        if info.get('game_title') and info['phase']=='batch':
            self.status.set(f'Now generating: {info["platform"]} - {info["game_title"]}')

    def show_operation_progress(self,info):
        """Display counted install/restore progress after the preparation phase.
        """
        percent=100*info['done']/info['total'] if info['total'] else 100
        self.last_progress={'phase':info['mode']}; self.progress.stop(); self.progress.configure(mode='determinate',maximum=100,value=percent)
        self.system_progress.configure(mode='determinate',value=0)
        self.system_progress_text.set('System progress: waiting for generation')
        self.progress_text.set(f'Overall progress - {info["mode"].capitalize()}: {info["done"]} / {info["total"]} games ({percent:.1f}%) - {info["title"]}')

    def stop_processing(self):
        """Request a cooperative stop without interrupting the current PNG write."""
        if not self.busy or self.job_kind!='batch': return
        self.cancelled.set(); self.stop_button.configure(text='Stopping...',state='disabled')

    def preferences(self):
        from workflow_ui import preferences
        preferences(self)

    def review_install(self):
        from workflow_ui import review_install
        review_install(self)

    def review_restore(self):
        from workflow_ui import review_previous_backup
        review_previous_backup(self)

    def apply_memory_settings(self):
        """Replace per-preview caches and enforce the shared source budget.
        """
        DECODED_CACHE.set_budget(self.options['source_cache_mb']*1024*1024)
        self.preview_layers=PreviewLayerCache(self.options['layer_cache_mb']*1024*1024)
        if self.context:
            updated=dict(self.context); updated['image_cache']=ImageCache(self.options['source_cache_mb']*1024*1024)
            updated['layer_cache']=self.preview_layers; updated['cache_namespace']=object(); self.context=updated
            self.schedule_render()

    def logo_changed(self,event=None):
        """Resolve a selected pack off-thread without changing the preview game."""
        if self.busy or len(self.preview_selection())!=1 or not self.library: return
        platform=self.preview_selection()[0]; value=self.logo_var.get()
        choice='auto' if value==AUTO else value; self.logo_choices[platform]=choice
        context=self.context; library=self.library
        # Never mutate sources or caches owned by an in-flight render. A result
        # is applied to a replacement context only if this selection is current.
        def worker():
            try: self.events.put(('logo_loaded',(context,platform,choice,find_logo(library,platform,choice),None)))
            except Exception as error: self.events.put(('logo_loaded',(context,platform,choice,None,str(error))))
        threading.Thread(target=worker,daemon=True).start(); self.save()

    def scan(self):
        """Reload library metadata and discover installed logo packs off-thread.
        """
        if self.busy: return
        self.load_cancel.set(); self.pending_load=None; self.load_id+=1; self.render_id+=1; self.context=None
        self.job_kind='scan'; self.last_progress=None; self.set_busy(True); self.status.set('Reading LaunchBox system folders...'); path=self.launchbox.get()
        def worker():
            try:
                library=Library(path); library.discovered_logo_packs=packs(library); self.events.put(('library',library))
            except Exception as error: self.events.put(('error',str(error)))
        threading.Thread(target=worker,daemon=True).start()

    def selection_changed(self,event=None):
        self.invalid_colors.clear()
        for entry in self.hex_entries.values(): entry.configure(style='TEntry')
        self.show_palette(); selected=self.preview_selection()
        if len(selected)==1 and self.library and not self.busy:
            if self.requested_platform!=selected[0] or (self.context is None and not self.loading): self.request_random()
        else:
            self.load_cancel.set(); self.pending_load=None; self.load_id+=1; self.render_id+=1; self.context=None; self.requested_platform=None
            self.preview_title.set('Click a system name to preview its artwork.')
        self.update_activity()

    def palette_profile(self,platform):
        profile=dict(profile_for(self.library,platform,self.profiles)); profile.update(self.palettes.get(platform,{})); return profile

    def show_palette(self,preserve_field=None):
        selected=self.preview_selection(); enabled=len(selected)==1 and self.library is not None and not self.busy
        self.palette_label.configure(text=selected[0] if enabled else 'Click a system name to edit its corner colors.')
        profile=self.palette_profile(selected[0]) if enabled else None
        values={'main':profile['main'],'page_color':page_color_for(profile),'page_back_color':page_back_color_for(profile)} if profile else {}
        self.reset_palette_button.configure(state='normal' if enabled else 'disabled')
        self.logo_picker.configure(state='readonly' if enabled else 'disabled',values=[AUTO]+list(self.available_packs))
        choice=self.logo_choices.get(selected[0],'auto') if enabled else 'auto'
        self.logo_var.set(AUTO if choice=='auto' else choice)
        self.color_sync=True
        try:
            for label,field in FIELDS:
                self.swatches[field].configure(state='normal' if enabled else 'disabled')
                self.hex_entries[field].configure(state='normal' if enabled else 'disabled')
                if profile:
                    value=values[field]
                    if len(value)==9: value='#'+value[3:]
                    value=hex_color(value); self.swatches[field].configure(bg=value,activebackground=value)
                    if field!=preserve_field and field not in self.invalid_colors: self.hex_vars[field].set(value)
                elif not self.busy:
                    self.hex_vars[field].set(''); self.swatches[field].configure(bg='#444444',activebackground='#444444')
        finally: self.color_sync=False

    def reset_palette(self):
        if self.busy or self.picker or not self.library or len(self.preview_selection())!=1: return
        self.palettes.pop(self.preview_selection()[0],None); self.invalid_colors.clear()
        for entry in self.hex_entries.values(): entry.configure(style='TEntry')
        self.show_palette(); self.schedule_render(); self.save()

    def colors_valid(self):
        if self.invalid_colors:
            self.status.set('Enter a complete HEX color (#RRGGBB) before previewing or generating.'); return False
        return True

    def apply_color(self,platform,field,value,persist=True,preserve_field=None):
        value=hex_color(value); overrides=self.palettes.setdefault(platform,{}); overrides[field]=value
        if field=='main': overrides.update(page_color=value,page_back_color=shade_color(value))
        elif field=='page_color': overrides['page_back_color']=shade_color(value)
        self.show_palette(preserve_field); self.schedule_render()
        if persist: self.save()

    def hex_changed(self,field):
        if self.color_sync or self.busy or not self.library or len(self.preview_selection())!=1: return
        try: value=hex_color(self.hex_vars[field].get())
        except ValueError:
            self.invalid_colors.add(field); self.hex_entries[field].configure(style='InvalidColor.TEntry')
            self.render_id+=1
            if self.render_after: self.root.after_cancel(self.render_after); self.render_after=None
            self.colors_valid(); return
        self.invalid_colors.discard(field); self.hex_entries[field].configure(style='TEntry')
        self.apply_color(self.preview_selection()[0],field,value,preserve_field=field)

    def finish_hex(self,field):
        if field in self.invalid_colors: return
        self.show_palette()

    def choose_color(self,field):
        if self.busy or self.picker or len(self.preview_selection())!=1 or not self.library or not self.colors_valid(): return
        platform=self.preview_selection()[0]; profile=self.palette_profile(platform)
        initial=page_color_for(profile) if field=='page_color' else page_back_color_for(profile) if field=='page_back_color' else profile[field]
        if len(initial)==9: initial='#'+initial[3:]
        self.flush_settings()
        existed=platform in self.palettes; original=copy.deepcopy(self.palettes.get(platform,{}))
        def changed(value): self.apply_color(platform,field,value,persist=False)
        def apply(value):
            self.picker=None; self.apply_color(platform,field,value,persist=False)
            self.custom_colors=[value]+[c for c in self.custom_colors if c!=value]; self.custom_colors=self.custom_colors[:12]; self.save()
        def cancel():
            self.picker=None
            if existed: self.palettes[platform]=original
            else: self.palettes.pop(platform,None)
            self.show_palette(); self.schedule_render(); self.save()
        self.picker=ColorPicker(self.root,initial,changed,apply,cancel,
                                title=platform+' - '+dict((field,label) for label,field in FIELDS)[field],custom_colors=self.custom_colors)

    def set_crop(self,value):
        value=crop_percentage(value); self.crop_scale_value.set(value); self.crop_text.set(f'{value:g}'); self.save(); self.schedule_render()

    def crop_from_slider(self,value):
        if not self.busy: self.set_crop(round(float(value),1))

    def crop_from_text(self,event=None):
        if self.busy: return
        try: value=crop_percentage(self.crop_text.get())
        except ValueError:
            self.render_id+=1
            if self.render_after: self.root.after_cancel(self.render_after); self.render_after=None
            self.status.set('Background crop: enter a number from 0 to 100.'); return
        self.crop_scale_value.set(value); self.save(); self.schedule_render()

    def request_random(self):
        if self.busy or not self.library or len(self.preview_selection())!=1 or not self.colors_valid(): return
        if Path(self.launchbox.get()).resolve() not in [self.library.root,self.library.root.parent]:
            self.status.set('The LaunchBox folder changed. Scan systems again.'); return
        platform=self.preview_selection()[0]; previous=self.context['game'].id if self.context and self.context['game'].platform.casefold()==platform.casefold() else None
        self.load_cancel.set(); self.load_cancel=threading.Event(); self.load_id+=1; self.render_id+=1
        self.context=None; self.requested_platform=platform
        self.pending_load=(self.load_id,self.library,platform,self.title_filter.get(),previous,self.load_cancel)
        self.preview_title.set(f'{platform} - loading a random game...'); self.status.set('Finding artwork for the selected game...')
        self.run_pending_load()

    def run_pending_load(self):
        if self.loading or not self.pending_load or self.busy: return
        job,self.pending_load=self.pending_load,None
        job_id,library,platform,title,previous,cancel=job
        games_cache,folders_cache,profiles=self.game_cache,self.folder_cache,self.profiles
        logo_choice=self.logo_choices.get(platform,'auto'); source_budget=self.options['source_cache_mb']*1024*1024; layers=self.preview_layers
        self.last_progress=None
        self.loading=True; self.update_activity()
        def worker():
            context,error=None,None
            try:
                if platform not in games_cache: games_cache[platform]=library.games(platform)
                if cancel.is_set(): raise PreviewCancelled()
                game=random_game(games_cache[platform],title,previous)
                profile=dict(profile_for(library,platform,profiles))
                if logo_choice!='auto': profile['logo_pack']=logo_choice
                index=PreviewMediaIndex(library,platform,game,folders_cache,cancel)
                selected,sources,notes,missing=select_sources(library,game,profile,index)
                if cancel.is_set(): raise PreviewCancelled()
                context={'library':library,'game':game,'profile':profile,'selected':selected,'sources':sources,'missing':missing,'platform':platform,'image_cache':ImageCache(source_budget),'layer_cache':layers,'cache_namespace':object()}
            except PreviewCancelled: pass
            except Exception as failure: error=str(failure)
            self.events.put(('loaded',(job_id,context,error)))
        threading.Thread(target=worker,daemon=True).start()

    def schedule_render(self):
        self.render_id+=1
        if self.render_after: self.root.after_cancel(self.render_after)
        self.render_after=self.root.after(220,self.launch_render)

    def launch_render(self):
        if self.render_after: self.root.after_cancel(self.render_after)
        self.render_after=None
        if self.busy or not self.context or len(self.preview_selection())!=1 or self.preview_selection()[0]!=self.context['platform'] or not self.colors_valid(): return
        try: crop=crop_percentage(self.crop_text.get())
        except ValueError: return
        if self.rendering:
            self.pending_render=True; return
        self.pending_render=False; self.rendering=True; self.last_progress=None; self.update_activity()
        revision=self.render_id; context=self.context
        profile=copy.deepcopy(context['profile']); profile.update(copy.deepcopy(self.palettes.get(context['platform'],{}))); profile['background_position']=crop
        self.status.set(f'Updating live preview - crop {crop:g}%...')
        def worker():
            try: value=(revision,render(context['library'],context['game'],profile,context['selected'],image_cache=context['image_cache'],layer_cache=context['layer_cache'],cache_namespace=context.get('cache_namespace')),None,crop)
            except Exception as failure: value=(revision,None,str(failure),crop)
            self.events.put(('rendered',value))
        threading.Thread(target=worker,daemon=True).start()

    def start_generation(self):
        if self.busy or not self.library or not self.selected():
            if not self.busy: self.status.set('Scan your library and select systems first.')
            return
        if Path(self.launchbox.get()).resolve() not in [self.library.root,self.library.root.parent]:
            self.status.set('The LaunchBox folder changed. Scan systems again.'); return
        try: crop=crop_percentage(self.crop_text.get())
        except ValueError as error:
            self.status.set(str(error)); return
        if not self.colors_valid() or self.picker: return
        self.flush_settings(); self.load_cancel.set(); self.pending_load=None; self.render_id+=1; self.cancelled.clear(); self.job_kind='batch'; self.last_progress=None; self.set_busy(True)
        library=self.library; platforms=self.selected(); output=self.output.get(); title=self.title_filter.get(); force=self.rebuild.get(); palettes=copy.deepcopy(self.palettes); logos=dict(self.logo_choices); interval=self.options['checkpoint_games']
        def worker():
            try:
                rows=generate(library,output,platforms,title=title,force=force,progress=lambda text:self.events.put(('batch_status',text)),cancelled=self.cancelled,palette_overrides=palettes,background_position=crop,logo_overrides=logos,checkpoint_games=interval,progress_state=lambda value:self.events.put(('batch_progress',value)))
                self.events.put(('done',(rows,output)))
            except Exception as error: self.events.put(('error',str(error)))
        threading.Thread(target=worker,daemon=True).start()

    def start(self,preview=False):
        if preview: self.request_random()
        else: self.start_generation()

    def poll(self):
        if self.closed: return
        try:
            while True:
                kind,value=self.events.get_nowait()
                if kind=='status': self.status.set(value)
                elif kind=='batch_status':
                    # Counted labels belong above the bars. The text callback
                    # retains CLI compatibility; only preparation messages are
                    # needed here once game identity comes from progress data.
                    if value.startswith('Counting visible games:'): self.status.set(value)
                elif kind=='logo_loaded':
                    original,platform,choice,path,error=value
                    if original is not None and self.context is original and self.logo_choices.get(platform)==choice:
                        if error: self.status.set('Could not load the console logo: '+error)
                        else:
                            updated=dict(original); updated['selected']=dict(original['selected']); updated['selected']['Platform clear logo']=path
                            updated['image_cache']=ImageCache(self.options['source_cache_mb']*1024*1024)
                            updated['layer_cache']=self.preview_layers; updated['cache_namespace']=object()
                            self.context=updated; self.schedule_render()
                elif kind=='batch_progress': self.show_batch_progress(value)
                elif kind=='operation_progress': self.show_operation_progress(value)
                elif kind=='workflow_result':
                    callback,result,error=value; self.set_busy(False)
                    if error: self.status.set(error); messagebox.showerror('Marquee operation',error,parent=self.root)
                    else: callback(result)
                elif kind=='error': self.set_busy(False); self.status.set(value)
                elif kind=='library':
                    self.library=value; self.available_packs=getattr(value,'discovered_logo_packs',{}); self.profiles=load_profiles(); self.game_cache={}; self.folder_cache={}; self.requested_platform=None
                    self.systems.set_enabled(True); self.systems.delete(0,'end')
                    for platform in value.platform_files: self.systems.insert('end',platform)
                    self.preview_layers=PreviewLayerCache(self.options['layer_cache_mb']*1024*1024)
                    self.set_busy(False); self.save(); self.status.set(f'Found {len(value.platform_files)} systems. Click a name to preview; check systems to generate.')
                elif kind=='loaded':
                    self.loading=False; job_id,context,error=value
                    if job_id==self.load_id and not self.busy:
                        if context:
                            self.context=context; self.preview_title.set(f'{context["platform"]} - {context["game"].title}'); self.schedule_render()
                        elif error:
                            self.status.set(error); self.preview_title.set(f'{self.requested_platform} - no preview available')
                            self.photo=None; self.preview.configure(image='',text='No matching preview could be loaded.',padding=(0,75))
                    self.run_pending_load(); self.update_activity()
                elif kind=='rendered':
                    self.rendering=False; revision,image,error,crop=value
                    if revision==self.render_id and not self.busy:
                        if image:
                            self.photo=ImageTk.PhotoImage(image.resize((960,180),Image.Resampling.LANCZOS)); self.preview.configure(image=self.photo,text='',padding=0)
                            missing=len(self.context['missing']) if self.context else 0
                            self.status.set(f'Live preview - crop {crop:g}%. '+(f'{missing} source images missing; fallbacks used. ' if missing else '')+'Preview changes are not saved as files.')
                        elif error: self.status.set(error)
                    if self.pending_render or revision!=self.render_id: self.launch_render()
                    self.update_activity()
                elif kind=='done':
                    rows,output=value; self.set_busy(False)
                    generated=sum(row['status']=='generated' for row in rows); errors=sum(row['status']=='error' for row in rows)
                    self.status.set(f'{"Stopped" if self.cancelled.is_set() else "Finished"}: {generated} images generated; {errors} errors.')
                    self.export_status.set('Output folder: '+self.output.get())
        except queue.Empty: pass
        self.poll_after=self.root.after(80,self.poll)

    def open_output(self):
        path=Path(self.output.get())
        if path.is_dir(): os.startfile(path)
        else: self.status.set('Choose an existing output folder or generate artwork first.')

    def close(self):
        if self.busy:
            if self.job_kind=='batch':
                self.cancelled.set(); self.status.set('Stopping after the current game; close this window once the batch stops.')
            else: self.status.set('Wait for the artwork operation to finish before closing.')
        else:
            if self.picker: self.picker.cancel()
            self.flush_settings(); self.closed=True; self.load_cancel.set()
            if self.poll_after: self.root.after_cancel(self.poll_after)
            if self.render_after: self.root.after_cancel(self.render_after)
            self.progress.stop(); self.system_progress.stop()
            self.root.destroy()


def run():
    root=tk.Tk(); App(root); root.mainloop()
