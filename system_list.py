"""Native system checkboxes with an independent, keyboard-accessible preview row."""
import tkinter as tk
from tkinter import ttk


class SystemList(ttk.Frame):
    """Keep batch inclusion separate from the system whose artwork is previewed.

    Checkboxes use native Tk controls and support Space. System-name buttons
    select a preview without changing any checkboxes. Scroll bindings belong
    to this component only, so other windows and color controls are unaffected.
    A small list-style API also supports programmatic population and selection.
    """
    def __init__(self,parent,on_preview=None,on_checked=None):
        super().__init__(parent)
        self.rows=[]; self.active_name=None; self.enabled=True
        self.on_preview=on_preview; self.on_checked=on_checked
        self.canvas=tk.Canvas(self,height=170,highlightthickness=1,highlightbackground='#CCCCCC',background='#F7F7F7')
        scrollbar=ttk.Scrollbar(self,orient='vertical',command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scrollbar.set)
        self.canvas.pack(side='left',fill='both',expand=True); scrollbar.pack(side='right',fill='y')
        self.content=ttk.Frame(self.canvas)
        self.content_id=self.canvas.create_window(0,0,window=self.content,anchor='nw')
        self.content.bind('<Configure>',lambda event:self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.canvas.bind('<Configure>',lambda event:self.canvas.itemconfigure(self.content_id,width=event.width-2))
        self.canvas.bind('<MouseWheel>',self._wheel); self.content.bind('<MouseWheel>',self._wheel)

    def _wheel(self,event):
        if event.delta: self.canvas.yview_scroll(-int(event.delta/120) or (-1 if event.delta>0 else 1),'units')
        return 'break'

    def insert(self,index,name):
        """Append a system without checking it or starting a preview."""
        if index!='end' and int(index)!=len(self.rows): raise ValueError('Systems must be appended in display order.')
        frame=ttk.Frame(self.content); frame.pack(fill='x',pady=1)
        value=tk.BooleanVar(self,value=False)
        row={'name':name,'frame':frame,'value':value}
        check=ttk.Checkbutton(frame,variable=value,command=lambda:self._checked(row))
        check.pack(side='left',padx=(5,3))
        button=tk.Button(frame,text=name,command=lambda:self.set_active(self.rows.index(row)),anchor='w',
                         relief='flat',borderwidth=0,font=('Segoe UI',10),background='#F0F0F0',
                         activebackground='#E3EFFB',cursor='hand2',padx=7,pady=4,takefocus=True)
        button.pack(side='left',fill='x',expand=True)
        row.update(checkbutton=check,name_button=button); self.rows.append(row)
        Tooltip(check,'Include '+name+' when generating artwork')
        Tooltip(button,'Preview and edit '+name)
        for widget in [frame,check,button]: widget.bind('<MouseWheel>',self._wheel)
        button.bind('<Up>',lambda event:self._step(row,-1))
        button.bind('<Down>',lambda event:self._step(row,1))
        if not self.enabled: check.state(['disabled']); button.configure(state='disabled')

    def _checked(self,row):
        # The first checked system supplies an initial preview, while later
        # checks preserve whichever system the user is currently reviewing.
        if self.active_name is None and row['value'].get(): self.set_active(self.rows.index(row),notify=False)
        if self.on_checked: self.on_checked()

    def _step(self,row,direction):
        if self.rows and self.enabled:
            index=max(0,min(len(self.rows)-1,self.rows.index(row)+direction))
            self.set_active(index); self.rows[index]['name_button'].focus_set()
            self.canvas.update_idletasks()
            y=self.rows[index]['frame'].winfo_y(); height=max(1,self.content.winfo_height())
            top,bottom=self.canvas.yview()
            if y<top*height or y+self.rows[index]['frame'].winfo_height()>bottom*height:
                self.canvas.yview_moveto(max(0,(y-self.canvas.winfo_height()/2)/height))
        return 'break'

    def set_active(self,index,notify=True):
        """Choose the preview row without altering batch inclusion."""
        if not self.enabled: return
        self.active_name=self.rows[int(index)]['name']
        for row in self.rows:
            row['name_button'].configure(background='#DCEBFA' if row['name']==self.active_name else '#F0F0F0')
        if notify and self.on_preview: self.on_preview()

    def active_system(self): return self.active_name
    def checked(self): return [row['name'] for row in self.rows if row['value'].get()]
    def get(self,index): return self.rows[int(index)]['name']
    def size(self): return len(self.rows)
    def curselection(self): return tuple(n for n,row in enumerate(self.rows) if row['value'].get())

    def _range(self,first,last=None):
        start=int(first); stop=len(self.rows)-1 if last=='end' else int(last) if last is not None else start
        return range(start,min(stop+1,len(self.rows)))

    def select_set(self,first,last=None):
        """Check a range; callers choose when to refresh dependent controls."""
        indexes=list(self._range(first,last))
        for n in indexes: self.rows[n]['value'].set(True)
        if self.active_name is None and indexes: self.set_active(indexes[0],notify=False)

    def selection_clear(self,first,last=None):
        for n in self._range(first,last): self.rows[n]['value'].set(False)

    def delete(self,first,last=None):
        indexes=list(self._range(first,last))
        removed=[self.rows[n] for n in indexes]
        for n in reversed(indexes): self.rows.pop(n)['frame'].destroy()
        if any(row['name']==self.active_name for row in removed): self.active_name=None

    def set_enabled(self,enabled):
        """Freeze checkbox/name input while a batch or file operation runs."""
        self.enabled=bool(enabled)
        for row in self.rows:
            row['checkbutton'].state(['!disabled'] if enabled else ['disabled'])
            row['name_button'].configure(state='normal' if enabled else 'disabled')


class Tooltip:
    """Provide a short explanation for an icon without opening a modal dialog."""
    def __init__(self,widget,text):
        self.widget=widget; self.text=text; self.timer=None; self.window=None
        widget.bind('<Enter>',self.schedule,add='+'); widget.bind('<FocusIn>',self.schedule,add='+')
        for event in ['<Leave>','<FocusOut>','<ButtonPress>','<Destroy>']: widget.bind(event,self.hide,add='+')

    def schedule(self,event=None):
        self.hide(); self.timer=self.widget.after(500,self.show)

    def show(self):
        self.timer=None
        if not self.widget.winfo_exists(): return
        self.window=tk.Toplevel(self.widget); self.window.wm_overrideredirect(True)
        self.window.geometry(f'+{self.widget.winfo_rootx()}+{self.widget.winfo_rooty()+self.widget.winfo_height()+5}')
        ttk.Label(self.window,text=self.text,padding=(7,5),relief='solid',borderwidth=1).pack()

    def hide(self,event=None):
        if self.timer:
            self.widget.after_cancel(self.timer); self.timer=None
        if self.window: self.window.destroy(); self.window=None
