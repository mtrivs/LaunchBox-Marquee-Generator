"""Paint-style color selection with nonblocking, cancellable live callbacks."""
import colorsys,re,tkinter as tk
from tkinter import ttk
from functools import lru_cache
from PIL import Image,ImageTk

FIELDS=(('Paper','main'),('Fold','page_color'),('Reverse','page_back_color'))

def hex_color(value):
    value=str(value).strip(); value=value[1:] if value.startswith('#') else value
    if not re.fullmatch(r'[0-9a-fA-F]{6}',value):
        raise ValueError('Enter six HEX digits, for example #AABBCC.')
    return '#'+value.upper()

def rgb(value):
    value=hex_color(value)
    return tuple(int(value[i:i+2],16) for i in (1,3,5))

@lru_cache(maxsize=12)
def color_area(hue,width=240,height=176):
    image=Image.new('RGB',(width,height))
    pure=colorsys.hsv_to_rgb(hue/360,1,1)
    image.putdata([tuple(round(255*(1-s+s*c)*v) for c in pure)
                   for y in range(height) for v in [1-y/(height-1)]
                   for x in range(width) for s in [x/(width-1)]])
    return image

class ColorPicker:
    def __init__(self,parent,initial,on_change,on_apply,on_cancel,title='Choose color',custom_colors=None):
        self.initial=hex_color(initial); self.value=self.initial
        self.on_change=on_change; self.on_apply=on_apply; self.on_cancel=on_cancel
        self.closed=False; self.updating=False; self.valid=True; self.area_hue=None
        self.window=tk.Toplevel(parent); self.window.withdraw(); self.window.title(title)
        self.window.transient(parent); self.window.resizable(False,False)
        self.window.protocol('WM_DELETE_WINDOW',self.cancel)
        self.window.bind('<Escape>',lambda event:self.cancel())
        self.window.bind('<Return>',lambda event:self.apply())
        frame=ttk.Frame(self.window,padding=14); frame.pack(fill='both',expand=True)
        ttk.Label(frame,text=title,font=('Segoe UI',12,'bold')).grid(row=0,column=0,columnspan=3,sticky='w')
        ttk.Label(frame,text='Adjust the color to preview it on the current marquee.').grid(row=1,column=0,columnspan=3,sticky='w',pady=(3,10))
        self.area=tk.Canvas(frame,width=240,height=176,highlightthickness=1,highlightbackground='#777777',takefocus=True,cursor='crosshair')
        self.area.grid(row=2,column=0,columnspan=3,sticky='w')
        self.area.bind('<Button-1>',self.area_changed); self.area.bind('<B1-Motion>',self.area_changed)
        for name in ['Left','Right','Up','Down']:
            self.area.bind('<'+name+'>',lambda event,name=name:self.area_key(name))
        self.area_image=self.area.create_image(0,0,anchor='nw')
        self.marker_outer=self.area.create_oval(0,0,0,0,outline='black',width=3)
        self.marker_inner=self.area.create_oval(0,0,0,0,outline='white',width=1)
        hue_image=Image.new('RGB',(240,12))
        hue_image.putdata([tuple(round(c*255) for c in colorsys.hsv_to_rgb(x/239,1,1)) for y in range(12) for x in range(240)])
        self.hue_photo=ImageTk.PhotoImage(hue_image,master=self.window)
        ttk.Label(frame,image=self.hue_photo).grid(row=3,column=0,columnspan=3,sticky='w',pady=(8,0))
        self.hue_var=tk.DoubleVar(self.window)
        self.hue_slider=ttk.Scale(frame,from_=0,to=360,variable=self.hue_var,command=self.hue_changed,length=240)
        self.hue_slider.grid(row=4,column=0,columnspan=3,sticky='w')
        ttk.Label(frame,text='Hue').grid(row=5,column=0,sticky='w')
        samples=ttk.Frame(frame); samples.grid(row=6,column=0,columnspan=3,sticky='ew',pady=8)
        for label,value in [('Original',self.initial),('New',self.value)]:
            ttk.Label(samples,text=label).pack(side='left',padx=(0,5))
            square=tk.Label(samples,bg=value,width=5,height=2,relief='solid',borderwidth=1)
            square.pack(side='left',padx=(0,16))
            if label=='New': self.new_swatch=square
        ttk.Label(frame,text='HEX').grid(row=7,column=0,sticky='w')
        self.hex_var=tk.StringVar(self.window,value=self.value)
        self.hex_entry=ttk.Entry(frame,textvariable=self.hex_var,width=12,font=('Consolas',11))
        self.hex_entry.grid(row=7,column=1,columnspan=2,sticky='w')
        self.hex_var.trace_add('write',self.hex_changed)
        self.rgb_vars=[]; self.rgb_boxes=[]
        for row,(label,number) in enumerate(zip(('Red','Green','Blue'),rgb(self.initial)),8):
            ttk.Label(frame,text=label).grid(row=row,column=0,sticky='w',pady=2)
            variable=tk.StringVar(self.window,value=str(number)); self.rgb_vars.append(variable)
            box=ttk.Spinbox(frame,from_=0,to=255,textvariable=variable,width=7)
            box.grid(row=row,column=1,columnspan=2,sticky='w'); self.rgb_boxes.append(box)
            variable.trace_add('write',self.rgb_changed)
        self.message=tk.StringVar(self.window,value='Apply keeps this color. Cancel restores the original palette.')
        ttk.Label(frame,textvariable=self.message,wraplength=340).grid(row=11,column=0,columnspan=3,sticky='w',pady=(6,8))
        ttk.Label(frame,text='Custom colors').grid(row=12,column=0,columnspan=3,sticky='w')
        custom=ttk.Frame(frame); custom.grid(row=13,column=0,columnspan=3,sticky='w',pady=(4,10))
        colors=list(dict.fromkeys([hex_color(c) for c in (custom_colors or [])]))[:12]
        defaults=['#000000','#FFFFFF','#808080','#C00000','#E8B52A','#226CB8','#3D9267','#6C4A8B']
        for i,value in enumerate((colors+[c for c in defaults if c not in colors])[:12]):
            tk.Button(custom,bg=value,activebackground=value,width=3,height=1,relief='solid',borderwidth=1,
                      command=lambda value=value:self.set_color(value)).grid(row=i//6,column=i%6,padx=(0,5),pady=2)
        buttons=ttk.Frame(frame); buttons.grid(row=14,column=0,columnspan=3,sticky='e')
        self.apply_button=ttk.Button(buttons,text='Apply',command=self.apply); self.apply_button.pack(side='left',padx=(0,6))
        ttk.Button(buttons,text='Cancel',command=self.cancel).pack(side='left')
        self.h,self.s,self.v=colorsys.rgb_to_hsv(*(c/255 for c in rgb(self.initial)))
        self._sync(); self.window.update_idletasks()
        x=parent.winfo_rootx()+max(0,(parent.winfo_width()-self.window.winfo_reqwidth())//2)
        y=parent.winfo_rooty()+max(0,(parent.winfo_height()-self.window.winfo_reqheight())//2)
        self.window.geometry(f'+{x}+{y}'); self.window.deiconify(); self.window.grab_set(); self.hex_entry.focus_set()

    def _sync(self):
        self.updating=True
        try:
            self.hex_var.set(self.value)
            for variable,number in zip(self.rgb_vars,rgb(self.value)): variable.set(str(number))
            self.hue_var.set(self.h*360); self.new_swatch.configure(bg=self.value)
            hue=round(self.h*360)%360
            if hue!=self.area_hue:
                self.area_photo=ImageTk.PhotoImage(color_area(hue),master=self.window)
                self.area.itemconfigure(self.area_image,image=self.area_photo); self.area_hue=hue
            x,y=self.s*239,(1-self.v)*175
            for marker in [self.marker_outer,self.marker_inner]: self.area.coords(marker,x-4,y-4,x+4,y+4)
            self.valid=True; self.apply_button.configure(state='normal')
            self.message.set('Apply keeps this color. Cancel restores the original palette.')
        finally: self.updating=False

    def set_color(self,value,keep_hue=False):
        if self.closed: return
        value=hex_color(value)
        h,s,v=colorsys.rgb_to_hsv(*(c/255 for c in rgb(value)))
        if not keep_hue: self.h=h if s else self.h
        self.s,self.v=s,v; changed=value!=self.value; self.value=value; self._sync()
        if changed: self.on_change(value)

    def hsv_changed(self):
        value='#'+''.join(f'{round(c*255):02X}' for c in colorsys.hsv_to_rgb(self.h,self.s,self.v))
        self.set_color(value,keep_hue=True)

    def area_changed(self,event):
        self.area.focus_set(); self.s=max(0,min(1,event.x/239)); self.v=max(0,min(1,1-event.y/175)); self.hsv_changed()

    def area_key(self,name):
        self.s=max(0,min(1,self.s+({'Left':-1,'Right':1}.get(name,0)/100)))
        self.v=max(0,min(1,self.v+({'Down':-1,'Up':1}.get(name,0)/100))); self.hsv_changed(); return 'break'

    def hue_changed(self,value):
        if self.updating or self.closed: return
        self.h=float(value)/360; self.hsv_changed()

    def hex_changed(self,*args):
        if self.updating or self.closed: return
        try: value=hex_color(self.hex_var.get())
        except ValueError as error: self.invalid(str(error)); return
        self.set_color(value)

    def rgb_changed(self,*args):
        if self.updating or self.closed: return
        try:
            numbers=[int(variable.get()) for variable in self.rgb_vars]
            if any(not 0<=n<=255 for n in numbers): raise ValueError()
        except ValueError: self.invalid('Red, Green and Blue must be whole numbers from 0 to 255.'); return
        self.set_color('#'+''.join(f'{n:02X}' for n in numbers))

    def invalid(self,message):
        self.valid=False; self.apply_button.configure(state='disabled'); self.message.set(message)

    def _close(self):
        self.closed=True; self.window.grab_release(); self.window.destroy()

    def apply(self):
        if self.closed or not self.valid: return
        value=self.value; self._close(); self.on_apply(value)

    def cancel(self):
        if self.closed: return
        self._close(); self.on_cancel()

