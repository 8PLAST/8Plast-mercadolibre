"""Portal de gestión autenticado y webhook público. No inicia trabajadores WSGI."""
import hmac
import json
import os
import secrets
import sqlite3
import tempfile
import time
from datetime import timedelta, datetime
from pathlib import Path
from flask import abort, flash, redirect, render_template, request, send_file, session
from db import Database, StockError, MOVEMENT_TYPES
from ml_integration import MercadoLibreClient, token_lock
from readonly_portal.app import create_app as create_readonly_app
from runtime_config import database_path, data_dir, sync_owner, file_lock
from webhook_server.storage import store_delivery

def create_app(config=None):
    app=create_readonly_app({'DATABASE_PATH':str(database_path()), **(config or {})})
    app.config.update(SECRET_KEY=os.environ.get('PORTAL_SECRET_KEY'), SESSION_COOKIE_SECURE=True,
        SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax', PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
        MAX_CONTENT_LENGTH=100*1024*1024, CLOUD_PORTAL=True)
    if config: app.config.update(config)
    password=app.config.get('PORTAL_PASSWORD') or os.getenv('PORTAL_PASSWORD','')
    if not app.secret_key or not password: raise RuntimeError('Faltan credenciales del portal.')
    db=Database(app.config['DATABASE_PATH']); app.extensions['stock_db']=db
    client=MercadoLibreClient(db)
    failures={}
    # La app local continúa siendo de solo lectura; sólo esta fábrica habilita gestión.
    app.before_request_funcs[None]=[f for f in app.before_request_funcs.get(None,[]) if f.__name__!='enforce_read_only']

    @app.before_request
    def protect_portal():
        session.setdefault('csrf',secrets.token_urlsafe(32))
        if request.endpoint in ('static','health','mercadolibre_webhook'): return
        if request.endpoint!='login' and not session.get('authenticated'): return redirect('/login')
        if request.method=='POST' and not hmac.compare_digest(request.form.get('csrf',''),session['csrf']): abort(403)

    @app.context_processor
    def common():
        return {'cloud_portal':True,'csrf':session.get('csrf',''),'request_key':secrets.token_urlsafe(24),'owner':sync_owner()}

    @app.route('/login',methods=['GET','POST'])
    def login():
        if request.method=='POST':
            ip=request.remote_addr or 'unknown'; count,at=failures.get(ip,(0,time.time()))
            if time.time()-at>300: count=0; at=time.time()
            if count>=10: abort(429)
            if hmac.compare_digest(request.form.get('password','').encode(),password.encode()):
                failures.pop(ip,None); session.clear(); session['authenticated']=True
                session['csrf']=secrets.token_urlsafe(32); session.permanent=True
                return redirect('/')
            if len(failures)>1024: failures.clear()
            failures[ip]=(count+1,at); flash('Contraseña incorrecta.')
        return render_template('cloud_login.html')

    @app.post('/logout')
    def logout(): session.clear(); return redirect('/login')

    def health():
        try:
            with db.session() as con: con.execute('SELECT 1 FROM products LIMIT 1').fetchone()
            status=json.loads((Path(db.path).parent/'worker_status.json').read_text())
            fresh=0 <= time.time()-status['time'] < 900 and status['state'] in ('standby','running')
            return {'status':'ok' if fresh else 'degraded','worker':status['state'],'sync_enabled':sync_owner()}, 200 if fresh else 503
        except (OSError,ValueError,KeyError,TypeError,sqlite3.Error): return {'status':'starting'},503
    app.view_functions['health']=health

    @app.post('/webhook/mercadolibre')
    def mercadolibre_webhook():
        payload=request.get_json(silent=True)
        if not isinstance(payload,dict): return {'error':'invalid_payload'},400
        try:
            result=store_delivery(payload,Path(db.path))
            return {'status':'received','duplicate':result['duplicate']},200
        except sqlite3.Error: return {'status':'temporary_error'},503

    @app.get('/agregar-inventario')
    def add_inventory():
        return render_template('cloud_inventory.html',page='inventory',products=db.products())

    @app.get('/gestion')
    def manage():
        selected=db.product(request.args.get('product',type=int)) if request.args.get('product') else None
        with db.session() as con:
            pending=[dict(r) for r in con.execute("SELECT order_id,processing_status,last_error FROM marketplace_order_inbox WHERE processing_status IN ('ERROR','UNASSOCIATED','PENDING','CANCEL_REVIEW') ORDER BY date_created DESC LIMIT 200")]
        return render_template('cloud_manage.html',page='management',products=db.products(),selected=selected,
            listings=db.marketplace_listings(),pending=pending,types=MOVEMENT_TYPES,
            processing=db.marketplace_processing_config(),connected=bool(client.secure_config().get('access_token')))

    @app.post('/gestion/accion')
    def action():
        if not sync_owner(): abort(409, 'Railway está en espera. Completá el traspaso antes de modificar datos.')
        f=request.form; kind=f.get('action')
        try:
            pid=int(f.get('product_id') or 0)
            with file_lock(Path(db.path).parent/'management.lock',timeout=30):
                if kind=='product':
                    product={'kind':f['kind'],'sku':f['sku'],'name':f['name'],'width_cm':float(f['width_cm']),
                        'microns':float(f['microns']),'length_cm':float(f['length_cm']) if f['kind']=='BAG' else None,
                        'meters_per_roll':float(f['meters_per_roll']) if f['kind']=='ROLL' else None,
                        'color_material':f.get('color_material',''),'category':f.get('category',''),
                        'minimum_stock':int(f['minimum_stock']),'target_stock':int(f['target_stock']),
                        'storage_pack':int(f.get('storage_pack') or 50),'active':int(f.get('active','1'))}
                    if pid: db.update_product(pid,product)
                    else: db.add_product(product,int(f.get('initial_stock') or 0))
                elif kind=='movement':
                    reason=f.get('reason','').strip()
                    if not reason: raise ValueError('Ingresá el motivo del movimiento.')
                    amount=int(f['amount']); typ=f['movement_type']
                    if typ not in ('Entrada de producción','Entrada manual','Venta','Salida manual','Devolución'): raise ValueError('Movimiento no permitido.')
                    if f.get('storage_packs')=='1':
                        product=db.product(pid)
                        if product['kind']!='BAG': raise ValueError('Los packs físicos corresponden a bolsas.')
                        amount*=product['storage_pack']
                    key=f.get('request_key','')
                    if not key: abort(400)
                    db.move_stock(pid,amount,typ,reason,external_key='WEB:'+key)
                elif kind=='adjust':
                    if not f.get('reason','').strip(): raise ValueError('Ingresá un motivo.')
                    if not f.get('request_key'): abort(400)
                    db.adjust_stock(pid,int(f['target']),f['reason'],external_key='WEB:'+f['request_key'])
                elif kind=='listing':
                    values={'listing_id':f['listing_id'],'variation_id':f.get('variation_id',''),'product_id':pid,
                        'units_consumed':int(f['units_consumed']),'presentation_type':f.get('presentation_type',''),
                        'listing_name':f.get('listing_name',''),'active':int(f.get('active','1')),'sync_mode':'SOLO_DESCONTAR_VENTAS'}
                    if f.get('association_id'): db.update_marketplace_listing(int(f['association_id']),values)
                    else: db.add_marketplace_listing(pid,values['listing_id'],values['units_consumed'],values['variation_id'],values['presentation_type'],values['listing_name'])
                    with db.session() as con:
                        con.execute("UPDATE marketplace_order_inbox SET processing_status='PENDING' WHERE processing_status='UNASSOCIATED'")
                elif kind=='exclude': db.exclude_marketplace_listing(f['listing_id'],f.get('variation_id',''),reason=f.get('reason',''))
                elif kind=='return':
                    if not f.get('reference','').strip(): raise ValueError('Ingresá la referencia única de devolución.')
                    db.process_marketplace_return(f['order_id'],f['listing_id'],f.get('variation_id',''),int(f['quantity']),f['reference'])
                elif kind=='presentation': db.add_presentation(int(f['quantity']))
                elif kind=='audit': db.request_marketplace_audit()
                elif kind=='processing':
                    existing=db.marketplace_processing_config()
                    activation=existing['activation_at'] or f.get('activation_at')
                    if not activation: raise ValueError('Falta fecha de inicio.')
                    db.configure_marketplace_processing(f.get('enabled')=='1',max(60,int(f['interval_seconds'])),activation)
                else: abort(400)
            flash('Guardado correctamente.')
        except (ValueError,sqlite3.IntegrityError,TimeoutError) as exc: flash(str(exc))
        return redirect('/agregar-inventario' if f.get('return_to')=='inventory' else '/gestion')

    @app.post('/gestion/auditoria')
    def backup():
        directory=Path(db.path).parent/'backups'; directory.mkdir(exist_ok=True)
        target=directory/('8plast_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f')+'.db')
        db.backup(target)
        return send_file(target,as_attachment=True,download_name=target.name)

    @app.post('/gestion/importar-productos')
    def import_products():
        if not sync_owner(): abort(409)
        from bulk_import import preview_file,import_preview
        upload=request.files['file']; suffix=Path(upload.filename).suffix.lower()
        if suffix not in ('.xlsx','.csv'): abort(400)
        fd,name=tempfile.mkstemp(suffix=suffix); os.close(fd)
        try:
            upload.save(name)
            with file_lock(Path(db.path).parent/'management.lock',timeout=30):
                rows=preview_file(name,db)
                if any(r.status=='ERROR' for r in rows):
                    flash('No se importó: '+ '; '.join(r.message for r in rows if r.status=='ERROR'))
                else:
                    count,_=import_preview(db,rows); flash(f'Productos importados: {count}. Backup creado.')
        except ValueError as exc: flash(str(exc))
        finally: os.unlink(name)
        return redirect('/gestion')

    @app.post('/gestion/importar-base')
    def import_database():
        if sync_owner(): abort(409,'La importación inicial requiere MELI_SYNC_ENABLED=0.')
        from cloud_import import import_snapshot
        fd,name=tempfile.mkstemp(suffix='.db'); os.close(fd)
        try:
            request.files['file'].save(name)
            import_snapshot(db,Path(name)); flash('Base importada. Se conservaron las notificaciones recibidas en Railway.')
        except (ValueError,sqlite3.Error) as exc: flash(str(exc))
        finally: os.unlink(name)
        return redirect('/gestion')

    @app.post('/oauth/start')
    def oauth_start():
        if not sync_owner(): abort(409,'Autorizá Mercado Libre después de detener el sincronizador local.')
        with token_lock():
            url=client.authorization_url()
            session['oauth_state']=client.secure_config()['oauth_state']
            session['oauth_time']=time.time()
        return redirect(url)

    @app.get('/oauth/callback')
    def oauth_callback():
        if not sync_owner(): abort(409)
        expected=session.pop('oauth_state',None); when=session.pop('oauth_time',0)
        if not expected or time.time()-when>600 or not hmac.compare_digest(request.args.get('state',''),expected): abort(400)
        if not request.args.get('code'): abort(400)
        with token_lock(): client.exchange_code(request.url)
        flash('Mercado Libre autorizado. Los tokens se conservarán en el volumen.')
        return redirect('/gestion')

    return app
