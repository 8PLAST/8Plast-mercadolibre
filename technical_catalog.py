"""One owner-approved technical source for commercial replies.

Publication links are explicit, versioned and private. Titles never silently
select a variant. Corrections/ML attributes cannot mutate this catalog.
"""
import json
import re
from pathlib import Path
from decimal import Decimal

CATALOG_PATH=Path(__file__).parent/'catalog'/'2026-11-03-owner-v1.json'


def load():
    data=json.loads(CATALOG_PATH.read_text())
    if len(data['families'])!=10:raise ValueError('Catálogo incompleto')
    return data


def variant(variant_id):
    data=load()
    for family in data['families']:
        for v in family['variants']:
            if v['id']==variant_id:return data,family,v
    raise ValueError('Variante de catálogo no reconocida')


def initialize(store):
    with store.connect() as con:
        con.executescript('''
        CREATE TABLE IF NOT EXISTS catalog_publication_links (
          id INTEGER PRIMARY KEY, item_id TEXT NOT NULL, variant_id TEXT NOT NULL,
          catalog_version TEXT NOT NULL, confirmed_details TEXT NOT NULL,
          source TEXT NOT NULL, created TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS catalog_discrepancies (
          id INTEGER PRIMARY KEY, item_id TEXT NOT NULL, catalog_version TEXT NOT NULL,
          field TEXT NOT NULL, observed TEXT NOT NULL, expected TEXT NOT NULL,
          recorded TEXT NOT NULL, UNIQUE(item_id,catalog_version,field,observed,expected));
        ''')


def link(store,item_id,variant_id,details,source):
    from assistant_center import now
    if not re.fullmatch(r'MLA\d+',item_id) or not source.strip() or not isinstance(details,dict):
        raise ValueError('Confirmá publicación, detalles y fuente')
    catalog,family,v=variant(variant_id)
    if family['kind']=='rolls':
        if details.get('material') not in ('virgin_ldpe','recovered_ldpe','black_recovered'):
            raise ValueError('Confirmar material exacto del rollo')
        if not details.get('meters_total'):raise ValueError('Confirmar longitud del rollo')
        if v['width_cm']==50 and Decimal(str(details['meters_total']))!=100:
            raise ValueError('Para 50 cm solo está confirmada la longitud de 100 metros')
    initialize(store)
    with store.connect() as con:
        con.execute('INSERT INTO catalog_publication_links(item_id,variant_id,catalog_version,confirmed_details,source,created) VALUES(?,?,?,?,?,?)',
                    (item_id,variant_id,catalog['version'],json.dumps(details),source,now()))


def _thickness(text):
    match=re.search(r'(\d+(?:[.,]\d+)?)\s*(?:mic|µm|μm)',str(text),re.I)
    return Decimal(match[1].replace(',','.')) if match else None


def evidence(store,item_id,public_facts=None):
    from assistant_center import now
    initialize(store)
    with store.connect() as con:
        row=con.execute('SELECT * FROM catalog_publication_links WHERE item_id=? ORDER BY id DESC LIMIT 1',(item_id,)).fetchone()
    catalog=load()
    if not row:return {'version':catalog['version'],'rules':catalog['rules'],'identity_confirmed':False,
                       'warnings':['Falta vincular esta publicación a una variante exacta del catálogo confirmado.'],'knowledge':[]}
    try:_,family,v=variant(row['variant_id'])
    except ValueError:return {'version':catalog['version'],'rules':catalog['rules'],'identity_confirmed':False,
                              'warnings':['El vínculo usa una variante retirada o incompatible; requiere revisión.'],'knowledge':[]}
    details=json.loads(row['confirmed_details'])
    confirmed={'family':family['id'],'family_name':family['name'],'variant_id':v['id'],
               **{k:val for k,val in family.items() if k not in ('variants','id','name')},**v}
    if family['kind']=='rolls':
        confirmed['material']=details['material'];confirmed['meters_total']=details['meters_total']
    for key in ('units_per_pack','publication_title'):
        if key in details:confirmed[key]=details[key]
    warnings=[]
    public_facts=public_facts or {}
    conflicts=[]
    published=_thickness(public_facts.get('espesor_publicado',''))
    expected=v.get('published_microns') or v['real_microns_per_face']
    if published is not None and published!=expected:
        conflicts.append(('espesor_publicado',str(published),str(expected)))
    material=str(public_facts.get('material_publicado','')).lower()
    if confirmed.get('material')=='virgin_ldpe' and re.search(r'recicl|recuper',material):
        conflicts.append(('material_publicado',material,'Polietileno virgen'))
    if family.get('virgin') is False and re.search(r'virgen',material):
        conflicts.append(('material_publicado',material,'Polietileno recuperado'))
    color=str(public_facts.get('color','')).lower()
    if color and family.get('color') and color not in (family['color'],family['color']+'s'):
        conflicts.append(('color',color,family['color']))
    measure=str(public_facts.get('medida',''))
    dims=re.findall(r'(\d+(?:[.,]\d+)?)\s*cm',measure,re.I)
    if family['kind']=='bags' and len(dims)==2:
        observed=[Decimal(x.replace(',','.')) for x in dims]
        if observed!=[Decimal(str(v['width_cm'])),Decimal(str(v['length_cm']))]:
            conflicts.append(('medida',measure,str(v['width_cm'])+' × '+str(v['length_cm'])+' cm'))
    for field,observed,expected_value in conflicts:
        warnings.append('Discrepancia '+field+': publicación '+observed+' / catálogo '+expected_value+'. Revisar sin modificar ficha.')
        with store.connect() as con:
            con.execute('INSERT OR IGNORE INTO catalog_discrepancies(item_id,catalog_version,field,observed,expected,recorded) VALUES(?,?,?,?,?,?)',
                        (item_id,catalog['version'],field,observed,expected_value,now()))
    entry={'id':'catalog:'+v['id'],'source':catalog['source'],'updated':catalog['effective_date'],
           'authority':'owner_catalog','value':confirmed}
    return {'version':catalog['version'],'rules':catalog['rules'],'identity_confirmed':True,
            'warnings':warnings,'knowledge':[entry]}
