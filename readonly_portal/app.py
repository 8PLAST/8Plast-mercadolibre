from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from runtime_config import database_path

from flask import Flask, abort, render_template, request

from stock_rules import priority_sql, state_sql
from marketplace_analytics import marketplace_metrics, marketplace_statistics, PRIORITY_ORDER
from product_planning import build_production_plan, FAMILY_LABELS, PRODUCTION_ORDER


ROOT=Path(__file__).resolve().parents[1]
DEFAULT_DB=ROOT/"8plast_stock.db"
SAFE_METHODS={"GET","HEAD"}
BAG_COLORS={
    "Todas":(),
    "Negras":("negro","negra","negros","negras"),
    "Verdes":("verde","verdes"),
    "Amarillas":("amarillo","amarilla","amarillos","amarillas"),
    "Azules":("azul","azules"),
    "Rojas":("rojo","roja","rojos","rojas"),
    "Polietileno cristal":(),
}
ROLL_MICRONS={"Todos":None,"50 micrones":50,"70 micrones":70}


def connect_readonly(path):
    con=sqlite3.connect(f"file:{Path(path).resolve().as_posix()}?mode=ro",uri=True,timeout=10)
    con.row_factory=sqlite3.Row
    con.execute("PRAGMA query_only=ON")
    return con


def create_app(config=None):
    app=Flask(__name__)
    app.config.update(DATABASE_PATH=str(database_path()))
    if config: app.config.update(config)

    @app.before_request
    def enforce_read_only():
        if request.method not in SAFE_METHODS:
            abort(405)

    @app.after_request
    def security_headers(response):
        response.headers["Cache-Control"]="no-store"
        response.headers["X-Content-Type-Options"]="nosniff"
        response.headers["X-Frame-Options"]="DENY"
        response.headers["Referrer-Policy"]="no-referrer"
        response.headers["Content-Security-Policy"]="default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:"
        response.headers["Permissions-Policy"]="camera=(), microphone=(), geolocation=()"
        return response

    def rows(sql,args=()):
        con=connect_readonly(app.config["DATABASE_PATH"])
        try: return con.execute(sql,args).fetchall()
        finally: con.close()

    def analytics():
        con=connect_readonly(app.config["DATABASE_PATH"])
        try:
            metrics,unlinked=marketplace_metrics(con)
            sync=con.execute("""SELECT MAX(last_success_at) last_success,
                MAX(CASE WHEN last_run_status='ERROR' THEN last_error END) last_error
                FROM marketplace_order_checkpoints WHERE marketplace='MERCADOLIBRE'""").fetchone()
            return metrics,unlinked,sync
        finally: con.close()

    def statistics_data():
        con=connect_readonly(app.config["DATABASE_PATH"])
        try:return marketplace_statistics(con)
        finally:con.close()

    def counts(kind=None):
        where="WHERE active=1"; args=[]
        if kind: where+=" AND kind=?"; args.append(kind)
        return rows(f"""SELECT
          COUNT(*) total,
          SUM(CASE WHEN minimum_stock>0 AND target_stock>0 AND stock>=target_stock THEN 1 ELSE 0 END) ok,
          SUM(CASE WHEN minimum_stock>0 AND target_stock>0 AND stock>minimum_stock AND stock<target_stock THEN 1 ELSE 0 END) low,
          SUM(CASE WHEN minimum_stock>0 AND target_stock>0 AND stock>0 AND stock<=minimum_stock THEN 1 ELSE 0 END) replenish,
          SUM(CASE WHEN minimum_stock>0 AND target_stock>0 AND stock<=0 THEN 1 ELSE 0 END) out_of_stock,
          SUM(CASE WHEN minimum_stock=0 OR target_stock=0 THEN 1 ELSE 0 END) unconfigured
          FROM products {where}""",args)[0]

    @app.get("/health")
    def health(): return {"status":"ok","mode":"read-only"}

    @app.get("/")
    def dashboard():
        recent=rows("""SELECT m.created_at,p.sku,p.name product_name,m.movement_type,m.quantity_delta,
          m.stock_after,m.source FROM stock_movements m JOIN products p ON p.id=m.product_id
          ORDER BY m.id DESC LIMIT 15""")
        alerts=[dict(r) for r in rows(f"""SELECT p.*,{state_sql()} state FROM products p
          WHERE p.active=1 AND p.minimum_stock>0 AND p.target_stock>0 AND p.stock<p.target_stock
          ORDER BY {priority_sql()},(p.target_stock-p.stock) DESC,p.name COLLATE NOCASE""")]
        metrics,unlinked,audit,_=statistics_data(); _,_,sync=analytics()
        for product in alerts: product.update(metrics.get(product["id"],{}))
        alerts.sort(key=lambda p:(PRIORITY_ORDER.get(p.get("priority"),4),-(p["target_stock"]-p["stock"]),p["name"].casefold()))
        total=rows("SELECT COALESCE(SUM(stock),0) total FROM products WHERE active=1")[0]["total"]
        risk=sum(1 for m in metrics.values() if m["coverage"] is not None and m["coverage"]<7)
        product_names={p["id"]:dict(p) for p in rows("SELECT id,name FROM products WHERE active=1")}
        best_sellers=sorted(((product_names[pid],m) for pid,m in metrics.items() if pid in product_names),
                            key=lambda item:item[1]["sales_180"],reverse=True)[:5]
        production_plan=build_production_plan([dict(p) for p in rows("SELECT * FROM products WHERE active=1")],metrics)
        next_production=[p for p in production_plan if p["production_priority"] not in {"NO REQUIERE PRODUCCIÓN","DATOS INCOMPLETOS"}][:5]
        return render_template("dashboard.html",page="dashboard",counts=counts(),recent=recent,alerts=alerts,total_stock=total,
            ml_risk=risk,ml_unlinked=sum(1 for m in metrics.values() if not m["linked"]),ml_last_sync=sync["last_success"],unlinked_publications=len(unlinked),
            audit_complete=audit["complete"],best_sellers=best_sellers,next_production=next_production)

    def products_page(kind):
        search=request.args.get("q","").strip(); state=request.args.get("state","Todos").upper()
        color=request.args.get("color","Todas") if kind=="BAG" else "Todas"
        if color not in BAG_COLORS: color="Todas"
        roll_microns=request.args.get("microns","Todos") if kind=="ROLL" else "Todos"
        if roll_microns not in ROLL_MICRONS: roll_microns="Todos"
        where=["p.kind=?","p.active=1"]; args=[kind]
        if kind=="BAG" and color=="Polietileno cristal":
            where.append("(LOWER(TRIM(p.category))='cristal' OR UPPER(p.sku) LIKE 'CRI-%')")
        elif kind=="BAG" and color!="Todas":
            values=BAG_COLORS[color]
            where.append(f"LOWER(TRIM(p.color_material)) IN ({','.join('?' for _ in values)})")
            args.extend(values)
        if kind=="ROLL" and ROLL_MICRONS[roll_microns] is not None:
            where.append("p.microns=?"); args.append(ROLL_MICRONS[roll_microns])
        if search:
            where.append("(p.name LIKE ? OR p.sku LIKE ? OR p.color_material LIKE ? OR p.category LIKE ?)")
            args.extend([f"%{search}%"]*4)
        expression=state_sql()
        if state in {"OK","BAJO","REPONER","SIN STOCK","SIN CONFIGURAR"}:
            where.append(f"({expression})=?"); args.append(state)
        elif state=="BAJO + REPONER": where.append(f"({expression}) IN ('BAJO','REPONER')")
        order=request.args.get("sort","product"); direction="DESC" if request.args.get("dir")=="desc" else "ASC"
        sortable={"product":"p.name COLLATE NOCASE","stock":"p.stock","minimum":"p.minimum_stock","target":"p.target_stock","missing":"(p.target_stock-p.stock)","state":priority_sql()}
        products=rows(f"""SELECT p.*,{expression} state,
          CASE WHEN p.target_stock>0 THEN MAX(p.target_stock-p.stock,0) END missing FROM products p
          WHERE {' AND '.join(where)} ORDER BY {sortable.get(order,sortable['product'])} {direction},p.name COLLATE NOCASE""",args)
        return render_template("products.html",page="bags" if kind=="BAG" else "rolls",kind=kind,
            products=products,counts=counts(kind),search=search,state=state,color=color,bag_colors=BAG_COLORS,
            roll_microns=roll_microns,roll_micron_tabs=ROLL_MICRONS,sort=order,direction=direction.lower())

    @app.get("/bolsas")
    def bags(): return products_page("BAG")

    @app.get("/rollos")
    def rolls(): return products_page("ROLL")

    @app.get("/reposicion")
    def replenishment():
        search=request.args.get("q","").strip().casefold(); family=request.args.get("family","TODOS"); level=request.args.get("level","Todos"); sort=request.args.get("sort","intelligent")
        metrics,_,_,_=statistics_data(); base=[dict(r) for r in rows(f"SELECT p.*,{state_sql()} state,MAX(p.target_stock-p.stock,0) missing FROM products p WHERE p.active=1")]
        products=build_production_plan(base,metrics)
        family_counts={key:sum(1 for p in products if key=="TODOS" or p["family"]==key) for key in FAMILY_LABELS}
        if family!="TODOS":products=[p for p in products if p["family"]==family]
        if search:products=[p for p in products if search in f'{p["sku"]} {p["name"]}'.casefold()]
        level_map={"Ahora":"FABRICAR AHORA","Alta":"PRIORIDAD ALTA","Media":"PRIORIDAD MEDIA","Puede esperar":"PUEDE ESPERAR"}
        if level in level_map:products=[p for p in products if p["production_priority"]==level_map[level]]
        sorters={"coverage":lambda p:(p["coverage"] if p.get("coverage") is not None else 10**9),"sales":lambda p:-p.get("adjusted_velocity",0),"stock":lambda p:p["stock"],"missing":lambda p:-p["missing"],"product":lambda p:p["name"].casefold()}
        if sort in sorters:products.sort(key=sorters[sort])
        for index,p in enumerate(products,1):p["display_order"]=index
        actionable=[p for p in products if p["production_priority"] not in {"NO REQUIERE PRODUCCIÓN","DATOS INCOMPLETOS"}]
        totals={"bags":sum(p["suggested"] for p in actionable if p["kind"]=="BAG"),"rolls":sum(p["suggested"] for p in actionable if p["kind"]=="ROLL")}
        return render_template("replenishment.html",page="replenishment",products=products,top=actionable[:5],search=request.args.get("q",""),family=family,level=level,sort=sort,families=FAMILY_LABELS,family_counts=family_counts,totals=totals,critical_count=sum(p.get("coverage") is not None and p["coverage"]<7 for p in products))

    @app.get("/mercadolibre")
    def marketplace():
        search=request.args.get("q","").strip().casefold(); link=request.args.get("link","Todos")
        metrics,unlinked,sync=analytics(); products=[dict(r) for r in rows("SELECT * FROM products WHERE active=1 ORDER BY name COLLATE NOCASE")]
        for product in products: product.update(metrics.get(product["id"],{}))
        if search: products=[p for p in products if search in f'{p["sku"]} {p["name"]}'.casefold()]
        if link=="Vinculados": products=[p for p in products if p.get("linked")]
        elif link=="No vinculados": products=[p for p in products if not p.get("linked")]
        return render_template("marketplace.html",page="marketplace",products=products,unlinked=unlinked,sync=sync,search=request.args.get("q",""),link=link)

    @app.get("/estadisticas")
    def statistics():
        metrics,unlinked,audit,monthly=statistics_data(); period=int(request.args.get("period","30"))
        if period not in {7,30,90,180}:period=30
        family=request.args.get("family","TODOS"); classification=request.args.get("classification","Todos"); trend=request.args.get("trend","Todos"); selected_id=request.args.get("product",type=int)
        all_products=build_production_plan([dict(r) for r in rows("SELECT * FROM products WHERE active=1")],metrics)
        family_counts={key:sum(1 for p in all_products if key=="TODOS" or p["family"]==key) for key in FAMILY_LABELS}
        family_products=[p for p in all_products if family=="TODOS" or p["family"]==family]; products=list(family_products)
        if classification!="Todos":products=[p for p in products if p.get("classification")==classification]
        if trend=="Crecimiento":products=[p for p in products if "CRECIMIENTO" in p.get("trend","")]
        elif trend=="Caída":products=[p for p in products if "CAÍDA" in p.get("trend","")]
        elif trend=="Estable":products=[p for p in products if p.get("trend")=="ESTABLE"]
        products.sort(key=lambda p:(-p.get(f"sales_{period}",0),-p.get("adjusted_velocity",0),p["name"].casefold()))
        top=products[:10]; maximum=max([p.get(f"sales_{period}",0) for p in top] or [1]) or 1
        ranked=sorted(family_products,key=lambda p:(-p.get("sales_180",0),-p.get("adjusted_velocity",0)))
        growth=sorted([p for p in family_products if "CRECIMIENTO" in p.get("trend","")],key=lambda p:p.get("trend_change",0),reverse=True)[:5]
        decline=sorted([p for p in family_products if "CAÍDA" in p.get("trend","")],key=lambda p:p.get("trend_change",0))[:5]
        risk=sorted([p for p in family_products if p.get("coverage") is not None],key=lambda p:p["coverage"])[:5]
        overstock=sorted([p for p in family_products if p.get("coverage") is not None and p["coverage"]>120],key=lambda p:p["coverage"],reverse=True)[:5]
        selected=next((p for p in products if p["id"]==selected_id),None)
        filtered_monthly={month:{"TOTAL":sum(p.get("monthly",{}).get(month,0) for p in family_products)} for month in sorted({m for p in family_products for m in p.get("monthly",{})})}
        return render_template("statistics.html",page="statistics",products=products,top=top,maximum=maximum,period=period,
            family=family,families=FAMILY_LABELS,family_counts=family_counts,classification=classification,trend=trend,audit=audit,monthly=filtered_monthly,selected=selected,growth=growth,decline=decline,
            risk=risk,overstock=overstock,best=ranked[:5],unlinked=unlinked,total30=sum(p.get("sales_30",0) for p in family_products),
            total180=sum(p.get("sales_180",0) for p in family_products))

    @app.get("/producto/<int:product_id>/historial")
    def product_history(product_id):
        product=rows(f"SELECT p.*,{state_sql()} state FROM products p WHERE p.id=?",(product_id,))
        if not product: abort(404)
        history=rows("""SELECT * FROM stock_movements WHERE product_id=? ORDER BY id DESC LIMIT 100""",(product_id,))
        metrics,_,_=analytics()
        return render_template("product_history.html",page="history",product=product[0],movements=history,metric=metrics.get(product_id,{}))

    @app.get("/movimientos")
    def movements():
        search=request.args.get("q","").strip(); movement_type=request.args.get("type","Todos"); period=request.args.get("period","Todos"); kind=request.args.get("kind","Todos")
        where=["1=1"]; args=[]
        if search:
            where.append("(p.name LIKE ? OR p.sku LIKE ? OR m.reason LIKE ? OR m.external_key LIKE ?)")
            args.extend([f"%{search}%"]*4)
        if movement_type!="Todos": where.append("m.movement_type=?"); args.append(movement_type)
        if kind in {"BAG","ROLL"}: where.append("p.kind=?"); args.append(kind)
        days={"Hoy":0,"7 días":7,"30 días":30}.get(period)
        if days==0: where.append("date(m.created_at)=date('now','localtime')")
        elif days: where.append("datetime(m.created_at)>=datetime('now',?)"); args.append(f"-{days} days")
        movements=rows(f"""SELECT m.*,p.sku,p.name product_name,p.kind FROM stock_movements m
          JOIN products p ON p.id=m.product_id WHERE {' AND '.join(where)} ORDER BY m.id DESC LIMIT 500""",args)
        types=[r["movement_type"] for r in rows("SELECT DISTINCT movement_type FROM stock_movements ORDER BY movement_type")]
        return render_template("movements.html",page="movements",movements=movements,types=types,
                               search=search,movement_type=movement_type,period=period,kind=kind)

    return app


app=create_app()


if __name__=="__main__":
    app.run(host="127.0.0.1",port=8765,debug=False)
