from __future__ import annotations

import sqlite3
import tkinter as tk
import webbrowser
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from db import DB_PATH, MOVEMENT_TYPES, Database, StockError
from ml_integration import MercadoLibreClient, MercadoLibreError


BG, CARD, NAVY, BLUE, GREEN, RED, AMBER, MUTED = "#f3f6fa", "#ffffff", "#17253d", "#2673dd", "#16845b", "#cf3b3b", "#d88700", "#64748b"


def number(value):
    return f"{value:g}" if isinstance(value, float) else str(value)


class ProductDialog(tk.Toplevel):
    def __init__(self, app, product=None):
        super().__init__(app)
        self.app, self.product, self.result = app, product, False
        self.title("Editar producto" if product else "Nuevo producto")
        self.geometry("610x720"); self.resizable(False, False); self.transient(app); self.grab_set()
        self.configure(bg=BG)
        body = ttk.Frame(self, padding=22); body.pack(fill="both", expand=True)
        self.kind = tk.StringVar(value=(product["kind"] if product else "BAG"))
        ttk.Label(body, text="Tipo de producto", style="Section.TLabel").grid(row=0, column=0, columnspan=2, sticky="w", pady=(0,8))
        kinds = ttk.Frame(body); kinds.grid(row=1,column=0,columnspan=2,sticky="w",pady=(0,15))
        ttk.Radiobutton(kinds,text="Bolsas cortadas",variable=self.kind,value="BAG",command=self.toggle).pack(side="left")
        ttk.Radiobutton(kinds,text="Rollos bolsa tubo",variable=self.kind,value="ROLL",command=self.toggle).pack(side="left",padx=20)
        fields = [("SKU interno","sku"),("Nombre","name"),("Ancho (cm)","width_cm"),("Largo (cm)","length_cm"),
                  ("Micrones","microns"),("Metros por rollo","meters_per_roll"),("Color / material","color_material"),
                  ("Tipo / categoría","category"),("Pack de almacenamiento","storage_pack"),
                  ("Stock mínimo","minimum_stock"),("Stock objetivo","target_stock")]
        self.vars, self.widgets, self.labels = {}, {}, {}
        for i,(label,key) in enumerate(fields, start=2):
            lab=ttk.Label(body,text=label); lab.grid(row=i,column=0,sticky="w",pady=6); self.labels[key]=lab
            var=tk.StringVar(value="" if not product or product[key] is None else number(product[key])); self.vars[key]=var
            ent=ttk.Entry(body,textvariable=var,width=39); ent.grid(row=i,column=1,sticky="ew",pady=6); self.widgets[key]=ent
        self.initial = tk.StringVar(value="0")
        if not product:
            ttk.Label(body,text="Stock inicial").grid(row=13,column=0,sticky="w",pady=6)
            self.initial_entry=ttk.Entry(body,textvariable=self.initial,width=39); self.initial_entry.grid(row=13,column=1,sticky="ew",pady=6)
            ttk.Label(body,text="En bolsas individuales o cantidad de rollos.",style="Hint.TLabel").grid(row=14,column=1,sticky="w")
        self.active=tk.BooleanVar(value=True if not product else bool(product["active"]))
        ttk.Checkbutton(body,text="Producto activo",variable=self.active).grid(row=15,column=1,sticky="w",pady=12)
        buttons=ttk.Frame(body); buttons.grid(row=16,column=0,columnspan=2,sticky="e",pady=(18,0))
        ttk.Button(buttons,text="Cancelar",command=self.destroy).pack(side="left",padx=5)
        ttk.Button(buttons,text="Guardar producto",style="Primary.TButton",command=self.save).pack(side="left")
        body.columnconfigure(1,weight=1); self.toggle(); self.widgets["sku"].focus_set()

    def toggle(self):
        bag=self.kind.get()=="BAG"
        for key,visible in (("length_cm",bag),("category",bag),("storage_pack",bag),("meters_per_roll",not bag)):
            if visible:
                self.labels[key].grid(); self.widgets[key].grid()
            else:
                self.labels[key].grid_remove(); self.widgets[key].grid_remove()

    def save(self):
        try:
            bag=self.kind.get()=="BAG"
            if not self.vars["sku"].get().strip() or not self.vars["name"].get().strip(): raise ValueError("SKU y nombre son obligatorios.")
            minimum=int(self.vars["minimum_stock"].get() or 0); target=int(self.vars["target_stock"].get() or 0)
            storage_pack=int(self.vars["storage_pack"].get() or 50) if bag else 1
            if storage_pack <= 0: raise ValueError("El pack de almacenamiento debe ser un entero mayor que cero.")
            if minimum < 0 or target < 0: raise ValueError("Los niveles de stock no pueden ser negativos.")
            if target < minimum: raise ValueError("El stock objetivo debe ser igual o mayor que el stock mínimo.")
            data={"kind":self.kind.get(),"sku":self.vars["sku"].get(),"name":self.vars["name"].get(),
                  "width_cm":float(self.vars["width_cm"].get().replace(",",".")),
                  "length_cm":float(self.vars["length_cm"].get().replace(",",".")) if bag else None,
                  "microns":float(self.vars["microns"].get().replace(",",".")),
                  "meters_per_roll":None if bag else float(self.vars["meters_per_roll"].get().replace(",",".")),
                  "color_material":self.vars["color_material"].get(),"category":self.vars["category"].get(),
                  "minimum_stock":minimum,"target_stock":target,"storage_pack":storage_pack,"active":int(self.active.get())}
            if self.product: self.app.db.update_product(self.product["id"],data)
            else: self.app.db.add_product(data,int(self.initial.get() or 0))
            self.result=True; self.destroy()
        except (ValueError, sqlite3.IntegrityError, StockError) as exc:
            msg="Ese SKU ya existe." if "UNIQUE" in str(exc) else str(exc)
            messagebox.showerror("No se pudo guardar",msg,parent=self)


class MovementDialog(tk.Toplevel):
    def __init__(self, app, preset="Entrada manual"):
        super().__init__(app); self.app=app; self.title("Registrar movimiento"); self.geometry("570x450"); self.resizable(False,False); self.transient(app); self.grab_set()
        body=ttk.Frame(self,padding=24); body.pack(fill="both",expand=True)
        products=app.db.products(active="Activos"); self.map={f'{p["sku"]} · {p["name"]}':p for p in products}
        self.product=tk.StringVar(); self.mtype=tk.StringVar(value=preset); self.amount=tk.StringVar(); self.mode=tk.StringVar(value="bolsas individuales / rollos"); self.reason=tk.StringVar()
        ttk.Label(body,text="Producto").pack(anchor="w"); self.combo=ttk.Combobox(body,textvariable=self.product,values=list(self.map),state="readonly",width=60); self.combo.pack(fill="x",pady=(4,12))
        ttk.Label(body,text="Tipo de movimiento").pack(anchor="w"); ttk.Combobox(body,textvariable=self.mtype,values=MOVEMENT_TYPES,state="readonly").pack(fill="x",pady=(4,12))
        ttk.Label(body,text="Cantidad").pack(anchor="w"); row=ttk.Frame(body); row.pack(fill="x",pady=(4,4)); ttk.Entry(row,textvariable=self.amount).pack(side="left",fill="x",expand=True)
        self.mode_combo=ttk.Combobox(row,textvariable=self.mode,values=["bolsas individuales / rollos","packs físicos"],state="readonly",width=25); self.mode_combo.pack(side="left",padx=(8,0))
        self.help=ttk.Label(body,text="En packs físicos se usa la configuración propia del producto.",style="Hint.TLabel"); self.help.pack(anchor="w")
        ttk.Label(body,text="Motivo / referencia").pack(anchor="w",pady=(14,0)); ttk.Entry(body,textvariable=self.reason).pack(fill="x",pady=(4,12))
        ttk.Button(body,text="Registrar movimiento",style="Primary.TButton",command=self.save).pack(anchor="e",pady=12)
        if products: self.combo.current(0)

    def save(self):
        try:
            p=self.map.get(self.product.get()); amount=int(self.amount.get())
            if not p: raise ValueError("Seleccioná un producto.")
            if self.mode.get()=="packs físicos":
                if p["kind"]!="BAG": raise ValueError("Los packs físicos solo se aplican a bolsas.")
                amount*=p["storage_pack"]
            if self.mtype.get()=="Ajuste de inventario":
                if not messagebox.askyesno("Confirmar ajuste",f"¿Querés fijar el stock de {p['name']} en {amount}?",parent=self): return
                self.app.db.adjust_stock(p["id"],amount,self.reason.get() or "Conteo físico")
            elif self.mtype.get()=="Entrada de producción":
                # La conversión se realiza en la capa de stock para que interfaz y futuras integraciones usen la misma regla.
                raw_amount=int(self.amount.get())
                self.app.db.add_production(p["id"],raw_amount,self.mode.get()=="packs físicos",self.reason.get())
            else:
                self.app.db.move_stock(p["id"],amount,self.mtype.get(),self.reason.get())
            self.app.refresh_all(); self.destroy(); messagebox.showinfo("Movimiento registrado","El stock y el historial fueron actualizados.",parent=self.app)
        except (ValueError,StockError) as exc: messagebox.showerror("No se pudo registrar",str(exc),parent=self)


class AssociationDialog(tk.Toplevel):
    def __init__(self, app, association=None):
        super().__init__(app); self.app=app; self.association=association; self.result=False
        self.title("Editar asociación" if association else "Nueva asociación MercadoLibre")
        self.geometry("650x560"); self.resizable(False,False); self.transient(app); self.grab_set()
        body=ttk.Frame(self,padding=24); body.pack(fill="both",expand=True)
        products=app.db.products(active="Activos"); self.products={f'{p["sku"]} · {p["name"]}':p for p in products}
        selected=""
        if association:
            selected=next((name for name,p in self.products.items() if p["id"]==association["product_id"]),"")
        values={"listing_id":"" if not association else association["listing_id"],
            "variation_id":"" if not association else association["variation_id"],
            "listing_name":"" if not association else association["listing_name"],
            "units_consumed":"" if not association else str(association["units_consumed"]),
            "presentation_type":"" if not association else association["presentation_type"]}
        self.vars={k:tk.StringVar(value=v) for k,v in values.items()}; self.product=tk.StringVar(value=selected)
        self.mode=tk.StringVar(value="SOLO_DESCONTAR_VENTAS" if not association else association["sync_mode"])
        self.active=tk.BooleanVar(value=True if not association else bool(association["active"]))
        fields=[("ID de publicación","listing_id"),("ID de variación (opcional)","variation_id"),("Nombre de publicación","listing_name")]
        row=0
        for label,key in fields:
            ttk.Label(body,text=label).grid(row=row,column=0,sticky="w",pady=7); ttk.Entry(body,textvariable=self.vars[key],width=43).grid(row=row,column=1,sticky="ew",pady=7); row+=1
        ttk.Label(body,text="Producto interno").grid(row=row,column=0,sticky="w",pady=7); ttk.Combobox(body,textvariable=self.product,values=list(self.products),state="readonly",width=40).grid(row=row,column=1,sticky="ew",pady=7); row+=1
        for label,key in [("Cantidad física por unidad vendida","units_consumed"),("Presentación (ej. x100)","presentation_type")]:
            ttk.Label(body,text=label).grid(row=row,column=0,sticky="w",pady=7); ttk.Entry(body,textvariable=self.vars[key]).grid(row=row,column=1,sticky="ew",pady=7); row+=1
        ttk.Label(body,text="Modo").grid(row=row,column=0,sticky="w",pady=7); ttk.Combobox(body,textvariable=self.mode,values=["SOLO_DESCONTAR_VENTAS","SINCRONIZAR_STOCK"],state="readonly").grid(row=row,column=1,sticky="ew",pady=7); row+=1
        ttk.Checkbutton(body,text="Asociación activa",variable=self.active).grid(row=row,column=1,sticky="w",pady=10); row+=1
        ttk.Label(body,text="La aplicación no modifica stock publicado aunque se seleccione el modo futuro de sincronización.",style="Hint.TLabel",wraplength=400).grid(row=row,column=1,sticky="w"); row+=1
        buttons=ttk.Frame(body); buttons.grid(row=row,column=0,columnspan=2,sticky="e",pady=20)
        ttk.Button(buttons,text="Cancelar",command=self.destroy).pack(side="left",padx=6); ttk.Button(buttons,text="Guardar",style="Primary.TButton",command=self.save).pack(side="left")
        body.columnconfigure(1,weight=1)

    def save(self):
        try:
            product=self.products.get(self.product.get()); consumed=int(self.vars["units_consumed"].get())
            if not self.vars["listing_id"].get().strip(): raise ValueError("El ID de publicación es obligatorio.")
            if not product: raise ValueError("Seleccioná el producto interno.")
            if consumed<=0: raise ValueError("La cantidad consumida debe ser mayor que cero.")
            data={k:v.get() for k,v in self.vars.items()}; data.update({"product_id":product["id"],"units_consumed":consumed,"sync_mode":self.mode.get(),"active":int(self.active.get())})
            if self.association:self.app.db.update_marketplace_listing(self.association["id"],data)
            else:self.app.db.add_marketplace_listing(product["id"],data["listing_id"],consumed,data["variation_id"],data["presentation_type"],data["listing_name"],data["sync_mode"])
            self.result=True; self.destroy()
        except (ValueError,sqlite3.IntegrityError) as exc:
            msg="Ya existe una asociación para esa publicación y variación." if "UNIQUE" in str(exc) else str(exc)
            messagebox.showerror("No se pudo guardar",msg,parent=self)


class MercadoLibreConfigDialog(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app); self.app=app; self.title("Conectar MercadoLibre"); self.geometry("650x480"); self.resizable(False,False); self.transient(app); self.grab_set()
        public=app.ml.public_config(); body=ttk.Frame(self,padding=24); body.pack(fill="both",expand=True)
        self.client_id=tk.StringVar(value=public.get("client_id","")); self.secret=tk.StringVar(); self.redirect=tk.StringVar(value=public.get("redirect_uri","")); self.code=tk.StringVar()
        ttk.Label(body,text="1. Credenciales de tu aplicación",style="Section.TLabel").pack(anchor="w")
        for label,var,show in [("Client ID / App ID",self.client_id,""),("Client Secret",self.secret,"●"),("Redirect URI exacta",self.redirect,"")]:
            ttk.Label(body,text=label).pack(anchor="w",pady=(9,2)); ttk.Entry(body,textvariable=var,show=show).pack(fill="x")
        row=ttk.Frame(body); row.pack(fill="x",pady=12); ttk.Button(row,text="Guardar credenciales",command=self.save_config).pack(side="left"); ttk.Button(row,text="Abrir autorización",style="Primary.TButton",command=self.authorize).pack(side="left",padx=8)
        ttk.Separator(body).pack(fill="x",pady=10); ttk.Label(body,text="2. Después de autorizar",style="Section.TLabel").pack(anchor="w")
        ttk.Label(body,text="Pegá aquí la URL completa a la que te redirigió MercadoLibre (o solamente el código):",style="Hint.TLabel",wraplength=580).pack(anchor="w",pady=(8,3))
        ttk.Entry(body,textvariable=self.code).pack(fill="x"); ttk.Button(body,text="Completar conexión",command=self.exchange).pack(anchor="e",pady=10)

    def save_config(self):
        if not all(v.get().strip() for v in (self.client_id,self.secret,self.redirect)):
            messagebox.showerror("Faltan datos","Completá los tres campos.",parent=self); return
        try:self.app.ml.save_configuration(self.client_id.get(),self.secret.get(),self.redirect.get()); self.app.refresh_ml(); messagebox.showinfo("Guardado","Las claves quedaron cifradas para tu usuario de Windows.",parent=self)
        except Exception as exc:messagebox.showerror("No se pudo guardar",str(exc),parent=self)

    def authorize(self):
        try:webbrowser.open(self.app.ml.authorization_url())
        except MercadoLibreError as exc:messagebox.showerror("No se pudo autorizar",str(exc),parent=self)

    def exchange(self):
        try:self.app.ml.exchange_code(self.code.get()); self.app.refresh_ml(); messagebox.showinfo("Conectado","La cuenta de MercadoLibre quedó autorizada.",parent=self); self.destroy()
        except MercadoLibreError as exc:messagebox.showerror("No se pudo conectar",str(exc),parent=self)


class StockApp(tk.Tk):
    def __init__(self):
        super().__init__(); self.db=Database(); self.ml=MercadoLibreClient(self.db); self.title("8Plast · Gestión de Stock"); self.geometry("1280x780"); self.minsize(1040,650); self.configure(bg=BG)
        self.style_ui(); self.build(); self.refresh_all()

    def style_ui(self):
        s=ttk.Style(self); s.theme_use("clam"); s.configure(".",font=("Segoe UI",10),background=BG,foreground=NAVY)
        s.configure("TFrame",background=BG); s.configure("Card.TFrame",background=CARD); s.configure("TLabel",background=BG)
        s.configure("Card.TLabel",background=CARD); s.configure("Title.TLabel",font=("Segoe UI Semibold",22),foreground=NAVY)
        s.configure("Section.TLabel",font=("Segoe UI Semibold",12),foreground=NAVY); s.configure("Hint.TLabel",font=("Segoe UI",9),foreground=MUTED)
        s.configure("Metric.TLabel",font=("Segoe UI Semibold",25),background=CARD,foreground=NAVY)
        s.configure("Primary.TButton",background=BLUE,foreground="white",padding=(14,8)); s.map("Primary.TButton",background=[("active","#165ebc")])
        s.configure("Treeview",rowheight=30,background=CARD,fieldbackground=CARD,borderwidth=0); s.configure("Treeview.Heading",font=("Segoe UI Semibold",9),background="#e7edf5",padding=7)
        s.configure("TNotebook",background=BG,borderwidth=0); s.configure("TNotebook.Tab",padding=(20,10),font=("Segoe UI Semibold",10))

    def build(self):
        top=ttk.Frame(self,padding=(24,18)); top.pack(fill="x"); ttk.Label(top,text="8PLAST",style="Title.TLabel").pack(side="left")
        ttk.Label(top,text="Gestión de stock",style="Hint.TLabel").pack(side="left",padx=12,pady=(10,0))
        ttk.Button(top,text="Cargar producción",style="Primary.TButton",command=lambda:MovementDialog(self,"Entrada de producción")).pack(side="right")
        ttk.Button(top,text="Registrar movimiento",command=lambda:MovementDialog(self)).pack(side="right",padx=8)
        self.tabs=ttk.Notebook(self); self.tabs.pack(fill="both",expand=True,padx=24,pady=(0,20))
        self.dashboard_tab=ttk.Frame(self.tabs,padding=16); self.bags_tab=ttk.Frame(self.tabs,padding=16); self.rolls_tab=ttk.Frame(self.tabs,padding=16); self.history_tab=ttk.Frame(self.tabs,padding=16); self.ml_tab=ttk.Frame(self.tabs,padding=16); self.settings_tab=ttk.Frame(self.tabs,padding=16)
        for frame,title in [(self.dashboard_tab,"Panel principal"),(self.bags_tab,"Bolsas"),(self.rolls_tab,"Rollos"),(self.history_tab,"Movimientos"),(self.ml_tab,"MercadoLibre"),(self.settings_tab,"Configuración")]: self.tabs.add(frame,text=title)
        self.build_dashboard(); self.build_products(self.bags_tab,"BAG"); self.build_products(self.rolls_tab,"ROLL"); self.build_history(); self.build_ml(); self.build_settings()

    def build_dashboard(self):
        metrics=ttk.Frame(self.dashboard_tab); metrics.pack(fill="x")
        self.metric_labels=[]
        for i,title in enumerate(["Productos activos","Necesitan producción","Sin stock"]):
            card=ttk.Frame(metrics,style="Card.TFrame",padding=18); card.grid(row=0,column=i,sticky="ew",padx=(0 if i==0 else 8,0)); ttk.Label(card,text=title,style="Card.TLabel").pack(anchor="w"); val=ttk.Label(card,text="0",style="Metric.TLabel"); val.pack(anchor="w"); self.metric_labels.append(val); metrics.columnconfigure(i,weight=1)
        lower=ttk.Frame(self.dashboard_tab); lower.pack(fill="both",expand=True,pady=(18,0)); lower.columnconfigure(0,weight=2); lower.columnconfigure(1,weight=1); lower.rowconfigure(1,weight=1)
        ttk.Label(lower,text="Últimos movimientos",style="Section.TLabel").grid(row=0,column=0,sticky="w",pady=(0,8)); ttk.Label(lower,text="Alertas",style="Section.TLabel").grid(row=0,column=1,sticky="w",padx=(16,0),pady=(0,8))
        self.recent=self.tree(lower,["Fecha","Producto","Tipo","Cambio","Stock"],[125,220,150,75,75]); self.recent.grid(row=1,column=0,sticky="nsew")
        self.alerts=self.tree(lower,["Producto","Estado","Stock","Objetivo","Faltan","Packs físicos"],[190,85,60,70,70,85]); self.alerts.grid(row=1,column=1,sticky="nsew",padx=(16,0))

    def tree(self,parent,columns,widths):
        tree=ttk.Treeview(parent,columns=columns,show="headings",selectmode="browse")
        for c,w in zip(columns,widths): tree.heading(c,text=c); tree.column(c,width=w,minwidth=55,anchor="w")
        tree.tag_configure("low",foreground=AMBER); tree.tag_configure("empty",foreground=RED); tree.tag_configure("inactive",foreground=MUTED)
        return tree

    def build_products(self,frame,kind):
        tools=ttk.Frame(frame); tools.pack(fill="x",pady=(0,10)); search=tk.StringVar(); active=tk.StringVar(value="Activos")
        ttk.Label(tools,text="Buscar").pack(side="left"); ent=ttk.Entry(tools,textvariable=search,width=28); ent.pack(side="left",padx=7)
        ttk.Combobox(tools,textvariable=active,values=["Activos","Inactivos","Todos"],state="readonly",width=12).pack(side="left")
        ttk.Button(tools,text="Nuevo producto",style="Primary.TButton",command=lambda:self.new_product(kind)).pack(side="right")
        ttk.Button(tools,text="Editar",command=lambda:self.edit_selected(kind)).pack(side="right",padx=7)
        if kind=="BAG": cols=["SKU","Producto","Medida","Micrones","Stock bolsas","Pack almac.","Packs físicos","Mínimo","Objetivo","Faltan"]+[f"x{x}" for x in self.db.presentations()]+["Estado"]
        else: cols=["SKU","Producto","Ancho","Micrones","Metros","Rollos","Mínimo","Objetivo","Faltan","Estado"]
        widths=[90,235]+[85]*(len(cols)-2); tree=self.tree(frame,cols,widths); tree.pack(fill="both",expand=True)
        setattr(self,f"{kind.lower()}_tree",tree); setattr(self,f"{kind.lower()}_search",search); setattr(self,f"{kind.lower()}_active",active)
        search.trace_add("write",lambda *_:self.refresh_products(kind)); active.trace_add("write",lambda *_:self.refresh_products(kind)); tree.bind("<Double-1>",lambda e:self.edit_selected(kind))

    def build_history(self):
        tools=ttk.Frame(self.history_tab); tools.pack(fill="x",pady=(0,10)); self.hist_search=tk.StringVar(); self.hist_type=tk.StringVar(value="Todos")
        ttk.Label(tools,text="Buscar").pack(side="left"); ttk.Entry(tools,textvariable=self.hist_search,width=30).pack(side="left",padx=7)
        ttk.Combobox(tools,textvariable=self.hist_type,values=["Todos",*MOVEMENT_TYPES],state="readonly",width=24).pack(side="left")
        self.history=self.tree(self.history_tab,["Fecha y hora","SKU","Producto","Tipo","Cantidad","Anterior","Posterior","Motivo","Origen"],[145,90,210,155,80,75,75,240,80]); self.history.pack(fill="both",expand=True)
        self.hist_search.trace_add("write",lambda *_:self.refresh_history()); self.hist_type.trace_add("write",lambda *_:self.refresh_history())

    def build_settings(self):
        box=ttk.Frame(self.settings_tab,style="Card.TFrame",padding=22); box.pack(fill="x"); ttk.Label(box,text="Presentaciones de bolsas",style="Section.TLabel").pack(anchor="w")
        ttk.Label(box,text="Las presentaciones calculan disponibilidad sobre el mismo stock maestro; no crean stocks separados.",style="Card.TLabel").pack(anchor="w",pady=(4,12))
        row=ttk.Frame(box,style="Card.TFrame"); row.pack(fill="x"); self.presentation=tk.StringVar(); ttk.Entry(row,textvariable=self.presentation,width=12).pack(side="left"); ttk.Button(row,text="Agregar presentación",command=self.add_presentation).pack(side="left",padx=8); self.presentation_text=ttk.Label(row,style="Card.TLabel"); self.presentation_text.pack(side="left",padx=15)
        backup=ttk.Frame(self.settings_tab,style="Card.TFrame",padding=22); backup.pack(fill="x",pady=16); ttk.Label(backup,text="Copia de seguridad",style="Section.TLabel").pack(anchor="w"); ttk.Label(backup,text=f"Base local: {DB_PATH}",style="Card.TLabel").pack(anchor="w",pady=(4,12)); ttk.Button(backup,text="Crear copia ahora",command=self.backup).pack(anchor="w")

    def new_product(self,kind):
        d=ProductDialog(self); d.kind.set(kind); d.toggle(); self.wait_window(d)
        if d.result:self.refresh_all()

    def selected_product(self,kind):
        tree=getattr(self,f"{kind.lower()}_tree"); sel=tree.selection()
        return self.db.product(int(sel[0])) if sel else None

    def edit_selected(self,kind):
        p=self.selected_product(kind)
        if not p: messagebox.showwarning("Seleccioná un producto","Elegí un producto de la lista.",parent=self); return
        d=ProductDialog(self,p); self.wait_window(d)
        if d.result:self.refresh_all()

    def add_presentation(self):
        try:self.db.add_presentation(int(self.presentation.get())); self.presentation.set(""); messagebox.showinfo("Presentación agregada","Se aplicará al reiniciar la aplicación.",parent=self); self.refresh_settings()
        except (ValueError,StockError):messagebox.showerror("Valor inválido","Ingresá una cantidad entera mayor que cero.",parent=self)

    def backup(self):
        default=f"8plast_backup_{datetime.now():%Y%m%d_%H%M}.db"; path=filedialog.asksaveasfilename(title="Guardar copia",initialfile=default,defaultextension=".db",filetypes=[("Base SQLite","*.db")])
        if path:self.db.backup(path); messagebox.showinfo("Copia creada",f"La copia se guardó en:\n{path}",parent=self)

    def build_ml(self):
        status=ttk.Frame(self.ml_tab,style="Card.TFrame",padding=16); status.pack(fill="x",pady=(0,12))
        ttk.Label(status,text="Conexión MercadoLibre",style="Section.TLabel").pack(side="left"); self.ml_status=ttk.Label(status,style="Card.TLabel"); self.ml_status.pack(side="left",padx=15)
        ttk.Button(status,text="Configurar conexión",command=lambda:MercadoLibreConfigDialog(self)).pack(side="right")
        ttk.Button(status,text="Leer ventas ahora",style="Primary.TButton",command=self.sync_ml).pack(side="right",padx=8)
        tools=ttk.Frame(self.ml_tab); tools.pack(fill="x",pady=(0,10)); self.ml_filter=tk.StringVar(value="Todas")
        ttk.Combobox(tools,textvariable=self.ml_filter,values=["Todas","Activas","Inactivas"],state="readonly",width=12).pack(side="left")
        ttk.Button(tools,text="Nueva asociación",style="Primary.TButton",command=self.new_association).pack(side="right")
        ttk.Button(tools,text="Editar",command=self.edit_association).pack(side="right",padx=7)
        cols=["Publicación","Variación","Nombre publicación","Producto interno","Tipo","Consume","Presentación","Estado","Modo","Última sincronización"]
        self.ml_tree=self.tree(self.ml_tab,cols,[115,90,190,210,65,70,90,70,180,140]); self.ml_tree.pack(fill="both",expand=True); self.ml_tree.bind("<Double-1>",lambda e:self.edit_association())
        self.ml_filter.trace_add("write",lambda *_:self.refresh_ml())
        ttk.Label(self.ml_tab,text="Modo predeterminado: solo lee ventas y descuenta stock físico. Esta versión nunca modifica el stock publicado.",style="Hint.TLabel").pack(anchor="w",pady=(8,0))

    def new_association(self):
        d=AssociationDialog(self); self.wait_window(d)
        if d.result:self.refresh_ml()

    def edit_association(self):
        sel=self.ml_tree.selection()
        if not sel:messagebox.showwarning("Seleccioná una asociación","Elegí una fila de la lista.",parent=self); return
        assoc=self.db.marketplace_listing_by_id(int(sel[0])); d=AssociationDialog(self,assoc); self.wait_window(d)
        if d.result:self.refresh_ml()

    def refresh_ml(self):
        try:self.ml_status.configure(text=self.ml.connection_status())
        except Exception:self.ml_status.configure(text="Configuración segura no disponible")
        self.ml_tree.delete(*self.ml_tree.get_children())
        for row in self.db.marketplace_listings(self.ml_filter.get()):
            values=[row["listing_id"],row["variation_id"] or "—",row["listing_name"],f'{row["sku"]} · {row["product_name"]}',
                "Bolsas" if row["kind"]=="BAG" else "Rollos",row["units_consumed"],row["presentation_type"],"Activa" if row["active"] else "Inactiva",row["sync_mode"],row["last_synced_at"] or "—"]
            self.ml_tree.insert("","end",iid=str(row["id"]),values=values,tags=("" if row["active"] else "inactive",))

    def sync_ml(self):
        if not messagebox.askyesno("Leer ventas","Se consultarán las órdenes recientes y se descontarán únicamente las publicaciones asociadas. ¿Continuar?",parent=self):return
        try:
            summary=self.ml.sync_recent_orders(); self.refresh_all()
            unassociated=", ".join(summary["unassociated"]) or "ninguna"
            messagebox.showinfo("Lectura finalizada",f'Órdenes revisadas: {summary["orders"]}\nMovimientos aplicados: {summary["processed"]}\nYa procesados: {summary["duplicates"]}\nSin asociación: {unassociated}',parent=self)
        except (MercadoLibreError,StockError) as exc:messagebox.showerror("No se pudo leer ventas",str(exc),parent=self)

    def status(self,p):
        if p["stock"]==0:return "SIN STOCK","empty"
        if p["stock"]<=p["minimum_stock"]:return "PRODUCIR","low"
        return "OK",("" if p["active"] else "inactive")

    def refresh_products(self,kind):
        tree=getattr(self,f"{kind.lower()}_tree"); tree.delete(*tree.get_children()); pres=self.db.presentations()
        for p in self.db.products(kind,getattr(self,f"{kind.lower()}_search").get(),getattr(self,f"{kind.lower()}_active").get()):
            state,tag=self.status(p)
            missing=self.db.production_needed(p)
            if not p["active"]:state="INACTIVO"; tag="inactive"
            if kind=="BAG": vals=[p["sku"],p["name"],f'{number(p["width_cm"])}x{number(p["length_cm"])} cm',number(p["microns"]),p["stock"],p["storage_pack"],p["stock"]//p["storage_pack"],p["minimum_stock"],p["target_stock"],missing,*[p["stock"]//x for x in pres],state]
            else: vals=[p["sku"],p["name"],f'{number(p["width_cm"])} cm',number(p["microns"]),number(p["meters_per_roll"]),p["stock"],p["minimum_stock"],p["target_stock"],missing,state]
            tree.insert("", "end", iid=str(p["id"]), values=vals, tags=(tag,))

    def refresh_dashboard(self):
        counts,recent,alerts=self.db.dashboard(); values=[counts["active"] or 0,counts["low"] or 0,counts["empty"] or 0]
        for label,val in zip(self.metric_labels,values):label.configure(text=str(val))
        self.recent.delete(*self.recent.get_children())
        for m in recent:self.recent.insert("","end",values=[m["created_at"][:16].replace("T"," "),m["product_name"],m["movement_type"],f'{m["quantity_delta"]:+}',m["stock_after"]])
        self.alerts.delete(*self.alerts.get_children())
        for p in alerts:
            state,_=self.status(p); missing=self.db.production_needed(p); packs=(missing+p["storage_pack"]-1)//p["storage_pack"] if p["kind"]=="BAG" else "—"
            self.alerts.insert("","end",values=[p["name"],state,p["stock"],p["target_stock"],missing,packs],tags=("empty" if p["stock"]==0 else "low",))

    def refresh_history(self):
        self.history.delete(*self.history.get_children())
        for m in self.db.movements(self.hist_search.get(),self.hist_type.get()):
            self.history.insert("","end",values=[m["created_at"][:19].replace("T"," "),m["sku"],m["product_name"],m["movement_type"],f'{m["quantity_delta"]:+}',m["stock_before"],m["stock_after"],m["reason"],m["source"]])

    def refresh_settings(self): self.presentation_text.configure(text="Actuales: "+", ".join(f"x{x}" for x in self.db.presentations()))
    def refresh_all(self): self.refresh_dashboard(); self.refresh_products("BAG"); self.refresh_products("ROLL"); self.refresh_history(); self.refresh_ml(); self.refresh_settings()


if __name__ == "__main__":
    StockApp().mainloop()
