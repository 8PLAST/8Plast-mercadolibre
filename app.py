from __future__ import annotations

import sqlite3
import threading
import tkinter as tk
import webbrowser
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from db import DB_PATH, MOVEMENT_TYPES, Database, StockError
from product_planning import build_production_plan


BAG_COLOR_TABS=("Todas","Negras","Verdes","Amarillas","Azules","Rojas","Polietileno cristal")
ROLL_MICRON_TABS=("Todos","50 micrones","70 micrones")
BAG_COLOR_VALUES={
    "Negras":{"negro","negra","negros","negras"},
    "Verdes":{"verde","verdes"},
    "Amarillas":{"amarillo","amarilla","amarillos","amarillas"},
    "Azules":{"azul","azules"},
    "Rojas":{"rojo","roja","rojos","rojas"},
}


def bag_matches_color(product, selected_color):
    """Filtro visual: nunca transforma ni duplica el producto recibido."""
    if selected_color=="Todas": return True
    if selected_color=="Polietileno cristal":
        data=dict(product)
        return data.get("kind")=="BAG" and (str(data.get("category","")).strip().casefold()=="cristal" or str(data.get("sku","")).upper().startswith("CRI-"))
    value=str(product["color_material"] or "").strip().casefold()
    return value in BAG_COLOR_VALUES.get(selected_color,set())


def filter_bags_by_color(products, selected_color):
    return [product for product in products if bag_matches_color(product,selected_color)]


def roll_matches_microns(product, selected_microns):
    """Filtro visual que conserva el mismo registro y stock maestro del rollo."""
    if selected_microns=="Todos": return True
    expected={"50 micrones":50.0,"70 micrones":70.0}.get(selected_microns)
    if expected is None: return True
    try: return float(product["microns"])==expected
    except (TypeError,ValueError): return False


def filter_rolls_by_microns(products, selected_microns):
    return [product for product in products if roll_matches_microns(product,selected_microns)]
from bulk_import import create_template, import_preview, preview_file
from ml_integration import MercadoLibreClient, MercadoLibreError


BG, CARD, NAVY, BLUE, GREEN, RED, AMBER, MUTED = "#f5f7fa", "#ffffff", "#111827", "#1677ff", "#2eba73", "#ef3340", "#ff9418", "#6b7280"
SIDEBAR="#ffffff"; BORDER="#e5e7eb"; SECONDARY="#f1f5f9"; SHADOW="#edf0f4"


def icon_canvas(parent,name,size=24,color=NAVY,bg=CARD,stroke=2):
    """Render centralizado de los assets ilustrados 2.5D de 8PLAST."""
    c=tk.Canvas(parent,width=size,height=size,bg=bg,highlightthickness=0,bd=0)
    illustrated={"home":"warehouse","package":"poly-bags","rolls":"film-roll","history":"stock-movements","store":"online-sales","settings":"settings-controls","inventory":"inventory-boxes","alert":"stock-low","x-circle":"stock-critical"}
    if name in illustrated:
        variant=28 if size<=28 else 36 if size<=36 else 56
        path=Path(__file__).parent/"readonly_portal"/"static"/"icons"/"illustrated"/f"{illustrated[name]}-{variant}.png"
        image=tk.PhotoImage(file=str(path))
        c.create_image(size//2,size//2,image=image)
        c._illustrated_icon=image
        return c
    s=size/24; line=lambda *xy,**kw:c.create_line(*[v*s for v in xy],**({"fill":color,"width":stroke,"capstyle":"round","joinstyle":"round"}|kw))
    oval=lambda *xy,**kw:c.create_oval(*[v*s for v in xy],**({"outline":color,"width":stroke}|kw))
    rect=lambda *xy,**kw:c.create_rectangle(*[v*s for v in xy],**({"outline":color,"width":stroke}|kw))
    poly=lambda points,fill,outline="":c.create_polygon(*[v*s for point in points for v in point],fill=fill,outline=outline)
    if name in {"home","package","rolls","history","store","settings","alert","x-circle"}:
        # Sombra y objetos por capas: mismos colores y geometría que los SVG web.
        if name=="home":
            poly([(3,10),(12,3),(21,10),(21,21),(3,21)],"#3779dc"); poly([(6,11),(12,6),(18,11),(18,19),(6,19)],"#dff1ff"); rect(9,12,12,19,fill="#ffffff"); rect(14,9,17,19,fill="#50d39a")
        elif name=="package":
            poly([(4,8),(20,8),(22,22),(2,22)],"#63a9ef"); line(8,9,9,5,12,3,15,5,16,9); poly([(7,13),(17,13),(16,19),(6,19)],"#d8f4ff")
        elif name=="rolls":
            oval(2,4,14,22,fill="#8eb9e7",outline="#6e91bd"); rect(8,4,17,22,fill="#8eb9e7",outline=""); oval(10,4,22,22,fill="#b9d7f4",outline="#6e91bd"); oval(14,8,19,18,fill="#ffffff",outline=""); oval(15,10,18,16,fill="#527aaa",outline="")
        elif name=="history":
            poly([(3,8),(11,4),(19,8),(11,12)],"#78c8ff"); poly([(3,8),(11,12),(11,21),(3,17)],"#3e78d9"); poly([(19,8),(11,12),(11,21),(19,17)],"#8ed4ff"); line(14,3,22,3,19,1); line(22,3,19,6); line(10,21,2,21,5,19,fill="#50a875")
        elif name=="store":
            rect(4,9,20,22,fill="#80b9ef",outline="#4a78b4"); poly([(3,9),(5,3),(19,3),(21,9)],"#ffd45f"); rect(8,14,13,22,fill="#ffffff",outline=""); rect(15,13,19,17,fill="#dff1ff",outline="")
        elif name=="settings":
            poly([(10,2),(14,2),(15,6),(18,7),(21,5),(23,9),(20,12),(21,16),(24,18),(21,22),(17,20),(14,22),(13,24),(8,22),(8,19),(4,18),(2,20),(0,16),(3,13),(3,9),(1,7),(4,3),(8,5)],"#6f89ad"); oval(7,7,17,17,fill="#dceafd",outline=""); oval(10,10,14,14,fill="#526b92",outline="")
        elif name=="alert":
            poly([(2,8),(10,4),(18,8),(10,12)],"#79a9e8"); poly([(2,8),(10,12),(10,21),(2,17)],"#4f7fcb"); poly([(18,8),(10,12),(10,21),(18,17)],"#9cc9f5"); poly([(17,8),(23,21),(11,21)],"#f2a32b"); line(17,12,17,17,fill="#ffffff"); oval(16.5,18.5,17.5,19.5,fill="#ffffff",outline="")
        else:
            poly([(2,8),(10,4),(18,8),(10,12)],"#b9c9dc"); poly([(2,8),(10,12),(10,21),(2,17)],"#8298b2"); poly([(18,8),(10,12),(10,21),(18,17)],"#d8e4ef"); oval(11,8,23,22,fill="#e34a58",outline=""); line(14,12,20,18,fill="#ffffff"); line(20,12,14,18,fill="#ffffff")
        return c
    if name=="home":
        line(3,11,12,3,21,11); line(5,10,5,21,19,21,19,10); rect(10,15,14,21)
    elif name=="package":
        line(4,7,12,3,20,7,12,11,4,7,4,17,12,21,20,17,20,7); line(12,11,12,21)
    elif name=="history":
        line(3,12,6,9,9,12); line(6,9,6,16); oval(5,5,21,21); line(13,9,13,13,16,15)
    elif name=="store":
        line(4,9,5,4,19,4,20,9); line(5,11,5,21,19,21,19,11); rect(9,15,15,21)
    elif name=="settings":
        oval(8,8,16,16); oval(3,3,21,21); line(12,1,12,4,12,20,12,23); line(1,12,4,12,20,12,23,12)
    elif name=="alert":
        line(12,3,22,20,2,20,12,3); line(12,9,12,14); oval(11.2,16.5,12.8,18.1)
    elif name=="x-circle":
        oval(3,3,21,21); line(9,9,15,15); line(15,9,9,15)
    elif name=="search":
        oval(3,3,16,16); line(15,15,21,21)
    elif name=="filter":
        line(3,5,21,5,14,13,14,20,10,18,10,13,3,5)
    elif name=="download":
        line(12,3,12,16); line(7,11,12,16,17,11); line(4,21,20,21)
    elif name=="plus":
        oval(3,3,21,21); line(12,8,12,16); line(8,12,16,12)
    elif name=="bell":
        line(5,17,19,17,17,14,17,10); oval(7,3,17,13); line(10,20,14,20)
    else:
        oval(4,4,20,20); line(12,8,12,13); oval(11.2,16,12.8,17.6)
    return c


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
            if target > 0 and target < minimum: raise ValueError("El stock objetivo debe ser igual o mayor que el stock mínimo, o quedar en 0 si no está configurado.")
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


class MinimumStockDialog(tk.Toplevel):
    def __init__(self, app, product):
        super().__init__(app); self.app=app; self.product=product; self.result=False
        self.title("Definir niveles de stock"); self.geometry("470x320"); self.resizable(False,False)
        self.transient(app); self.grab_set()
        body=ttk.Frame(self,padding=24); body.pack(fill="both",expand=True)
        ttk.Label(body,text=product["name"],style="Section.TLabel").pack(anchor="w")
        unit="bolsas individuales" if product["kind"]=="BAG" else "rollos"
        ttk.Label(body,text=f"Ingresá mínimo y objetivo en {unit}. Usá 0 en ambos para dejarlo sin configurar.",
                  style="Hint.TLabel",wraplength=410).pack(anchor="w",pady=(5,14))
        self.minimum=tk.StringVar(value=str(product["minimum_stock"])); self.target=tk.StringVar(value=str(product["target_stock"]))
        ttk.Label(body,text="Stock mínimo").pack(anchor="w"); ttk.Entry(body,textvariable=self.minimum,width=18).pack(anchor="w",pady=(3,10))
        ttk.Label(body,text="Stock objetivo / tranquilo").pack(anchor="w"); ttk.Entry(body,textvariable=self.target,width=18).pack(anchor="w",pady=(3,0))
        row=ttk.Frame(body); row.pack(anchor="e",pady=(20,0),fill="x")
        ttk.Button(row,text="Cancelar",command=self.destroy).pack(side="right",padx=(7,0))
        ttk.Button(row,text="Guardar niveles",style="Primary.TButton",command=self.save).pack(side="right")

    def save(self):
        try:
            minimum=int(self.minimum.get()); target=int(self.target.get())
            self.app.db.update_stock_levels(self.product["id"],minimum,target)
            self.result=True; self.destroy()
        except (ValueError,StockError) as exc:
            messagebox.showerror("Valor inválido",str(exc) if isinstance(exc,StockError) else "Ingresá un número entero igual o mayor que cero.",parent=self)


class QuickStockDialog(tk.Toplevel):
    def __init__(self,app,product):
        super().__init__(app); self.app=app; self.product=product; self.result=False
        self.title("Edición rápida de stock"); self.geometry("490x390"); self.resizable(False,False); self.transient(app); self.grab_set()
        body=ttk.Frame(self,padding=24); body.pack(fill="both",expand=True)
        ttk.Label(body,text=product["name"],style="Section.TLabel").pack(anchor="w")
        ttk.Label(body,text=f'SKU {product["sku"]} · Los cambios de stock quedan registrados en Movimientos.',style="Hint.TLabel",wraplength=420).pack(anchor="w",pady=(4,16))
        self.stock=tk.StringVar(value=str(product["stock"])); self.minimum=tk.StringVar(value=str(product["minimum_stock"])); self.target=tk.StringVar(value=str(product["target_stock"])); self.reason=tk.StringVar(value="Edición rápida")
        for label,var in [("Stock actual",self.stock),("Stock mínimo",self.minimum),("Stock objetivo",self.target),("Motivo del ajuste",self.reason)]:
            ttk.Label(body,text=label).pack(anchor="w"); ttk.Entry(body,textvariable=var).pack(fill="x",pady=(3,10))
        row=ttk.Frame(body); row.pack(fill="x",pady=(10,0)); ttk.Button(row,text="Cancelar",command=self.destroy).pack(side="right",padx=(7,0)); ttk.Button(row,text="Guardar cambios",style="Primary.TButton",command=self.save).pack(side="right")

    def save(self):
        try:
            stock=int(self.stock.get()); minimum=int(self.minimum.get()); target=int(self.target.get())
            if stock<0: raise StockError("El stock no puede ser negativo.")
            self.app.db.update_stock_levels(self.product["id"],minimum,target)
            if stock!=int(self.product["stock"]): self.app.db.adjust_stock(self.product["id"],stock,self.reason.get().strip() or "Edición rápida")
            self.result=True; self.destroy()
        except (ValueError,StockError) as exc: messagebox.showerror("No se pudo guardar",str(exc) if isinstance(exc,StockError) else "Ingresá números enteros válidos.",parent=self)


class ProductHistoryDialog(tk.Toplevel):
    def __init__(self,app,product):
        super().__init__(app); self.title("Historial del producto"); self.geometry("1040x570"); self.transient(app)
        body=tk.Frame(self,bg=BG,padx=22,pady=20); body.pack(fill="both",expand=True)
        tk.Label(body,text=product["name"],font=("Segoe UI Semibold",20),fg=NAVY,bg=BG).pack(anchor="w")
        tk.Label(body,text=f'SKU {product["sku"]} · Stock actual: {product["stock"]}',font=("Segoe UI",10),fg=MUTED,bg=BG).pack(anchor="w",pady=(3,14))
        tree=app.tree(body,["Fecha y hora","Tipo","Cantidad","Stock anterior","Stock nuevo","Motivo","Origen"],[150,170,85,100,100,260,100]); tree.pack(fill="both",expand=True)
        for m in app.db.movements(product["sku"],"Todos",100): tree.insert("","end",values=[m["created_at"][:19].replace("T"," "),m["movement_type"],f'{m["quantity_delta"]:+}',m["stock_before"],m["stock_after"],m["reason"],m["source"]],tags=("positive" if m["quantity_delta"]>0 else "negative",))


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


class BulkPreviewDialog(tk.Toplevel):
    def __init__(self, app, rows, source_path):
        super().__init__(app); self.app=app; self.rows=rows; self.source_path=source_path
        self.title("Vista previa de carga masiva"); self.geometry("1050x680"); self.transient(app); self.grab_set()
        body=ttk.Frame(self,padding=20); body.pack(fill="both",expand=True)
        counts={status:sum(1 for row in rows if row.status==status) for status in ("VÁLIDO","DUPLICADO","ERROR")}
        ttk.Label(body,text="Vista previa",style="Title.TLabel").pack(anchor="w")
        ttk.Label(body,text=f'Válidos: {counts["VÁLIDO"]}   ·   Duplicados: {counts["DUPLICADO"]}   ·   Errores: {counts["ERROR"]}',style="Section.TLabel").pack(anchor="w",pady=(4,12))
        cols=["Hoja","Fila","SKU","Producto","Resultado","Detalle"]
        tree=app.tree(body,cols,[85,55,130,250,100,360]); tree.pack(fill="both",expand=True)
        tree.tag_configure("valid",foreground=GREEN); tree.tag_configure("duplicate",foreground=AMBER); tree.tag_configure("error",foreground=RED)
        tags={"VÁLIDO":"valid","DUPLICADO":"duplicate","ERROR":"error"}
        for row in rows:tree.insert("","end",values=[row.sheet,row.row_number,row.sku,row.name,row.status,row.message],tags=(tags[row.status],))
        footer=ttk.Frame(body); footer.pack(fill="x",pady=(14,0))
        ttk.Label(footer,text="Solo se importarán las filas válidas. Duplicados y errores permanecerán sin cambios.",style="Hint.TLabel").pack(side="left")
        ttk.Button(footer,text="Cancelar",command=self.destroy).pack(side="right",padx=6)
        button=ttk.Button(footer,text=f'Importar {counts["VÁLIDO"]} productos',style="Primary.TButton",command=self.confirm)
        button.pack(side="right"); button.configure(state="normal" if counts["VÁLIDO"] else "disabled")

    def confirm(self):
        valid=sum(1 for row in self.rows if row.status=="VÁLIDO")
        if not messagebox.askyesno("Confirmar carga",f"¿Importar {valid} productos válidos? Antes se creará una copia de seguridad automática.",parent=self):return
        try:
            count,backup=import_preview(self.app.db,self.rows); self.app.refresh_all(); self.destroy()
            messagebox.showinfo("Carga terminada",f"Se importaron {count} productos.\n\nCopia de seguridad:\n{backup}",parent=self.app)
        except (ValueError,sqlite3.IntegrityError,StockError) as exc:
            messagebox.showerror("No se pudo importar",f"No se importó ningún producto.\n\n{exc}",parent=self)


class StockApp(tk.Tk):
    def __init__(self):
        super().__init__(); self.db=Database(); self.ml=MercadoLibreClient(self.db)
        self.title("8Plast · Gestión de Stock"); self.geometry("1500x880"); self.minsize(1180,720); self.configure(bg=BG)
        self.style_ui(); self.build(); self.refresh_all(); self.protocol("WM_DELETE_WINDOW",self.close_app)
        self._movement_version = None
        self._refresh_job = self.after(5000, self.refresh_external_movements)
        # El procesamiento automático pertenece exclusivamente al trabajador independiente.
        # La interfaz puede consultar manualmente, pero nunca crea un segundo consultor periódico.

    def close_app(self):
        if getattr(self, '_refresh_job', None):
            self.after_cancel(self._refresh_job)
        self.destroy()

    def refresh_external_movements(self):
        """Refresca datos cuando el trabajador independiente registra movimientos."""
        try:
            with self.db.session() as con:
                version = con.execute('SELECT COALESCE(MAX(id),0) FROM stock_movements').fetchone()[0]
            if version != self._movement_version:
                self.refresh_all()
                self._movement_version = version
        except sqlite3.Error:
            pass  # Un bloqueo transitorio se reintenta sin cerrar la interfaz.
        finally:
            self._refresh_job = self.after(5000, self.refresh_external_movements)

    def style_ui(self):
        s=ttk.Style(self); s.theme_use("clam"); s.configure(".",font=("Segoe UI",10),background=BG,foreground=NAVY)
        s.configure("TFrame",background=BG); s.configure("Card.TFrame",background=CARD); s.configure("Header.TFrame",background=CARD); s.configure("TLabel",background=BG)
        s.configure("Card.TLabel",background=CARD); s.configure("Title.TLabel",font=("Segoe UI Semibold",29),foreground=NAVY)
        s.configure("Section.TLabel",font=("Segoe UI Semibold",12),foreground=NAVY); s.configure("Hint.TLabel",font=("Segoe UI",9),foreground=MUTED)
        s.configure("Metric.TLabel",font=("Segoe UI Semibold",36),background=CARD,foreground=NAVY)
        s.configure("TButton",background=CARD,foreground=NAVY,padding=(14,9),borderwidth=1,relief="flat")
        s.map("TButton",background=[("active","#dbe5f2"),("pressed","#cbd9e9")])
        s.configure("Primary.TButton",background=BLUE,foreground="white",padding=(15,10),borderwidth=0); s.map("Primary.TButton",background=[("active","#2868cb")])
        s.configure("Danger.TButton",background="#fce8e8",foreground="#a52d35",padding=(13,8)); s.map("Danger.TButton",background=[("active","#f8d6d8")])
        s.configure("Small.TButton",padding=(9,5),font=("Segoe UI Semibold",9))
        s.configure("ColorTab.TButton",background="#eef2f7",foreground="#536176",padding=(16,8),borderwidth=0,relief="flat")
        s.map("ColorTab.TButton",background=[("active","#e2e8f0")])
        s.configure("SelectedColorTab.TButton",background=BLUE,foreground="white",padding=(16,8),borderwidth=0)
        s.map("SelectedColorTab.TButton",background=[("active","#165ebc")],foreground=[("active","white")])
        s.configure("TEntry",fieldbackground=CARD,padding=8,bordercolor=BORDER,lightcolor=BORDER,darkcolor=BORDER)
        s.configure("TCombobox",fieldbackground=CARD,padding=7,bordercolor=BORDER,arrowcolor=MUTED)
        s.configure("Treeview",rowheight=46,background=CARD,fieldbackground=CARD,borderwidth=0,font=("Segoe UI",9))
        s.configure("Treeview.Heading",font=("Segoe UI Semibold",9),background="#f7f9fc",foreground="#66758a",padding=(8,12),borderwidth=0)
        s.map("Treeview",background=[("selected","#dce9ff")],foreground=[("selected",NAVY)])
        s.configure("Thin.Vertical.TScrollbar",background="#cbd5e1",troughcolor=CARD,borderwidth=0,arrowsize=8,width=8)
        s.configure("Navigation.TNotebook",background=BG,borderwidth=0)
        s.layout("Navigation.TNotebook.Tab",[])

    def build(self):
        shell=tk.Frame(self,bg=BG); shell.pack(fill="both",expand=True)
        sidebar=tk.Frame(shell,bg=SIDEBAR,width=228,highlightthickness=1,highlightbackground=BORDER); sidebar.pack(side="left",fill="y"); sidebar.pack_propagate(False)
        brand=tk.Frame(sidebar,bg=SIDEBAR); brand.pack(fill="x",padx=22,pady=(22,28))
        logo=tk.Canvas(brand,width=42,height=42,bg=SIDEBAR,highlightthickness=0); logo.pack(side="left")
        logo.create_rectangle(5,5,37,37,fill=BLUE,outline=""); logo.create_text(21,21,text="8P",fill="white",font=("Segoe UI Semibold",13))
        identity=tk.Frame(brand,bg=SIDEBAR); identity.pack(side="left",padx=(10,0))
        tk.Label(identity,text="8PLAST",font=("Segoe UI Semibold",13),fg=NAVY,bg=SIDEBAR).pack(anchor="w")
        tk.Label(identity,text="Gestión de stock",font=("Segoe UI",8),fg=MUTED,bg=SIDEBAR).pack(anchor="w")
        main=tk.Frame(shell,bg=BG); main.pack(side="left",fill="both",expand=True)
        header=ttk.Frame(main,style="Header.TFrame",padding=(30,17)); header.pack(fill="x")
        title_box=ttk.Frame(header,style="Header.TFrame"); title_box.pack(side="left")
        self.section_title=ttk.Label(title_box,text="8PLAST",font=("Segoe UI Semibold",16),foreground=NAVY,background=CARD); self.section_title.pack(anchor="w")
        self.section_subtitle=ttk.Label(title_box,text="Gestión de Stock",style="Hint.TLabel"); self.section_subtitle.pack(anchor="w")
        user=tk.Frame(header,bg=CARD); user.pack(side="right")
        icon_canvas(user,"bell",22,MUTED,CARD).pack(side="left",padx=(0,18))
        tk.Label(user,text="8P",font=("Segoe UI Semibold",10),fg="white",bg=BLUE,width=3,pady=7).pack(side="left")
        tk.Label(user,text="Administrador",font=("Segoe UI Semibold",9),fg=NAVY,bg=CARD).pack(side="left",padx=(9,0))
        self.tabs=ttk.Notebook(main,style="Navigation.TNotebook"); self.tabs.pack(fill="both",expand=True,padx=26,pady=20)
        self.dashboard_tab=ttk.Frame(self.tabs,padding=4); self.bags_tab=ttk.Frame(self.tabs,padding=4); self.rolls_tab=ttk.Frame(self.tabs,padding=4); self.replenishment_tab=ttk.Frame(self.tabs,padding=4); self.history_tab=ttk.Frame(self.tabs,padding=4); self.ml_tab=ttk.Frame(self.tabs,padding=4); self.statistics_tab=ttk.Frame(self.tabs,padding=4); self.settings_tab=ttk.Frame(self.tabs,padding=4)
        self.inventory_tab=ttk.Frame(self.tabs,padding=4)
        sections=[("home","Inicio",self.dashboard_tab),("package","Bolsas",self.bags_tab),("rolls","Rollos",self.rolls_tab),("alert","Reposición",self.replenishment_tab),("history","Movimientos",self.history_tab),("store","Mercado Libre",self.ml_tab),("chart","Estadísticas",self.statistics_tab),("settings","Configuración",self.settings_tab),("inventory","Agregar inventario",self.inventory_tab)]
        self.nav_buttons=[]
        for index,(icon,title,frame) in enumerate(sections):
            self.tabs.add(frame,text=title)
            item=tk.Frame(sidebar,bg=SIDEBAR,height=48,cursor="hand2"); item.pack(fill="x",padx=10,pady=3); item.pack_propagate(False)
            marker=tk.Frame(item,bg=SIDEBAR,width=3); marker.pack(side="left",fill="y")
            body=tk.Frame(item,bg=SIDEBAR); body.pack(fill="both",expand=True)
            ico=icon_canvas(body,icon,36,MUTED,SIDEBAR); ico.pack(side="left",padx=(8,8))
            label=tk.Label(body,text=title,font=("Segoe UI Semibold",9),fg=MUTED,bg=SIDEBAR); label.pack(side="left")
            for widget in (item,marker,body,ico,label): widget.bind("<Button-1>",lambda _e,i=index:self.select_section(i))
            self.nav_buttons.append((item,marker,body,ico,label))
        tk.Label(sidebar,text="SOLO LECTURA ML",font=("Segoe UI Semibold",7),fg="#9ca3af",bg=SIDEBAR).pack(side="bottom",pady=18)
        self.build_dashboard(); self.build_products(self.bags_tab,"BAG"); self.build_products(self.rolls_tab,"ROLL"); self.build_replenishment(); self.build_history(); self.build_ml(); self.build_statistics(); self.build_settings(); self.build_inventory()
        self.select_section(0)

    def select_section(self,index):
        self.tabs.select(index)
        for i,(item,marker,body,ico,label) in enumerate(self.nav_buttons):
            active=i==index; bg="#eef6ff" if active else SIDEBAR
            item.configure(bg=bg); marker.configure(bg=BLUE if active else SIDEBAR); body.configure(bg=bg); ico.configure(bg=bg); label.configure(bg=bg,fg=BLUE if active else MUTED)

    def build_inventory(self):
        body=ttk.Frame(self.inventory_tab,padding=24,style="Card.TFrame"); body.pack(fill="both",expand=True)
        ttk.Label(body,text="Agregar inventario",style="Section.TLabel").pack(anchor="w",pady=(0,20))
        ttk.Label(body,text="Sumar stock",style="Section.TLabel").pack(anchor="w")
        ttk.Label(body,text="Agregá las bolsas o rollos que ingresan al saldo actual.",style="Card.TLabel").pack(anchor="w",pady=8)
        ttk.Button(body,text="Agregar stock",style="Primary.TButton",command=lambda:MovementDialog(self,"Entrada de producción")).pack(anchor="w",pady=(0,28))
        ttk.Label(body,text="Actualizar por conteo físico",style="Section.TLabel").pack(anchor="w")
        ttk.Label(body,text="Ingresá el total real contado. El saldo quedará en esa cantidad.",style="Card.TLabel").pack(anchor="w",pady=8)
        ttk.Button(body,text="Guardar stock contado",command=lambda:MovementDialog(self,"Ajuste de inventario")).pack(anchor="w")

    def build_dashboard(self):
        heading=tk.Frame(self.dashboard_tab,bg=BG); heading.pack(fill="x",pady=(0,8))
        titles=tk.Frame(heading,bg=BG); titles.pack(side="left")
        tk.Label(titles,text="Resumen de stock",font=("Segoe UI Semibold",23),fg=NAVY,bg=BG).pack(anchor="w")
        tk.Label(titles,text="Decisiones operativas de inventario y producción",font=("Segoe UI",9),fg=MUTED,bg=BG).pack(anchor="w")
        self.dashboard_clock=tk.Label(heading,text="",font=("Segoe UI",8),fg=MUTED,bg=BG); self.dashboard_clock.pack(side="right",anchor="s",pady=5)
        metrics=tk.Frame(self.dashboard_tab,bg=BG); metrics.pack(fill="x"); self.metric_labels=[]
        cards=[("inventory","TOTAL PRODUCTOS","Activos",BLUE,"Todos"),("inventory","STOCK OK","Sobre objetivo",GREEN,"OK"),("alert","STOCK BAJO","Debajo objetivo",AMBER,"BAJO"),("x-circle","CRÍTICOS / REPONER","Prioridad alta",RED,"REPONER"),("x-circle","SIN STOCK","Stock en cero",RED,"SIN STOCK"),("settings","SIN CONFIGURAR","Faltan niveles",MUTED,"SIN CONFIGURAR")]
        for i,(icon,title,subtitle,color,state_filter) in enumerate(cards):
            self.metric_labels.append(self.build_stat_card(metrics,i,icon,title,subtitle,color,state_filter)); metrics.columnconfigure(i,weight=1,uniform="dashboard-kpi")
        decision=tk.Frame(self.dashboard_tab,bg=BG); decision.pack(fill="x",pady=(8,0)); decision.columnconfigure(0,weight=1); decision.columnconfigure(1,weight=1)
        alert_card=tk.Frame(decision,bg=CARD,highlightthickness=1,highlightbackground=BORDER,padx=15,pady=12); alert_card.grid(row=0,column=0,sticky="nsew",padx=(0,5))
        ah=tk.Frame(alert_card,bg=CARD); ah.pack(fill="x"); tk.Label(ah,text="Alertas de stock",font=("Segoe UI Semibold",12),fg=NAVY,bg=CARD).pack(side="left"); tk.Button(ah,text="Ver todas  →",font=("Segoe UI Semibold",8),fg=BLUE,bg=CARD,relief="flat",command=lambda:self.select_section(3)).pack(side="right")
        tk.Label(alert_card,text="Situación actual del inventario",font=("Segoe UI",8),fg=MUTED,bg=CARD).pack(anchor="w"); self.alerts=tk.Frame(alert_card,bg=CARD); self.alerts.pack(fill="both",expand=True,pady=(5,0))
        production=tk.Frame(decision,bg=CARD,highlightthickness=2,highlightbackground=BLUE,padx=15,pady=12); production.grid(row=0,column=1,sticky="nsew",padx=(5,0))
        ph=tk.Frame(production,bg=CARD); ph.pack(fill="x"); tk.Label(ph,text="Próximo a producir",font=("Segoe UI Semibold",13),fg=NAVY,bg=CARD).pack(side="left"); tk.Button(ph,text="Ver plan completo  →",font=("Segoe UI Semibold",8),fg=BLUE,bg=CARD,relief="flat",command=lambda:self.select_section(3)).pack(side="right")
        tk.Label(production,text="Orden recomendado según ventas y cobertura",font=("Segoe UI",8),fg=MUTED,bg=CARD).pack(anchor="w"); self.dashboard_production=tk.Frame(production,bg=CARD); self.dashboard_production.pack(fill="both",expand=True,pady=(5,0))
        secondary=tk.Frame(self.dashboard_tab,bg=BG); secondary.pack(fill="both",expand=True,pady=(8,0)); secondary.columnconfigure(0,weight=3); secondary.columnconfigure(1,weight=2); secondary.rowconfigure(0,weight=1)
        movement=tk.Frame(secondary,bg=CARD,highlightthickness=1,highlightbackground=BORDER,padx=15,pady=11); movement.grid(row=0,column=0,sticky="nsew",padx=(0,5))
        mh=tk.Frame(movement,bg=CARD); mh.pack(fill="x"); tk.Label(mh,text="Movimientos recientes",font=("Segoe UI Semibold",11),fg=NAVY,bg=CARD).pack(side="left"); tk.Button(mh,text="Ver todos  →",font=("Segoe UI Semibold",8),fg=BLUE,bg=CARD,relief="flat",command=lambda:self.select_section(4)).pack(side="right"); self.dashboard_recent=tk.Frame(movement,bg=CARD); self.dashboard_recent.pack(fill="both",expand=True,pady=(5,0))
        best=tk.Frame(secondary,bg=CARD,highlightthickness=1,highlightbackground=BORDER,padx=15,pady=11); best.grid(row=0,column=1,sticky="nsew",padx=(5,0))
        bh=tk.Frame(best,bg=CARD); bh.pack(fill="x"); tk.Label(bh,text="Best Sellers",font=("Segoe UI Semibold",11),fg=NAVY,bg=CARD).pack(side="left"); tk.Button(bh,text="Ver estadísticas  →",font=("Segoe UI Semibold",8),fg=BLUE,bg=CARD,relief="flat",command=lambda:self.select_section(6)).pack(side="right"); self.dashboard_best=tk.Frame(best,bg=CARD); self.dashboard_best.pack(fill="both",expand=True,pady=(5,0))
        ml_strip=tk.Frame(self.dashboard_tab,bg=BG); ml_strip.pack(fill="x",pady=(7,0)); self.ml_dashboard_labels=[]
        for i,title in enumerate(["Cobertura < 7 días","No vinculados a ML","Publicaciones sin vincular","Última sincronización ML"]):
            card=tk.Frame(ml_strip,bg=CARD,padx=10,pady=5,highlightthickness=1,highlightbackground=BORDER); card.grid(row=0,column=i,sticky="ew",padx=(0 if i==0 else 4,0)); value=tk.Label(card,text="—",font=("Segoe UI Semibold",9),fg=NAVY,bg=CARD); value.pack(side="left"); tk.Label(card,text="  "+title,font=("Segoe UI",7),fg=MUTED,bg=CARD).pack(side="left"); self.ml_dashboard_labels.append(value); ml_strip.columnconfigure(i,weight=1)

    def _build_dashboard_legacy(self):
        heading=tk.Frame(self.dashboard_tab,bg=BG); heading.pack(fill="x",pady=(0,16))
        titles=tk.Frame(heading,bg=BG); titles.pack(side="left")
        tk.Label(titles,text="Resumen de stock",font=("Segoe UI Semibold",28),fg=NAVY,bg=BG).pack(anchor="w")
        tk.Label(titles,text="Vista general del inventario físico de 8PLAST",font=("Segoe UI",10),fg=MUTED,bg=BG).pack(anchor="w",pady=(3,0))
        self.dashboard_clock=tk.Label(heading,text="",font=("Segoe UI",9),fg=MUTED,bg=BG); self.dashboard_clock.pack(side="right",anchor="s",pady=7)
        metrics=tk.Frame(self.dashboard_tab,bg=BG); metrics.pack(fill="x")
        self.metric_labels=[]
        cards=[("inventory","TOTAL PRODUCTOS","Productos activos",BLUE,"Todos"),("inventory","STOCK OK","Sobre el objetivo",GREEN,"OK"),("alert","STOCK BAJO","Debajo del objetivo",AMBER,"BAJO"),("x-circle","CRÍTICOS / REPONER","Prioridad alta",RED,"REPONER"),("x-circle","SIN STOCK","Stock actual en cero",RED,"SIN STOCK"),("settings","SIN CONFIGURAR","Faltan niveles",MUTED,"SIN CONFIGURAR")]
        for i,(icon,title,subtitle,color,state_filter) in enumerate(cards):
            self.metric_labels.append(self.build_stat_card(metrics,i,icon,title,subtitle,color,state_filter,row=0)); metrics.columnconfigure(i,weight=1,uniform="kpi")
        ml_strip=tk.Frame(self.dashboard_tab,bg=BG); ml_strip.pack(fill="x",pady=(3,0)); self.ml_dashboard_labels=[]
        for i,title in enumerate(["Cobertura < 7 días","No vinculados a ML","Publicaciones sin vincular","Última sincronización ML"]):
            card=tk.Frame(ml_strip,bg=CARD,padx=12,pady=8,highlightthickness=1,highlightbackground=BORDER); card.grid(row=0,column=i,sticky="ew",padx=(0 if i==0 else 5,0)); value=tk.Label(card,text="—",font=("Segoe UI Semibold",12),fg=NAVY,bg=CARD); value.pack(anchor="w"); tk.Label(card,text=title,font=("Segoe UI",8),fg=MUTED,bg=CARD).pack(anchor="w"); self.ml_dashboard_labels.append(value); ml_strip.columnconfigure(i,weight=1)
        lower=tk.Frame(self.dashboard_tab,bg=BG); lower.pack(fill="both",expand=True,pady=(18,0)); lower.columnconfigure(0,weight=4); lower.columnconfigure(1,weight=1); lower.rowconfigure(0,weight=1)
        inventory=tk.Frame(lower,bg=CARD,highlightthickness=1,highlightbackground=BORDER); inventory.grid(row=0,column=0,sticky="nsew",padx=(0,10))
        inv_head=tk.Frame(inventory,bg=CARD); inv_head.pack(fill="x",padx=20,pady=(17,12))
        tk.Label(inv_head,text="Estado del inventario",font=("Segoe UI Semibold",15),fg=NAVY,bg=CARD).pack(side="left")
        toolbar=tk.Frame(inventory,bg=CARD); toolbar.pack(fill="x",padx=20,pady=(0,12))
        search_box=tk.Frame(toolbar,bg="#f8fafc",highlightthickness=1,highlightbackground=BORDER); search_box.pack(side="left",fill="x",expand=True)
        icon_canvas(search_box,"search",17,MUTED,"#f8fafc").pack(side="left",padx=(10,3),pady=7)
        self.dashboard_search=tk.StringVar()
        entry=tk.Entry(search_box,textvariable=self.dashboard_search,font=("Segoe UI",9),fg=NAVY,bg="#f8fafc",relief="flat",bd=0); entry.pack(side="left",fill="x",expand=True,padx=(3,10),pady=8)
        self.dashboard_search.trace_add("write",lambda *_:self.refresh_dashboard_inventory())
        for text,icon,command in [("Filtros","filter",lambda:None),("Exportar","download",self.download_template)]:
            b=tk.Button(toolbar,text=text,font=("Segoe UI Semibold",9),fg=NAVY,bg=CARD,activebackground=SECONDARY,relief="flat",bd=1,highlightthickness=1,highlightbackground=BORDER,padx=13,pady=8,command=command); b.pack(side="left",padx=(8,0))
        cols=["Producto","Categoría","Medida","Micrones","Stock","Mínimo","Estado"]
        self.dashboard_inventory=self.tree(inventory,cols,[260,100,95,70,90,75,100]); self.dashboard_inventory.pack(fill="both",expand=True,padx=20,pady=(0,18))
        right=tk.Frame(lower,bg=BG); right.grid(row=0,column=1,sticky="nsew",padx=(10,0)); right.rowconfigure(1,weight=1); right.columnconfigure(0,weight=1)
        quick=tk.Frame(right,bg=CARD,highlightthickness=1,highlightbackground=BORDER,padx=16,pady=14); quick.grid(row=0,column=0,sticky="ew",pady=(0,12))
        tk.Label(quick,text="Acciones rápidas",font=("Segoe UI Semibold",13),fg=NAVY,bg=CARD).pack(anchor="w",pady=(0,9))
        for text,command in [("Agregar producto",lambda:self.new_product("BAG")),("Registrar movimiento",lambda:MovementDialog(self)),("Exportar inventario",self.download_template)]:
            tk.Button(quick,text=text,font=("Segoe UI Semibold",9),fg=BLUE,bg="#f6faff",activebackground="#eaf3ff",relief="flat",bd=0,padx=12,pady=8,anchor="w",command=command).pack(fill="x",pady=3)
        alert_card=tk.Frame(right,bg=CARD,highlightthickness=1,highlightbackground=BORDER,padx=15,pady=14); alert_card.grid(row=1,column=0,sticky="nsew")
        tk.Label(alert_card,text="Alertas de stock",font=("Segoe UI Semibold",13),fg=NAVY,bg=CARD).pack(anchor="w")
        tk.Label(alert_card,text="Ordenadas por prioridad",font=("Segoe UI",8),fg=MUTED,bg=CARD).pack(anchor="w",pady=(2,9))
        holder=tk.Frame(alert_card,bg=CARD); holder.pack(fill="both",expand=True)
        self.alert_canvas=tk.Canvas(holder,bg=CARD,highlightthickness=0,bd=0); scroll=ttk.Scrollbar(holder,orient="vertical",style="Thin.Vertical.TScrollbar",command=self.alert_canvas.yview)
        self.alert_canvas.configure(yscrollcommand=scroll.set); self.alert_canvas.pack(side="left",fill="both",expand=True); scroll.pack(side="right",fill="y")
        self.alerts=tk.Frame(self.alert_canvas,bg=CARD); self.alert_window=self.alert_canvas.create_window((0,0),window=self.alerts,anchor="nw")
        self.alerts.bind("<Configure>",lambda _e:self.alert_canvas.configure(scrollregion=self.alert_canvas.bbox("all"))); self.alert_canvas.bind("<Configure>",lambda e:self.alert_canvas.itemconfigure(self.alert_window,width=e.width))

    def build_stat_card(self,parent,index,icon,title,subtitle,color,state_filter=None,row=0):
        card=tk.Frame(parent,bg=CARD,padx=11,pady=9,highlightthickness=1,highlightbackground=BORDER,cursor="hand2" if state_filter else "arrow"); card.grid(row=row,column=index,sticky="nsew",padx=(0 if index==0 else 5,0),pady=(0,5))
        top=tk.Frame(card,bg=CARD); top.pack(fill="x")
        tint={BLUE:"#e8f1ff",GREEN:"#e8f7ef",AMBER:"#fff2df",RED:"#ffeaec",MUTED:"#eef1f5"}.get(color,"#eef1f5")
        icon_box=tk.Frame(top,bg=tint,padx=1,pady=1); icon_box.pack(side="left"); ico=icon_canvas(icon_box,icon,36,color,tint); ico.pack()
        title_label=tk.Label(top,text=title,font=("Segoe UI Semibold",7),fg=MUTED,bg=CARD,wraplength=105,justify="left"); title_label.pack(side="left",padx=6)
        value=tk.Label(card,text="0",font=("Segoe UI Semibold",23),fg=NAVY,bg=CARD); value.pack(anchor="w",pady=(3,0))
        subtitle_label=tk.Label(card,text=subtitle,font=("Segoe UI",7),fg=MUTED,bg=CARD); subtitle_label.pack(anchor="w")
        if state_filter:
            for widget in (card,top,icon_box,ico,title_label,value,subtitle_label): widget.bind("<Button-1>",lambda _e,s=state_filter:self.open_stock_filter(s))
        return value

    def open_stock_filter(self,state):
        if state=="Todos": self.bag_state.set("Todos"); self.roll_state.set("Todos")
        else:
            self.bag_state.set(state); self.roll_state.set(state)
        self.refresh_products("BAG"); self.refresh_products("ROLL")
        if state in {"BAJO","REPONER","SIN STOCK"}: self.replenishment_state.set(state); self.refresh_replenishment(); self.select_section(3)
        else: self.select_section(1)

    def open_product_from_alert(self,product):
        kind=product["kind"]; search=getattr(self,f"{kind.lower()}_search"); search.set(product["name"])
        getattr(self,f"{kind.lower()}_state").set("Todos"); self.refresh_products(kind); self.select_section(1 if kind=="BAG" else 2)

    def open_product_in_plan(self,product):
        self.replenishment_search.set(product["sku"]); self.replenishment_state.set("Todos"); self.replenishment_kind.set("Todos"); self.refresh_replenishment(); self.select_section(3)

    def build_recent_card(self,parent):
        recent_card=tk.Frame(parent,bg=CARD,padx=20,pady=18); recent_card.grid(row=0,column=0,sticky="nsew",padx=(0,10))
        recent_head=tk.Frame(recent_card,bg=CARD); recent_head.pack(fill="x")
        recent_titles=tk.Frame(recent_head,bg=CARD); recent_titles.pack(side="left")
        tk.Label(recent_titles,text="Últimos movimientos",font=("Segoe UI Semibold",14),fg=NAVY,bg=CARD).pack(anchor="w")
        tk.Label(recent_titles,text="Actividad reciente del inventario físico",font=("Segoe UI",9),fg=MUTED,bg=CARD).pack(anchor="w",pady=(3,0))
        tk.Button(recent_head,text="Ver todos  ›",font=("Segoe UI Semibold",9),fg=BLUE,bg=CARD,activeforeground="#2868cb",activebackground=CARD,relief="flat",bd=0,cursor="hand2",command=lambda:self.select_section(4)).pack(side="right")
        columns=tk.Frame(recent_card,bg="#fafbfd",pady=7); columns.pack(fill="x",pady=(14,0))
        for text,width,expand in [("FECHA",14,False),("PRODUCTO",1,True),("TIPO",17,False),("CAMBIO",9,False),("STOCK",8,False)]:
            label=tk.Label(columns,text=text,font=("Segoe UI Semibold",8),fg="#7b8799",bg="#fafbfd",anchor="w",width=width)
            label.pack(side="left",fill="x" if expand else "none",expand=expand,padx=(0,8) if text!="STOCK" else 0)
        self.recent=tk.Frame(recent_card,bg=CARD); self.recent.pack(fill="both",expand=True)

    def build_alerts_card(self,parent):
        alert_card=tk.Frame(parent,bg=CARD,padx=20,pady=18); alert_card.grid(row=0,column=1,sticky="nsew",padx=(10,0))
        tk.Label(alert_card,text="Alertas de stock",font=("Segoe UI Semibold",14),fg=NAVY,bg=CARD).pack(anchor="w")
        tk.Label(alert_card,text="Productos que requieren atención",font=("Segoe UI",9),fg=MUTED,bg=CARD).pack(anchor="w",pady=(3,12))
        holder=tk.Frame(alert_card,bg=CARD); holder.pack(fill="both",expand=True)
        self.alert_canvas=tk.Canvas(holder,bg=CARD,highlightthickness=0,bd=0)
        scroll=ttk.Scrollbar(holder,orient="vertical",style="Thin.Vertical.TScrollbar",command=self.alert_canvas.yview)
        self.alert_canvas.configure(yscrollcommand=scroll.set)
        self.alert_canvas.pack(side="left",fill="both",expand=True); scroll.pack(side="right",fill="y",padx=(6,0))
        self.alerts=tk.Frame(self.alert_canvas,bg=CARD)
        self.alert_window=self.alert_canvas.create_window((0,0),window=self.alerts,anchor="nw")
        self.alerts.bind("<Configure>",lambda _e:self.alert_canvas.configure(scrollregion=self.alert_canvas.bbox("all")))
        self.alert_canvas.bind("<Configure>",lambda e:self.alert_canvas.itemconfigure(self.alert_window,width=e.width))

    def scroll_alerts(self,event):
        self.alert_canvas.yview_scroll(-1 if event.delta>0 else 1,"units")

    def tree(self,parent,columns,widths):
        tree=ttk.Treeview(parent,columns=columns,show="headings",selectmode="browse")
        numeric={"Stock bolsas","Rollos","Stock mínimo","Objetivo","Faltan","Cantidad faltante","Stock actual","Cantidad","Anterior","Posterior"}
        for c,w in zip(columns,widths): tree.heading(c,text=c,command=lambda column=c:self.sort_tree(tree,column,False)); tree.column(c,width=w,minwidth=55,anchor="e" if c in numeric else "w")
        tree.tag_configure("ok",background="#e9f7ef",foreground="#17613a")
        tree.tag_configure("low",background="#fff6d8",foreground="#7a5700")
        tree.tag_configure("replenish",background="#fdebec",foreground="#8c2530")
        tree.tag_configure("outofstock",background="#fff1f2",foreground="#b4232e")
        tree.tag_configure("unconfigured",background="#f1f3f5",foreground="#59636e")
        tree.tag_configure("inactive",background="#f4f5f7",foreground=MUTED)
        tree.tag_configure("positive",foreground="#237552")
        tree.tag_configure("negative",foreground="#a3434c")
        return tree

    def sort_tree(self,tree,column,reverse):
        def key(item):
            value=tree.set(item,column)
            try:return (0,float(str(value).replace(".","").replace(",",".")))
            except ValueError:return (1,str(value).casefold())
        for index,item in enumerate(sorted(tree.get_children(""),key=key,reverse=reverse)): tree.move(item,"",index)
        tree.heading(column,command=lambda:self.sort_tree(tree,column,not reverse))

    def build_products(self,frame,kind):
        summary=tk.Frame(frame,bg=BG); summary.pack(fill="x",pady=(0,14)); summary_labels=[]
        for i,(title,color) in enumerate([("●  OK",GREEN),("●  BAJO",AMBER),("●  REPONER",RED),("●  SIN STOCK",RED),("●  SIN CONFIGURAR",MUTED)]):
            card=tk.Frame(summary,bg=CARD,padx=14,pady=10,highlightthickness=1,highlightbackground=BORDER); card.grid(row=0,column=i,sticky="ew",padx=(0 if i==0 else 6,0))
            tk.Label(card,text=title,font=("Segoe UI Semibold",9),fg=color,bg=CARD).pack(side="left"); value=tk.Label(card,text="0",font=("Segoe UI Semibold",15),fg=NAVY,bg=CARD); value.pack(side="right",padx=(8,0)); summary_labels.append(value); summary.columnconfigure(i,weight=1)
        content=tk.Frame(frame,bg=CARD,padx=18,pady=17); content.pack(fill="both",expand=True)
        if kind=="BAG":
            color_tabs=tk.Frame(content,bg="#eef2f7",padx=4,pady=4); color_tabs.pack(anchor="w",pady=(0,14))
            self.bag_color=tk.StringVar(value="Todas"); self.bag_color_buttons={}
            for label in BAG_COLOR_TABS:
                button=ttk.Button(color_tabs,text=label,style="ColorTab.TButton",
                    command=lambda value=label:self.set_bag_color(value))
                button.pack(side="left",padx=(0,6)); self.bag_color_buttons[label]=button
            self.bag_color_buttons["Todas"].configure(style="SelectedColorTab.TButton")
        else:
            micron_tabs=tk.Frame(content,bg="#eef2f7",padx=4,pady=4); micron_tabs.pack(anchor="w",pady=(0,14))
            self.roll_microns=tk.StringVar(value="Todos"); self.roll_micron_buttons={}
            for label in ROLL_MICRON_TABS:
                button=ttk.Button(micron_tabs,text=label,style="ColorTab.TButton",
                    command=lambda value=label:self.set_roll_microns(value))
                button.pack(side="left",padx=(0,6)); self.roll_micron_buttons[label]=button
            self.roll_micron_buttons["Todos"].configure(style="SelectedColorTab.TButton")
        tools=tk.Frame(content,bg=CARD); tools.pack(fill="x",pady=(0,13)); search=tk.StringVar(); active=tk.StringVar(value="Activos"); state=tk.StringVar(value="Todos")
        tk.Label(tools,text="⌕",font=("Segoe UI",16),fg=MUTED,bg=CARD).pack(side="left"); ent=ttk.Entry(tools,textvariable=search,width=28); ent.pack(side="left",padx=(5,9))
        ttk.Combobox(tools,textvariable=active,values=["Activos","Inactivos","Todos"],state="readonly",width=12).pack(side="left")
        ttk.Combobox(tools,textvariable=state,values=["Todos","OK","BAJO","REPONER","SIN STOCK","BAJO + REPONER","SIN CONFIGURAR"],state="readonly",width=18).pack(side="left",padx=7)
        ttk.Button(tools,text="Nuevo producto",style="Primary.TButton",command=lambda:self.new_product(kind)).pack(side="right")
        ttk.Button(tools,text="Editar",command=lambda:self.edit_selected(kind)).pack(side="right",padx=7)
        ttk.Button(tools,text="Edición rápida",command=lambda:self.quick_edit_selected(kind)).pack(side="right",padx=7)
        ttk.Button(tools,text="Ver historial",command=lambda:self.product_history(kind)).pack(side="right",padx=7)
        ttk.Button(tools,text="Definir niveles",command=lambda:self.define_minimum(kind)).pack(side="right")
        if kind=="BAG": cols=["SKU","Producto","Medida","Micrones","Stock bolsas","Pack almac.","Packs físicos","Stock mínimo","Objetivo","Faltan"]+[f"x{x}" for x in self.db.presentations()]+["Estado"]
        else: cols=["SKU","Producto","Ancho","Micrones","Metros","Rollos","Stock mínimo","Objetivo","Faltan","Estado"]
        widths=[90,235]+[85]*(len(cols)-2); tree=self.tree(content,cols,widths); tree.pack(fill="both",expand=True)
        setattr(self,f"{kind.lower()}_tree",tree); setattr(self,f"{kind.lower()}_search",search); setattr(self,f"{kind.lower()}_active",active); setattr(self,f"{kind.lower()}_state",state); setattr(self,f"{kind.lower()}_summary",summary_labels)
        search.trace_add("write",lambda *_:self.refresh_products(kind)); active.trace_add("write",lambda *_:self.refresh_products(kind)); state.trace_add("write",lambda *_:self.refresh_products(kind)); tree.bind("<Double-1>",lambda e:self.edit_selected(kind))

    def set_bag_color(self,color):
        if color not in BAG_COLOR_TABS: color="Todas"
        self.bag_color.set(color)
        for label,button in self.bag_color_buttons.items():
            button.configure(style="SelectedColorTab.TButton" if label==color else "ColorTab.TButton")
        self.refresh_products("BAG")

    def set_roll_microns(self,microns):
        if microns not in ROLL_MICRON_TABS: microns="Todos"
        self.roll_microns.set(microns)
        for label,button in self.roll_micron_buttons.items():
            button.configure(style="SelectedColorTab.TButton" if label==microns else "ColorTab.TButton")
        self.refresh_products("ROLL")

    def build_replenishment(self):
        content=tk.Frame(self.replenishment_tab,bg=CARD,padx=20,pady=18); content.pack(fill="both",expand=True)
        tk.Label(content,text="Plan de producción",font=("Segoe UI Semibold",24),fg=NAVY,bg=CARD).pack(anchor="w")
        self.replenishment_count=tk.Label(content,text="",font=("Segoe UI",10),fg=MUTED,bg=CARD); self.replenishment_count.pack(anchor="w",pady=(3,14))
        tools=tk.Frame(content,bg=CARD); tools.pack(fill="x",pady=(0,12))
        self.replenishment_search=tk.StringVar(); self.replenishment_state=tk.StringVar(value="Todos"); self.replenishment_kind=tk.StringVar(value="Todos")
        ttk.Entry(tools,textvariable=self.replenishment_search,width=32).pack(side="left",padx=(0,8))
        ttk.Combobox(tools,textvariable=self.replenishment_state,values=["Todos","SIN STOCK","REPONER","BAJO"],state="readonly",width=15).pack(side="left",padx=4)
        ttk.Combobox(tools,textvariable=self.replenishment_kind,values=["Todos","Bolsas","Rollos"],state="readonly",width=12).pack(side="left",padx=4)
        ttk.Button(tools,text="Limpiar filtros",command=self.clear_replenishment_filters).pack(side="left",padx=7)
        self.replenishment_tree=self.tree(content,["Orden","Producto","Familia","Stock","Cobertura","Venta/día","Tendencia","Sugerido producir","Prioridad","Motivo","Confianza"],[60,245,100,70,90,85,120,115,125,240,80]); self.replenishment_tree.pack(fill="both",expand=True)
        self.replenishment_tree.bind("<Double-1>",lambda _e:self.open_replenishment_product())
        self.replenishment_search.trace_add("write",lambda *_:self.refresh_replenishment()); self.replenishment_state.trace_add("write",lambda *_:self.refresh_replenishment()); self.replenishment_kind.trace_add("write",lambda *_:self.refresh_replenishment())

    def clear_replenishment_filters(self):
        self.replenishment_search.set(""); self.replenishment_state.set("Todos"); self.replenishment_kind.set("Todos")

    def open_replenishment_product(self):
        sel=self.replenishment_tree.selection()
        if not sel:return
        product=self.db.product(int(sel[0])); self.select_section(1 if product["kind"]=="BAG" else 2)
        search=getattr(self,f'{product["kind"].lower()}_search'); search.set(product["name"])

    def build_history(self):
        content=tk.Frame(self.history_tab,bg=CARD,padx=20,pady=18); content.pack(fill="both",expand=True)
        tk.Label(content,text="Historial de movimientos",font=("Segoe UI Semibold",13),fg=NAVY,bg=CARD).pack(anchor="w")
        tk.Label(content,text="Entradas, ventas y ajustes del stock físico",font=("Segoe UI",9),fg=MUTED,bg=CARD).pack(anchor="w",pady=(2,14))
        tools=tk.Frame(content,bg=CARD); tools.pack(fill="x",pady=(0,12)); self.hist_search=tk.StringVar(); self.hist_type=tk.StringVar(value="Todos"); self.hist_period=tk.StringVar(value="Todos"); self.hist_kind=tk.StringVar(value="Todos")
        tk.Label(tools,text="⌕",font=("Segoe UI",16),fg=MUTED,bg=CARD).pack(side="left"); ttk.Entry(tools,textvariable=self.hist_search,width=30).pack(side="left",padx=(5,9))
        ttk.Combobox(tools,textvariable=self.hist_type,values=["Todos",*MOVEMENT_TYPES],state="readonly",width=24).pack(side="left")
        ttk.Combobox(tools,textvariable=self.hist_period,values=["Todos","Hoy","7 días","30 días"],state="readonly",width=10).pack(side="left",padx=7)
        ttk.Combobox(tools,textvariable=self.hist_kind,values=["Todos","BAG","ROLL"],state="readonly",width=10).pack(side="left")
        self.history=self.tree(content,["Fecha y hora","SKU","Producto","Tipo","Cantidad","Anterior","Posterior","Motivo","Origen"],[145,90,210,155,80,75,75,240,80]); self.history.pack(fill="both",expand=True)
        for variable in (self.hist_search,self.hist_type,self.hist_period,self.hist_kind): variable.trace_add("write",lambda *_:self.refresh_history())

    def build_settings(self):
        box=ttk.Frame(self.settings_tab,style="Card.TFrame",padding=22); box.pack(fill="x"); ttk.Label(box,text="Presentaciones de bolsas",style="Section.TLabel").pack(anchor="w")
        ttk.Label(box,text="Las presentaciones calculan disponibilidad sobre el mismo stock maestro; no crean stocks separados.",style="Card.TLabel").pack(anchor="w",pady=(4,12))
        row=ttk.Frame(box,style="Card.TFrame"); row.pack(fill="x"); self.presentation=tk.StringVar(); ttk.Entry(row,textvariable=self.presentation,width=12).pack(side="left"); ttk.Button(row,text="Agregar presentación",command=self.add_presentation).pack(side="left",padx=8); self.presentation_text=ttk.Label(row,style="Card.TLabel"); self.presentation_text.pack(side="left",padx=15)
        backup=ttk.Frame(self.settings_tab,style="Card.TFrame",padding=22); backup.pack(fill="x",pady=16); ttk.Label(backup,text="Copia de seguridad",style="Section.TLabel").pack(anchor="w"); ttk.Label(backup,text=f"Base local: {DB_PATH}",style="Card.TLabel").pack(anchor="w",pady=(4,12)); ttk.Button(backup,text="Crear copia ahora",command=self.backup).pack(anchor="w")

    def build_statistics(self):
        content=tk.Frame(self.statistics_tab,bg=CARD,padx=20,pady=18); content.pack(fill="both",expand=True)
        head=tk.Frame(content,bg=CARD); head.pack(fill="x")
        titles=tk.Frame(head,bg=CARD); titles.pack(side="left"); tk.Label(titles,text="Estadísticas",font=("Segoe UI Semibold",24),fg=NAVY,bg=CARD).pack(anchor="w"); tk.Label(titles,text="Análisis de ventas y rotación · 180 días",font=("Segoe UI",9),fg=MUTED,bg=CARD).pack(anchor="w")
        self.audit_button=ttk.Button(head,text="Actualizar auditoría",style="Primary.TButton",command=self.run_historical_audit); self.audit_button.pack(side="right")
        self.audit_status=tk.Label(content,text="",font=("Segoe UI",9),fg=MUTED,bg=CARD); self.audit_status.pack(anchor="w",pady=(12,10))
        columns=["Ranking","Producto","Categoría","7d","30d","90d","180d","Velocidad ajustada","Tendencia","Stock","Cobertura","Clasificación","Prioridad V2"]
        self.statistics_tree=self.tree(content,columns,[65,250,85,60,70,70,75,105,125,70,85,125,115]); self.statistics_tree.pack(fill="both",expand=True)

    def run_historical_audit(self):
        self.audit_button.configure(state="disabled"); self.audit_status.configure(text="Consultando ventas y procesando 180 días…")
        def work():
            try:
                self.ml.audit_historical_orders()
                self.after(0,lambda:(self.audit_status.configure(text="Auditoría completada"),self.audit_button.configure(state="normal"),self.refresh_statistics(),self.refresh_replenishment(),self.refresh_dashboard()))
            except Exception as exc:self.after(0,lambda e=str(exc):(self.audit_status.configure(text=f"Datos incompletos · {e}"),self.audit_button.configure(state="normal")))
        threading.Thread(target=work,name="8Plast-ML-Historical-Audit",daemon=True).start()

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

    def define_minimum(self,kind):
        p=self.selected_product(kind)
        if not p: messagebox.showwarning("Seleccioná un producto","Elegí un producto de la lista.",parent=self); return
        d=MinimumStockDialog(self,p); self.wait_window(d)
        if d.result:self.refresh_all()

    def quick_edit_selected(self,kind):
        p=self.selected_product(kind)
        if not p: messagebox.showwarning("Seleccioná un producto","Elegí un producto de la lista.",parent=self); return
        d=QuickStockDialog(self,p); self.wait_window(d)
        if d.result:self.refresh_all()

    def product_history(self,kind):
        p=self.selected_product(kind)
        if not p: messagebox.showwarning("Seleccioná un producto","Elegí un producto de la lista.",parent=self); return
        ProductHistoryDialog(self,p)

    def add_presentation(self):
        try:self.db.add_presentation(int(self.presentation.get())); self.presentation.set(""); messagebox.showinfo("Presentación agregada","Se aplicará al reiniciar la aplicación.",parent=self); self.refresh_settings()
        except (ValueError,StockError):messagebox.showerror("Valor inválido","Ingresá una cantidad entera mayor que cero.",parent=self)

    def backup(self):
        default=f"8plast_backup_{datetime.now():%Y%m%d_%H%M}.db"; path=filedialog.asksaveasfilename(title="Guardar copia",initialfile=default,defaultextension=".db",filetypes=[("Base SQLite","*.db")])
        if path:self.db.backup(path); messagebox.showinfo("Copia creada",f"La copia se guardó en:\n{path}",parent=self)

    def bulk_load(self):
        path=filedialog.askopenfilename(title="Seleccionar archivo de productos",filetypes=[("Excel o CSV","*.xlsx *.csv"),("Excel","*.xlsx"),("CSV","*.csv")])
        if not path:return
        try:rows=preview_file(path,self.db); BulkPreviewDialog(self,rows,path)
        except Exception as exc:messagebox.showerror("No se pudo leer el archivo",str(exc),parent=self)

    def download_template(self):
        path=filedialog.asksaveasfilename(title="Guardar plantilla",initialfile="Plantilla_8Plast_Carga_Masiva.xlsx",defaultextension=".xlsx",filetypes=[("Excel","*.xlsx")])
        if not path:return
        try:create_template(path); messagebox.showinfo("Plantilla creada",f"La plantilla se guardó en:\n{path}",parent=self)
        except Exception as exc:messagebox.showerror("No se pudo crear la plantilla",str(exc),parent=self)

    def build_ml(self):
        status=ttk.Frame(self.ml_tab,style="Card.TFrame",padding=18); status.pack(fill="x",pady=(0,14))
        ttk.Label(status,text="Conexión MercadoLibre",style="Section.TLabel").pack(side="left"); self.ml_status=ttk.Label(status,style="Card.TLabel"); self.ml_status.pack(side="left",padx=15)
        ttk.Button(status,text="Configurar conexión",command=lambda:MercadoLibreConfigDialog(self)).pack(side="right")
        self.ml_sync_button=ttk.Button(status,text="Actualizar Mercado Libre",style="Primary.TButton",command=self.sync_ml); self.ml_sync_button.pack(side="right",padx=8)
        content=tk.Frame(self.ml_tab,bg=CARD,padx=18,pady=17); content.pack(fill="both",expand=True)
        tools=tk.Frame(content,bg=CARD); tools.pack(fill="x",pady=(0,12)); self.ml_filter=tk.StringVar(value="Todos")
        ttk.Combobox(tools,textvariable=self.ml_filter,values=["Todos","Vinculados","No vinculados"],state="readonly",width=15).pack(side="left")
        ttk.Button(tools,text="Nueva asociación",style="Primary.TButton",command=self.new_association).pack(side="right")
        ttk.Button(tools,text="Editar",command=self.edit_association).pack(side="right",padx=7)
        cols=["Producto interno / publicación","SKU","Publicaciones","Stock físico","Stock publicado ML","Ventas 7d","Ventas 30d","Venta/día","Cobertura","Estado"]
        self.ml_tree=self.tree(content,cols,[330,110,100,90,130,80,85,85,100,110]); self.ml_tree.pack(fill="both",expand=True); self.ml_tree.bind("<Double-1>",lambda e:self.edit_association())
        self.ml_filter.trace_add("write",lambda *_:self.refresh_ml())
        tk.Label(content,text="Mercado Libre permanece en modo lectura: nunca se modifica el stock publicado.",font=("Segoe UI",9),fg=MUTED,bg=CARD).pack(anchor="w",pady=(10,0))

    def new_association(self):
        d=AssociationDialog(self); self.wait_window(d)
        if d.result:self.refresh_ml()

    def edit_association(self):
        sel=self.ml_tree.selection()
        if not sel:messagebox.showwarning("Seleccioná una asociación","Elegí una fila de la lista.",parent=self); return
        if not str(sel[0]).startswith("l:"): messagebox.showinfo("Detalle del producto","Expandí el producto y elegí una publicación para editar su asociación.",parent=self); return
        assoc=self.db.marketplace_listing_by_id(int(str(sel[0]).split(":",1)[1])); d=AssociationDialog(self,assoc); self.wait_window(d)
        if d.result:self.refresh_ml()

    def refresh_ml(self):
        try:self.ml_status.configure(text=self.ml.connection_status())
        except Exception:self.ml_status.configure(text="Configuración segura no disponible")
        self.ml_tree.delete(*self.ml_tree.get_children())
        analytics,unlinked=self.db.marketplace_analytics(); products=list(self.db.products(active="Activos")); selected=self.ml_filter.get()
        for product in products:
            metric=analytics.get(product["id"],{}); linked=bool(metric.get("linked"))
            if selected=="Vinculados" and not linked or selected=="No vinculados" and linked: continue
            coverage=metric.get("coverage"); velocity=metric.get("velocity",0); listings=metric.get("listings",[])
            parent=f'p:{product["id"]}'; self.ml_tree.insert("","end",iid=parent,open=False,values=[product["name"],product["sku"],len(listings),product["stock"],metric.get("published_stock") if metric.get("published_known") else "Sin dato",metric.get("sales_7",0),metric.get("sales_30",0),f"{velocity:.2f}" if velocity else "Sin ventas",f"{coverage:.1f} días" if coverage is not None else "Sin datos",metric.get("coverage_state") if linked else "NO VINCULADO"],tags=("ok" if linked else "unconfigured",))
            for listing in listings:
                label=f'↳ {listing["listing_id"]}' + (f' / {listing["variation_id"]}' if listing["variation_id"] else "")
                self.ml_tree.insert(parent,"end",iid=f'l:{listing["id"]}',values=[label,"",listing["listing_name"] or listing["presentation_type"],"",listing["last_published_quantity"] if listing["last_published_quantity"] is not None else "Sin dato","","",f'factor {listing["units_consumed"]}',"",listing["sync_mode"]])
        sync=self.db.marketplace_last_sync(); suffix=f' · Última actualización {sync["last_success"][:16].replace("T"," ")}' if sync and sync["last_success"] else " · Sin sincronización"
        if sync and sync["last_error"]: suffix+=" · Datos desactualizados"
        self.ml_status.configure(text=self.ml_status.cget("text")+suffix+f" · {len(unlinked)} publicaciones sin vincular")

    def sync_ml(self):
        self.ml_sync_button.configure(state="disabled"); self.ml_status.configure(text="Actualizando Mercado Libre…")
        def work():
            try:
                self.ml.sync_recent_orders(); self.after(0,lambda:(self.refresh_all(),self.ml_sync_button.configure(state="normal")))
            except Exception as exc:
                self.after(0,lambda e=str(exc):(self.ml_status.configure(text=f"Mercado Libre temporalmente no disponible · {e}"),self.ml_sync_button.configure(state="normal")))
        threading.Thread(target=work,name="8Plast-ML-Manual-Sync",daemon=True).start()

    def status(self,p):
        state=self.db.stock_status(p)
        tags={"OK":"ok","BAJO":"low","REPONER":"replenish","SIN STOCK":"outofstock","SIN CONFIGURAR":"unconfigured"}
        return state,tags[state]

    def refresh_replenishment(self):
        if not hasattr(self,"replenishment_tree"): return
        self.replenishment_tree.delete(*self.replenishment_tree.get_children())
        kind={"Bolsas":"BAG","Rollos":"ROLL"}.get(self.replenishment_kind.get())
        products=[dict(p) for p in self.db.products(kind,self.replenishment_search.get(),"Activos","Todos")]; analytics,_=self.db.marketplace_analytics(); plan=build_production_plan(products,analytics); wanted=self.replenishment_state.get()
        if wanted!="Todos":plan=[p for p in plan if p.get("state")==wanted]
        for p in plan:
            coverage=p.get("coverage"); _,tag=self.status(p)
            self.replenishment_tree.insert("","end",iid=str(p["id"]),values=[f'#{p["production_order"]}',p["name"],p["family_label"],p["stock"],f"{coverage:.1f} días" if coverage is not None else "Sin ventas",f'{p.get("adjusted_velocity",0):.2f}',p.get("trend","—"),f'{p["suggested"]} {p["unit"]}',p["production_priority"],p["reason"],p["confidence"]],tags=(tag,))
        self.replenishment_count.configure(text=f"{sum(p['suggested']>0 for p in plan)} productos con producción sugerida")

    def refresh_products(self,kind):
        tree=getattr(self,f"{kind.lower()}_tree"); tree.delete(*tree.get_children()); pres=self.db.presentations()
        counts=self.db.stock_status_counts(kind)
        for label,key in zip(getattr(self,f"{kind.lower()}_summary"),("ok","low","replenish","out_of_stock","unconfigured")): label.configure(text=str(counts[key] or 0))
        products=self.db.products(kind,getattr(self,f"{kind.lower()}_search").get(),getattr(self,f"{kind.lower()}_active").get(),getattr(self,f"{kind.lower()}_state").get())
        if kind=="BAG": products=filter_bags_by_color(products,self.bag_color.get())
        else: products=filter_rolls_by_microns(products,self.roll_microns.get())
        for p in products:
            state,tag=self.status(p)
            missing=self.db.production_needed(p)
            if not p["active"]:state="INACTIVO"; tag="inactive"
            if kind=="BAG": vals=[p["sku"],p["name"],f'{number(p["width_cm"])}x{number(p["length_cm"])} cm',number(p["microns"]),p["stock"],p["storage_pack"],p["stock"]//p["storage_pack"],p["minimum_stock"],p["target_stock"],missing,*[p["stock"]//x for x in pres],state]
            else: vals=[p["sku"],p["name"],f'{number(p["width_cm"])} cm',number(p["microns"]),number(p["meters_per_roll"]),p["stock"],p["minimum_stock"],p["target_stock"],missing,state]
            tree.insert("", "end", iid=str(p["id"]), values=vals, tags=(tag,))

    def refresh_dashboard(self):
        counts,recent,alerts=self.db.dashboard()
        products=list(self.db.products("BAG","","Activos","Todos"))+list(self.db.products("ROLL","","Activos","Todos"))
        analytics,unlinked=self.db.marketplace_analytics(); sync=self.db.marketplace_last_sync()
        values=(counts["total"] or 0,counts["ok"] or 0,counts["low"] or 0,counts["replenish"] or 0,counts["out_of_stock"] or 0,counts["unconfigured"] or 0)
        for label,val in zip(self.metric_labels,values): label.configure(text=f"{int(val):,}".replace(",","."))
        risk=sum(1 for metric in analytics.values() if metric.get("coverage") is not None and metric["coverage"]<7); not_linked=sum(1 for metric in analytics.values() if not metric.get("linked")); last_sync=sync["last_success"][:16].replace("T"," ") if sync and sync["last_success"] else "Pendiente"
        for label,value in zip(self.ml_dashboard_labels,(risk,not_linked,len(unlinked),last_sync)): label.configure(text=str(value))
        self.dashboard_clock.configure(text=datetime.now().strftime("%d/%m/%Y · %H:%M"))
        self._dashboard_products=products; self.refresh_dashboard_inventory()
        for holder in (self.alerts,self.dashboard_production,self.dashboard_recent,self.dashboard_best):
            for child in holder.winfo_children():child.destroy()
        alerts=sorted(alerts,key=lambda p:({"URGENTE":0,"ALTA":1,"MEDIA":2,"BAJA":3}.get(analytics.get(p["id"],{}).get("priority"),4),-(self.db.production_needed(p) or 0),p["name"].casefold()))[:5]
        for p in alerts:
            metric=analytics.get(p["id"],{}); coverage=metric.get("coverage"); row=tk.Frame(self.alerts,bg=CARD,pady=3,cursor="hand2"); row.pack(fill="x")
            tk.Label(row,text=p["name"],font=("Segoe UI Semibold",8),fg=NAVY,bg=CARD,anchor="w").pack(side="left",fill="x",expand=True)
            tk.Label(row,text=f'Stock {p["stock"]} · {coverage:.1f} d' if coverage is not None else f'Stock {p["stock"]}',font=("Segoe UI",7),fg=MUTED,bg=CARD).pack(side="right")
            for widget in row.winfo_children()+[row]:widget.bind("<Button-1>",lambda _e,product=p:self.open_product_from_alert(product))
        plan=build_production_plan([dict(p) for p in products],analytics); next_production=[p for p in plan if p["production_priority"] not in {"NO REQUIERE PRODUCCIÓN","DATOS INCOMPLETOS"}][:5]
        for index,p in enumerate(next_production,1):
            row=tk.Frame(self.dashboard_production,bg="#f8fbff",pady=3,padx=5,cursor="hand2"); row.pack(fill="x",pady=1)
            tk.Label(row,text=f"#{index}",font=("Segoe UI Semibold",9),fg=BLUE,bg="#f8fbff",width=3).pack(side="left")
            copy=tk.Frame(row,bg="#f8fbff"); copy.pack(side="left",fill="x",expand=True); tk.Label(copy,text=p["name"],font=("Segoe UI Semibold",8),fg=NAVY,bg="#f8fbff",anchor="w").pack(fill="x"); tk.Label(copy,text=f'{p["production_priority"]} · {p["reason"]}',font=("Segoe UI",7),fg=MUTED,bg="#f8fbff",anchor="w").pack(fill="x")
            value=tk.Frame(row,bg="#f8fbff"); value.pack(side="right"); tk.Label(value,text=f'{p["suggested"]:,}'.replace(",",".")+f' {p["unit"]}',font=("Segoe UI Semibold",8),fg=RED if index<=2 else AMBER,bg="#f8fbff").pack(anchor="e"); tk.Label(value,text=f'{p["coverage"]:.1f} días' if p["coverage"] is not None else "Sin ventas",font=("Segoe UI",7),fg=MUTED,bg="#f8fbff").pack(anchor="e")
            for widget in [row,*row.winfo_children(),*copy.winfo_children(),*value.winfo_children()]:widget.bind("<Button-1>",lambda _e,product=p:self.open_product_in_plan(product))
        for m in list(recent)[:6]:
            row=tk.Frame(self.dashboard_recent,bg=CARD,pady=2); row.pack(fill="x"); tk.Label(row,text=m["created_at"][:16].replace("T"," "),font=("Segoe UI",7),fg=MUTED,bg=CARD,width=15,anchor="w").pack(side="left"); tk.Label(row,text=m["product_name"],font=("Segoe UI Semibold",8),fg=NAVY,bg=CARD,anchor="w").pack(side="left",fill="x",expand=True); tk.Label(row,text=f'{m["quantity_delta"]:+}',font=("Segoe UI Semibold",8),fg=GREEN if m["quantity_delta"]>0 else RED,bg=CARD).pack(side="right")
        ranked=sorted(((p,analytics.get(p["id"],{})) for p in products),key=lambda item:item[1].get("sales_180",0),reverse=True)[:5]
        for index,(p,m) in enumerate(ranked,1):
            row=tk.Frame(self.dashboard_best,bg=CARD,pady=3,cursor="hand2"); row.pack(fill="x"); tk.Label(row,text=f"#{index}",font=("Segoe UI Semibold",8),fg=BLUE,bg=CARD,width=3).pack(side="left"); tk.Label(row,text=p["name"],font=("Segoe UI Semibold",8),fg=NAVY,bg=CARD,anchor="w").pack(side="left",fill="x",expand=True); tk.Label(row,text=f'{m.get("sales_180",0):,}'.replace(",",".")+" u.",font=("Segoe UI",8),fg=MUTED,bg=CARD).pack(side="right")

    def refresh_dashboard_inventory(self):
        if not hasattr(self,"dashboard_inventory"): return
        self.dashboard_inventory.delete(*self.dashboard_inventory.get_children())
        query=self.dashboard_search.get().strip().casefold() if hasattr(self,"dashboard_search") else ""
        for p in getattr(self,"_dashboard_products",[]):
            if query and query not in f'{p["name"]} {p["sku"]} {p["width_cm"]} {p["length_cm"]}'.casefold(): continue
            state,tag=self.status(p); category="Rollos" if p["kind"]=="ROLL" else (p["color_material"] or "Bolsas")
            measure=f'{number(p["width_cm"])} cm' if p["kind"]=="ROLL" else f'{number(p["width_cm"])}x{number(p["length_cm"])}'
            label={"OK":"En stock","BAJO":"Stock bajo","REPONER":"Crítico","SIN STOCK":"Sin stock","SIN CONFIGURAR":"Sin configurar"}[state]
            self.dashboard_inventory.insert("","end",values=[p["name"],category,measure,number(p["microns"]),f'{p["stock"]} / {p["target_stock"]}',p["minimum_stock"],label],tags=(tag,))

    def refresh_history(self):
        self.history.delete(*self.history.get_children())
        for m in self.db.movements(self.hist_search.get(),self.hist_type.get(),500,self.hist_period.get(),self.hist_kind.get()):
            self.history.insert("","end",values=[m["created_at"][:19].replace("T"," "),m["sku"],m["product_name"],m["movement_type"],f'{m["quantity_delta"]:+}',m["stock_before"],m["stock_after"],m["reason"],m["source"]])

    def refresh_settings(self): self.presentation_text.configure(text="Actuales: "+", ".join(f"x{x}" for x in self.db.presentations()))
    def refresh_statistics(self):
        if not hasattr(self,"statistics_tree"):return
        self.statistics_tree.delete(*self.statistics_tree.get_children()); metrics,_,audit,_=self.db.marketplace_statistics(); products={p["id"]:p for p in self.db.products(None,"","Activos","Todos")}
        ranked=sorted(((products[pid],metric) for pid,metric in metrics.items() if pid in products),key=lambda item:(item[1].get("sales_180",0),item[1].get("adjusted_velocity",0)),reverse=True)
        for index,(p,m) in enumerate(ranked,1):
            coverage=m.get("coverage"); self.statistics_tree.insert("","end",iid=str(p["id"]),values=[index,p["name"],"Bolsas" if p["kind"]=="BAG" else "Rollos",m.get("sales_7",0),m.get("sales_30",0),m.get("sales_90",0),m.get("sales_180",0),f'{m.get("adjusted_velocity",0):.2f}',m.get("trend","—"),p["stock"],f"{coverage:.1f} días" if coverage is not None else "Sin ventas",m.get("classification","—"),m.get("priority","DATOS INCOMPLETOS")])
        self.audit_status.configure(text=("Auditoría completa" if audit.get("complete") else f'Datos incompletos · estado {audit.get("status","PENDIENTE")}'))
    def refresh_all(self): self.refresh_dashboard(); self.refresh_products("BAG"); self.refresh_products("ROLL"); self.refresh_replenishment(); self.refresh_history(); self.refresh_ml(); self.refresh_statistics(); self.refresh_settings()


if __name__ == "__main__":
    StockApp().mainloop()
