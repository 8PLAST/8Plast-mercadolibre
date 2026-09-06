from __future__ import annotations

import sqlite3
import json
import re
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from stock_rules import production_needed, stock_state
from marketplace_analytics import marketplace_metrics, marketplace_statistics
from runtime_config import database_path


DB_PATH = database_path()
MOVEMENT_TYPES = (
    "Entrada de producción",
    "Entrada manual",
    "Venta",
    "Ajuste de inventario",
    "Devolución",
    "Salida manual",
    "Devolución MercadoLibre",
    "Cancelación MercadoLibre",
)


class StockError(ValueError):
    pass


def now_text() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class Database:
    def __init__(self, path: str | Path = DB_PATH):
        self.path = str(path)
        self.initialize()

    def connect(self):
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(self.path, timeout=15)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON")
        con.execute("PRAGMA journal_mode = WAL")
        return con

    @contextmanager
    def session(self):
        con = self.connect()
        try:
            yield con
            con.commit()
        finally:
            con.close()

    @contextmanager
    def transaction(self):
        con = self.connect()
        try:
            con.execute("BEGIN IMMEDIATE")
            yield con
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    def initialize(self):
        self._migrate_signed_stock()
        with self.session() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS products (
                    id INTEGER PRIMARY KEY,
                    kind TEXT NOT NULL CHECK(kind IN ('BAG','ROLL')),
                    sku TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    name TEXT NOT NULL,
                    width_cm REAL NOT NULL CHECK(width_cm > 0),
                    length_cm REAL,
                    microns REAL NOT NULL CHECK(microns > 0),
                    meters_per_roll REAL,
                    color_material TEXT NOT NULL DEFAULT '',
                    category TEXT NOT NULL DEFAULT '',
                    stock INTEGER NOT NULL DEFAULT 0,
                    minimum_stock INTEGER NOT NULL DEFAULT 0 CHECK(minimum_stock >= 0),
                    target_stock INTEGER NOT NULL DEFAULT 0 CHECK(target_stock >= 0),
                    storage_pack INTEGER NOT NULL DEFAULT 50 CHECK(storage_pack > 0),
                    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
                    created_at TEXT NOT NULL,
                    CHECK(
                      (kind='BAG' AND length_cm > 0 AND meters_per_roll IS NULL)
                      OR
                      (kind='ROLL' AND meters_per_roll > 0 AND length_cm IS NULL)
                    )
                );

                CREATE TABLE IF NOT EXISTS bag_presentations (
                    id INTEGER PRIMARY KEY,
                    quantity INTEGER NOT NULL UNIQUE CHECK(quantity > 0),
                    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1))
                );

                CREATE TABLE IF NOT EXISTS stock_movements (
                    id INTEGER PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    product_id INTEGER NOT NULL REFERENCES products(id),
                    movement_type TEXT NOT NULL,
                    quantity_delta INTEGER NOT NULL CHECK(quantity_delta <> 0),
                    stock_before INTEGER NOT NULL,
                    stock_after INTEGER NOT NULL,
                    reason TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL DEFAULT 'LOCAL',
                    external_key TEXT UNIQUE
                );

                CREATE TABLE IF NOT EXISTS marketplace_listings (
                    id INTEGER PRIMARY KEY,
                    marketplace TEXT NOT NULL DEFAULT 'MERCADOLIBRE',
                    listing_id TEXT NOT NULL,
                    variation_id TEXT NOT NULL DEFAULT '',
                    product_id INTEGER NOT NULL REFERENCES products(id),
                    units_consumed INTEGER NOT NULL CHECK(units_consumed > 0),
                    presentation_type TEXT NOT NULL DEFAULT '',
                    listing_name TEXT NOT NULL DEFAULT '',
                    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
                    sync_mode TEXT NOT NULL DEFAULT 'SOLO_DESCONTAR_VENTAS'
                        CHECK(sync_mode IN ('SOLO_DESCONTAR_VENTAS','SINCRONIZAR_STOCK')),
                    last_published_quantity INTEGER,
                    last_stock_sent INTEGER,
                    last_synced_at TEXT,
                    sync_from TEXT NOT NULL,
                    UNIQUE(marketplace, listing_id, variation_id)
                );

                CREATE TABLE IF NOT EXISTS marketplace_events (
                    id INTEGER PRIMARY KEY,
                    marketplace TEXT NOT NULL,
                    external_event_id TEXT NOT NULL,
                    received_at TEXT NOT NULL,
                    processed_at TEXT,
                    payload TEXT,
                    UNIQUE(marketplace, external_event_id)
                );

                CREATE TABLE IF NOT EXISTS marketplace_processing_config (
                    id INTEGER PRIMARY KEY CHECK(id=1),
                    enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN (0,1)),
                    interval_seconds INTEGER NOT NULL DEFAULT 300 CHECK(interval_seconds >= 60),
                    activation_at TEXT,
                    updated_at TEXT NOT NULL
                );
                INSERT OR IGNORE INTO marketplace_processing_config
                    (id,enabled,interval_seconds,activation_at,updated_at)
                    VALUES (1,0,300,NULL,'');

                CREATE TABLE IF NOT EXISTS marketplace_order_reviews (
                    id INTEGER PRIMARY KEY,
                    marketplace TEXT NOT NULL DEFAULT 'MERCADOLIBRE',
                    review_key TEXT NOT NULL UNIQUE,
                    order_id TEXT NOT NULL,
                    listing_id TEXT NOT NULL DEFAULT '',
                    variation_id TEXT NOT NULL DEFAULT '',
                    review_type TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'PENDIENTE',
                    reason TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    seen_count INTEGER NOT NULL DEFAULT 1 CHECK(seen_count > 0)
                );
                CREATE INDEX IF NOT EXISTS idx_marketplace_reviews_status
                    ON marketplace_order_reviews(marketplace,status,review_type);

                CREATE TABLE IF NOT EXISTS marketplace_listing_exclusions (
                    id INTEGER PRIMARY KEY,
                    marketplace TEXT NOT NULL DEFAULT 'MERCADOLIBRE',
                    listing_id TEXT NOT NULL,
                    variation_id TEXT NOT NULL DEFAULT '',
                    listing_name TEXT NOT NULL DEFAULT '',
                    reason TEXT NOT NULL DEFAULT '',
                    excluded_at TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
                    UNIQUE(marketplace, listing_id, variation_id)
                );
                CREATE INDEX IF NOT EXISTS idx_marketplace_exclusions_active
                    ON marketplace_listing_exclusions(marketplace, active, listing_id);

                CREATE TABLE IF NOT EXISTS marketplace_order_inbox (
                    id INTEGER PRIMARY KEY,
                    marketplace TEXT NOT NULL DEFAULT 'MERCADOLIBRE',
                    order_id TEXT NOT NULL,
                    date_created TEXT NOT NULL,
                    date_last_updated TEXT,
                    payload_json TEXT NOT NULL,
                    processing_status TEXT NOT NULL DEFAULT 'PENDING',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    fetched_at TEXT NOT NULL,
                    processed_at TEXT,
                    UNIQUE(marketplace, order_id)
                );
                CREATE INDEX IF NOT EXISTS idx_marketplace_inbox_pending
                    ON marketplace_order_inbox(marketplace,processing_status,date_created,order_id);

                CREATE TABLE IF NOT EXISTS marketplace_order_checkpoints (
                    marketplace TEXT NOT NULL DEFAULT 'MERCADOLIBRE',
                    checkpoint_type TEXT NOT NULL CHECK(checkpoint_type IN ('CREATED','UPDATED','PROCESSED')),
                    covered_through TEXT,
                    last_order_id TEXT,
                    last_success_at TEXT,
                    last_run_status TEXT NOT NULL DEFAULT 'NEVER',
                    last_error TEXT,
                    pages_count INTEGER NOT NULL DEFAULT 0,
                    orders_count INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(marketplace,checkpoint_type)
                );

                CREATE TABLE IF NOT EXISTS marketplace_audit_orders (
                    marketplace TEXT NOT NULL DEFAULT 'MERCADOLIBRE',
                    order_id TEXT NOT NULL,
                    date_created TEXT NOT NULL,
                    date_last_updated TEXT,
                    payload_json TEXT NOT NULL,
                    fetched_at TEXT NOT NULL,
                    PRIMARY KEY(marketplace,order_id)
                );
                CREATE INDEX IF NOT EXISTS idx_marketplace_audit_date
                    ON marketplace_audit_orders(marketplace,date_created,order_id);

                CREATE TABLE IF NOT EXISTS marketplace_audit_state (
                    id INTEGER PRIMARY KEY CHECK(id=1),
                    status TEXT NOT NULL DEFAULT 'PENDING',
                    requested_at TEXT,
                    started_at TEXT,
                    completed_at TEXT,
                    period_start TEXT,
                    period_end TEXT,
                    pages_count INTEGER NOT NULL DEFAULT 0,
                    orders_count INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    updated_at TEXT NOT NULL
                );
                INSERT OR IGNORE INTO marketplace_audit_state(id,status,updated_at)
                    VALUES(1,'PENDING','');

                CREATE TABLE IF NOT EXISTS mercadolibre_webhook_events (
                    id INTEGER PRIMARY KEY,
                    event_key TEXT NOT NULL UNIQUE,
                    first_received_at TEXT NOT NULL,
                    last_received_at TEXT NOT NULL,
                    topic TEXT,
                    resource TEXT,
                    user_id TEXT,
                    application_id TEXT,
                    attempts INTEGER,
                    sent TEXT,
                    received TEXT,
                    payload_json TEXT NOT NULL,
                    delivery_count INTEGER NOT NULL DEFAULT 1,
                    is_complete INTEGER NOT NULL DEFAULT 0 CHECK(is_complete IN (0,1)),
                    processing_status TEXT NOT NULL DEFAULT 'RECEIVED'
                );

                CREATE TABLE IF NOT EXISTS mercadolibre_webhook_deliveries (
                    id INTEGER PRIMARY KEY,
                    event_id INTEGER NOT NULL REFERENCES mercadolibre_webhook_events(id),
                    received_at TEXT NOT NULL,
                    attempts INTEGER,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_ml_webhook_status ON mercadolibre_webhook_events(processing_status, id);

                CREATE TABLE IF NOT EXISTS internal_control (
                    id INTEGER PRIMARY KEY CHECK(id=1),
                    allow_stock_change INTEGER NOT NULL DEFAULT 0 CHECK(allow_stock_change IN (0,1))
                );
                INSERT OR IGNORE INTO internal_control(id,allow_stock_change) VALUES(1,0);

                CREATE INDEX IF NOT EXISTS idx_products_kind_active ON products(kind, active);
                CREATE INDEX IF NOT EXISTS idx_movements_product_date ON stock_movements(product_id, created_at DESC);

                CREATE TRIGGER IF NOT EXISTS prevent_stock_without_movement
                BEFORE UPDATE OF stock ON products
                WHEN NEW.stock <> OLD.stock
                     AND (SELECT allow_stock_change FROM internal_control WHERE id=1) <> 1
                BEGIN
                    SELECT RAISE(ABORT, 'El stock solo puede cambiar mediante un movimiento');
                END;
                """
            )
            con.executemany(
                "INSERT OR IGNORE INTO bag_presentations(quantity) VALUES (?)",
                [(50,), (100,), (200,), (300,), (500,)],
            )
            self._migrate(con)

    @staticmethod
    def _columns(con, table: str):
        return {row[1] for row in con.execute(f"PRAGMA table_info({table})")}

    def _migrate_signed_stock(self):
        """Retira sólo las restricciones de saldo; conserva filas, IDs e índices."""
        con = self.connect()
        try:
            schemas = {r['name']: r['sql'] for r in con.execute(
                "SELECT name,sql FROM sqlite_master WHERE type='table' AND name IN ('products','stock_movements')")}
            pattern = r'\s+CHECK\s*\(\s*(?:stock|stock_before|stock_after)\s*>=\s*0\s*\)'
            changes = {name: re.sub(pattern, '', sql, flags=re.I) for name,sql in schemas.items()
                       if re.search(pattern, sql, flags=re.I)}
            if not changes:
                return
            con.execute('PRAGMA foreign_keys=OFF')
            con.execute('BEGIN IMMEDIATE')
            for name, sql in changes.items():
                objects = [r['sql'] for r in con.execute(
                    "SELECT sql FROM sqlite_master WHERE tbl_name=? AND type IN ('index','trigger') AND sql IS NOT NULL", (name,))]
                temporary = name + '_signed_migration'
                new_sql = re.sub(r'^(CREATE TABLE\s+)(?:"'+name+r'"|'+name+r')',
                                 lambda m: m[1] + temporary, sql, count=1, flags=re.I)
                if new_sql == sql:
                    raise StockError('No se pudo identificar la tabla para migrar saldos.')
                con.execute(new_sql)
                columns = ','.join('"'+r['name']+'"' for r in con.execute('PRAGMA table_info('+name+')'))
                con.execute(f'INSERT INTO {temporary} ({columns}) SELECT {columns} FROM {name}')
                con.execute('DROP TABLE '+name)
                con.execute(f'ALTER TABLE {temporary} RENAME TO {name}')
                for statement in objects:
                    con.execute(statement)
            if con.execute('PRAGMA foreign_key_check').fetchone():
                raise StockError('Error de relaciones durante la migración de saldos.')
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    def _migrate(self, con):
        """Migraciones incrementales: nunca recrea productos, stock ni movimientos."""
        product_columns = self._columns(con, "products")
        if "target_stock" not in product_columns:
            con.execute("ALTER TABLE products ADD COLUMN target_stock INTEGER NOT NULL DEFAULT 0 CHECK(target_stock >= 0)")
            con.execute("UPDATE products SET target_stock=minimum_stock")
        if "storage_pack" not in product_columns:
            con.execute("ALTER TABLE products ADD COLUMN storage_pack INTEGER NOT NULL DEFAULT 50 CHECK(storage_pack > 0)")

        listing_columns = self._columns(con, "marketplace_listings")
        required = {"variation_id", "presentation_type", "listing_name", "sync_mode", "last_published_quantity"}
        if not required.issubset(listing_columns):
            con.execute("ALTER TABLE marketplace_listings RENAME TO marketplace_listings_legacy")
            con.execute("""CREATE TABLE marketplace_listings (
                id INTEGER PRIMARY KEY,
                marketplace TEXT NOT NULL DEFAULT 'MERCADOLIBRE',
                listing_id TEXT NOT NULL,
                variation_id TEXT NOT NULL DEFAULT '',
                product_id INTEGER NOT NULL REFERENCES products(id),
                units_consumed INTEGER NOT NULL CHECK(units_consumed > 0),
                presentation_type TEXT NOT NULL DEFAULT '',
                listing_name TEXT NOT NULL DEFAULT '',
                active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
                sync_mode TEXT NOT NULL DEFAULT 'SOLO_DESCONTAR_VENTAS'
                    CHECK(sync_mode IN ('SOLO_DESCONTAR_VENTAS','SINCRONIZAR_STOCK')),
                last_published_quantity INTEGER,
                last_stock_sent INTEGER,
                last_synced_at TEXT,
                sync_from TEXT NOT NULL,
                UNIQUE(marketplace, listing_id, variation_id)
            )""")
            con.execute("""INSERT INTO marketplace_listings
                (id,marketplace,listing_id,variation_id,product_id,units_consumed,presentation_type,listing_name,
                 active,sync_mode,last_published_quantity,last_stock_sent,last_synced_at,sync_from)
                SELECT id,marketplace,listing_id,'',product_id,units_consumed,'','',active,
                       'SOLO_DESCONTAR_VENTAS',NULL,last_stock_sent,last_synced_at,?
                FROM marketplace_listings_legacy""", (now_text(),))
            con.execute("DROP TABLE marketplace_listings_legacy")
        listing_columns = self._columns(con, "marketplace_listings")
        if "sync_from" not in listing_columns:
            migration_time = now_text()
            con.execute("ALTER TABLE marketplace_listings ADD COLUMN sync_from TEXT")
            con.execute("UPDATE marketplace_listings SET sync_from=? WHERE sync_from IS NULL OR sync_from=''", (migration_time,))

    @staticmethod
    def _enable_stock_change(con):
        con.execute("UPDATE internal_control SET allow_stock_change=1 WHERE id=1")

    def add_product(self, data: dict, initial_stock: int = 0, reason: str = "Stock inicial") -> int:
        if initial_stock < 0:
            raise StockError("El stock inicial no puede ser negativo.")
        with self.transaction() as con:
            cur = con.execute(
                """INSERT INTO products
                   (kind,sku,name,width_cm,length_cm,microns,meters_per_roll,color_material,
                    category,stock,minimum_stock,target_stock,storage_pack,active,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,0,?,?,?,1,?)""",
                (
                    data["kind"], data["sku"].strip(), data["name"].strip(), data["width_cm"],
                    data.get("length_cm"), data["microns"], data.get("meters_per_roll"),
                    data.get("color_material", "").strip(), data.get("category", "").strip(),
                    data.get("minimum_stock", 0), data.get("target_stock", 0), data.get("storage_pack", 50), now_text(),
                ),
            )
            product_id = cur.lastrowid
            if initial_stock:
                self._apply_movement(con, product_id, initial_stock, "Entrada manual", reason)
            return product_id

    def bulk_add_products(self, rows: list[dict]) -> list[int]:
        """Importación atómica: o se agregan todos los productos válidos o ninguno."""
        ids=[]
        with self.transaction() as con:
            for data in rows:
                cur=con.execute("""INSERT INTO products
                    (kind,sku,name,width_cm,length_cm,microns,meters_per_roll,color_material,category,
                     stock,minimum_stock,target_stock,storage_pack,active,created_at)
                    VALUES (?,?,?,?,?,?,?,?,?,0,?,?,?,?,?)""",
                    (data["kind"],data["sku"].strip(),data["name"].strip(),data["width_cm"],data.get("length_cm"),
                     data["microns"],data.get("meters_per_roll"),data.get("color_material","").strip(),
                     data.get("category","").strip(),data.get("minimum_stock",0),data.get("target_stock",0),
                     data.get("storage_pack",50),data.get("active",1),now_text()))
                product_id=cur.lastrowid; ids.append(product_id)
                if data.get("initial_stock",0):
                    self._apply_movement(con,product_id,data["initial_stock"],"Entrada manual","Carga masiva · stock inicial")
        return ids

    def update_product(self, product_id: int, data: dict):
        with self.transaction() as con:
            con.execute(
                """UPDATE products SET sku=?,name=?,width_cm=?,length_cm=?,microns=?,
                   meters_per_roll=?,color_material=?,category=?,minimum_stock=?,target_stock=?,storage_pack=?,active=? WHERE id=?""",
                (data["sku"].strip(), data["name"].strip(), data["width_cm"], data.get("length_cm"),
                 data["microns"], data.get("meters_per_roll"), data.get("color_material", "").strip(),
                 data.get("category", "").strip(), data.get("minimum_stock", 0), data.get("target_stock", 0),
                 data.get("storage_pack", 50), data.get("active", 1), product_id),
            )

    def _apply_movement(self, con, product_id: int, delta: int, movement_type: str, reason: str,
                        source: str = "LOCAL", external_key: str | None = None):
        if delta == 0:
            raise StockError("La cantidad debe ser mayor que cero.")
        row = con.execute("SELECT stock FROM products WHERE id=?", (product_id,)).fetchone()
        if not row:
            raise StockError("El producto no existe.")
        before = row["stock"]
        after = before + delta
        ml_sale = source == 'MERCADOLIBRE' and movement_type == 'Venta' and bool(external_key and external_key.startswith('ML:SALE:'))
        if after < 0 and delta < 0 and not ml_sale:
            raise StockError(f"Stock insuficiente. Disponible: {before}.")
        self._enable_stock_change(con)
        con.execute("UPDATE products SET stock=? WHERE id=?", (after, product_id))
        con.execute(
            """INSERT INTO stock_movements
               (created_at,product_id,movement_type,quantity_delta,stock_before,stock_after,reason,source,external_key)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (now_text(), product_id, movement_type, delta, before, after, reason.strip(), source, external_key),
        )
        con.execute("UPDATE internal_control SET allow_stock_change=0 WHERE id=1")
        return after

    def move_stock(self, product_id: int, amount: int, movement_type: str, reason: str,
                   source: str = "LOCAL", external_key: str | None = None) -> int:
        if movement_type not in MOVEMENT_TYPES:
            raise StockError("Tipo de movimiento inválido.")
        if amount <= 0:
            raise StockError("La cantidad debe ser mayor que cero.")
        positive = movement_type in ("Entrada de producción", "Entrada manual", "Devolución")
        delta = amount if positive else -amount
        try:
            with self.transaction() as con:
                return self._apply_movement(con, product_id, delta, movement_type, reason, source, external_key)
        except sqlite3.IntegrityError as exc:
            if external_key and "external_key" in str(exc):
                raise StockError("Ese movimiento externo ya fue procesado.") from exc
            raise

    def add_production(self, product_id: int, quantity: int, as_storage_packs: bool = False,
                       reason: str = "") -> int:
        product = self.product(product_id)
        if not product: raise StockError("El producto no existe.")
        if quantity <= 0: raise StockError("La cantidad debe ser mayor que cero.")
        if as_storage_packs:
            if product["kind"] != "BAG": raise StockError("Los packs físicos solo corresponden a bolsas.")
            quantity *= product["storage_pack"]
        return self.move_stock(product_id, quantity, "Entrada de producción", reason)

    def adjust_stock(self, product_id: int, target: int, reason: str, external_key: str | None = None) -> int:
        if target < 0:
            raise StockError("El stock no puede ser negativo.")
        with self.transaction() as con:
            if external_key and con.execute('SELECT 1 FROM stock_movements WHERE external_key=?',(external_key,)).fetchone():
                raise StockError('Ese ajuste ya fue procesado.')
            row = con.execute("SELECT stock FROM products WHERE id=?", (product_id,)).fetchone()
            if not row:
                raise StockError("El producto no existe.")
            delta = target - row["stock"]
            if delta == 0:
                raise StockError("El stock ya tiene ese valor.")
            return self._apply_movement(con, product_id, delta, "Ajuste de inventario", reason, external_key=external_key)

    @staticmethod
    def stock_status(product) -> str:
        return stock_state(product)

    def products(self, kind: str | None = None, search: str = "", active: str = "Todos",
                 stock_state: str = "Todos"):
        sql = "SELECT * FROM products WHERE 1=1"
        args: list = []
        if kind:
            sql += " AND kind=?"; args.append(kind)
        if search:
            sql += " AND (name LIKE ? OR sku LIKE ? OR color_material LIKE ? OR category LIKE ?)"
            term = f"%{search}%"; args.extend([term] * 4)
        if active == "Activos": sql += " AND active=1"
        elif active == "Inactivos": sql += " AND active=0"
        if stock_state == "SIN CONFIGURAR": sql += " AND (minimum_stock<=0 OR target_stock<=0)"
        elif stock_state == "SIN STOCK": sql += " AND minimum_stock>0 AND target_stock>0 AND stock<=0"
        elif stock_state == "REPONER": sql += " AND minimum_stock>0 AND target_stock>0 AND stock>0 AND stock<=minimum_stock"
        elif stock_state == "BAJO": sql += " AND minimum_stock>0 AND target_stock>0 AND stock>minimum_stock AND stock<target_stock"
        elif stock_state == "OK": sql += " AND minimum_stock>0 AND target_stock>0 AND stock>=target_stock"
        elif stock_state == "BAJO + REPONER":
            sql += " AND minimum_stock>0 AND target_stock>0 AND stock<target_stock"
        sql += " ORDER BY active DESC, name COLLATE NOCASE"
        with self.session() as con:
            return con.execute(sql, args).fetchall()

    def stock_status_counts(self, kind: str | None = None):
        where = "WHERE active=1"; args = []
        if kind:
            where += " AND kind=?"; args.append(kind)
        with self.session() as con:
            return con.execute(f"""SELECT
                COUNT(*) total,
                SUM(CASE WHEN minimum_stock>0 AND target_stock>0 AND stock>=target_stock THEN 1 ELSE 0 END) ok,
                SUM(CASE WHEN minimum_stock>0 AND target_stock>0 AND stock>minimum_stock AND stock<target_stock THEN 1 ELSE 0 END) low,
                SUM(CASE WHEN minimum_stock>0 AND target_stock>0 AND stock>0 AND stock<=minimum_stock THEN 1 ELSE 0 END) replenish,
                SUM(CASE WHEN minimum_stock>0 AND target_stock>0 AND stock<=0 THEN 1 ELSE 0 END) out_of_stock,
                SUM(CASE WHEN minimum_stock=0 OR target_stock=0 THEN 1 ELSE 0 END) unconfigured
                FROM products {where}""", args).fetchone()

    def update_stock_levels(self, product_id: int, minimum_stock: int, target_stock: int):
        if minimum_stock < 0 or target_stock < 0:
            raise StockError("Los niveles de stock no pueden ser negativos.")
        if (minimum_stock == 0) != (target_stock == 0):
            raise StockError("Definí ambos valores, o dejá ambos en 0 para mantenerlo sin configurar.")
        if target_stock and target_stock <= minimum_stock:
            raise StockError("El stock objetivo debe ser mayor que el stock mínimo.")
        with self.session() as con:
            if not con.execute("SELECT 1 FROM products WHERE id=?", (product_id,)).fetchone():
                raise StockError("El producto no existe.")
            con.execute("UPDATE products SET minimum_stock=?,target_stock=? WHERE id=?",
                        (minimum_stock,target_stock,product_id))

    def update_minimum_stock(self, product_id: int, minimum_stock: int):
        """Compatibilidad para integraciones anteriores; conserva el objetivo existente."""
        product = self.product(product_id)
        if not product: raise StockError("El producto no existe.")
        self.update_stock_levels(product_id,minimum_stock,int(product["target_stock"] or 0))

    def product(self, product_id: int):
        with self.session() as con:
            return con.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()

    def presentations(self):
        with self.session() as con:
            return [r["quantity"] for r in con.execute(
                "SELECT quantity FROM bag_presentations WHERE active=1 ORDER BY quantity")]

    def add_presentation(self, quantity: int):
        if quantity <= 0: raise StockError("La presentación debe ser mayor que cero.")
        with self.session() as con:
            con.execute("INSERT OR REPLACE INTO bag_presentations(quantity,active) VALUES (?,1)", (quantity,))

    def movements(self, search: str = "", movement_type: str = "Todos", limit: int = 500,
                  period: str = "Todos", kind: str = "Todos"):
        sql = """SELECT m.*,p.name product_name,p.sku,p.kind FROM stock_movements m
                 JOIN products p ON p.id=m.product_id WHERE 1=1"""
        args: list = []
        if search:
            sql += " AND (p.name LIKE ? OR p.sku LIKE ? OR m.reason LIKE ?)"
            args.extend([f"%{search}%"] * 3)
        if movement_type != "Todos": sql += " AND m.movement_type=?"; args.append(movement_type)
        if kind in {"BAG","ROLL"}: sql += " AND p.kind=?"; args.append(kind)
        if period == "Hoy": sql += " AND date(m.created_at)=date('now','localtime')"
        elif period in {"7 días","30 días"}:
            sql += " AND datetime(m.created_at)>=datetime('now',?)"; args.append(f"-{period.split()[0]} days")
        sql += " ORDER BY m.id DESC LIMIT ?"; args.append(limit)
        with self.session() as con:
            return con.execute(sql, args).fetchall()

    def dashboard(self):
        with self.session() as con:
            counts = con.execute("""SELECT COUNT(*) active, COUNT(*) total,
                SUM(CASE WHEN minimum_stock>0 AND target_stock>0 AND stock>=target_stock THEN 1 ELSE 0 END) ok,
                SUM(CASE WHEN minimum_stock>0 AND target_stock>0 AND stock>minimum_stock AND stock<target_stock THEN 1 ELSE 0 END) low,
                SUM(CASE WHEN minimum_stock>0 AND target_stock>0 AND stock>0 AND stock<=minimum_stock THEN 1 ELSE 0 END) replenish,
                SUM(CASE WHEN minimum_stock>0 AND target_stock>0 AND stock<=0 THEN 1 ELSE 0 END) out_of_stock,
                SUM(CASE WHEN minimum_stock=0 OR target_stock=0 THEN 1 ELSE 0 END) unconfigured
                FROM products WHERE active=1""").fetchone()
            recent = con.execute("""SELECT m.*,p.name product_name FROM stock_movements m
                JOIN products p ON p.id=m.product_id ORDER BY m.id DESC LIMIT 10""").fetchall()
            alerts = con.execute("""SELECT * FROM products WHERE active=1 AND minimum_stock>0 AND target_stock>0
                AND stock<target_stock
                ORDER BY CASE WHEN stock<=minimum_stock THEN 0 ELSE 1 END,
                         CASE WHEN stock<=minimum_stock THEN stock-minimum_stock ELSE stock-minimum_stock END,
                         name COLLATE NOCASE""").fetchall()
            return counts, recent, alerts

    @staticmethod
    def production_needed(product):
        return production_needed(product)

    def add_marketplace_listing(self, product_id: int, listing_id: str, units_consumed: int,
                                variation_id: str = "", presentation_type: str = "", listing_name: str = "",
                                sync_mode: str = "SOLO_DESCONTAR_VENTAS",
                                published_quantity: int | None = None) -> int:
        if sync_mode not in ("SOLO_DESCONTAR_VENTAS", "SINCRONIZAR_STOCK"):
            raise ValueError("Modo de sincronización inválido.")
        with self.session() as con:
            cur = con.execute("""INSERT INTO marketplace_listings
                (listing_id,variation_id,product_id,units_consumed,presentation_type,listing_name,sync_mode,
                 last_published_quantity,sync_from)
                VALUES (?,?,?,?,?,?,?,?,?)""", (listing_id.strip().upper(), variation_id.strip(), product_id,
                units_consumed, presentation_type, listing_name.strip(), sync_mode, published_quantity, now_text()))
            return cur.lastrowid

    def marketplace_listing(self, listing_id: str, variation_id: str = ""):
        with self.session() as con:
            return con.execute("""SELECT * FROM marketplace_listings
                WHERE marketplace='MERCADOLIBRE' AND listing_id=? AND variation_id=?""",
                (listing_id.strip().upper(), variation_id)).fetchone()

    def marketplace_listings(self, active: str = "Todos"):
        sql = """SELECT ml.*,p.name product_name,p.sku,p.kind FROM marketplace_listings ml
                 JOIN products p ON p.id=ml.product_id WHERE 1=1"""
        if active == "Activas": sql += " AND ml.active=1"
        elif active == "Inactivas": sql += " AND ml.active=0"
        sql += " ORDER BY ml.active DESC,ml.listing_id,ml.variation_id"
        with self.session() as con:
            return con.execute(sql).fetchall()

    def exclude_marketplace_listing(self, listing_id: str, variation_id: str = "",
                                    listing_name: str = "", reason: str = ""):
        with self.session() as con:
            con.execute("""INSERT INTO marketplace_listing_exclusions
                (marketplace,listing_id,variation_id,listing_name,reason,excluded_at,active)
                VALUES ('MERCADOLIBRE',?,?,?,?,?,1)
                ON CONFLICT(marketplace,listing_id,variation_id) DO UPDATE SET
                    listing_name=excluded.listing_name, reason=excluded.reason,
                    excluded_at=excluded.excluded_at, active=1""",
                (listing_id.strip().upper(), variation_id.strip(), listing_name.strip(), reason.strip(), now_text()))

    def marketplace_listing_exclusions(self, active_only: bool = True):
        sql = "SELECT * FROM marketplace_listing_exclusions WHERE marketplace='MERCADOLIBRE'"
        if active_only:
            sql += " AND active=1"
        sql += " ORDER BY listing_id,variation_id"
        with self.session() as con:
            return con.execute(sql).fetchall()

    def marketplace_processing_config(self):
        with self.session() as con:
            return con.execute("SELECT * FROM marketplace_processing_config WHERE id=1").fetchone()

    def configure_marketplace_processing(self, enabled: bool, interval_seconds: int = 300,
                                         activation_at: str | None = None):
        if interval_seconds < 60:
            raise ValueError("El intervalo mínimo es de 60 segundos.")
        with self.session() as con:
            con.execute("""UPDATE marketplace_processing_config
                SET enabled=?,interval_seconds=?,activation_at=?,updated_at=? WHERE id=1""",
                (int(enabled), interval_seconds, activation_at, now_text()))

    def marketplace_order_reviews(self, status: str = "PENDIENTE"):
        sql = "SELECT * FROM marketplace_order_reviews WHERE marketplace='MERCADOLIBRE'"
        args = []
        if status:
            sql += " AND status=?"; args.append(status)
        sql += " ORDER BY id DESC"
        with self.session() as con:
            return con.execute(sql,args).fetchall()

    def marketplace_checkpoint(self, checkpoint_type: str):
        with self.session() as con:
            return con.execute("""SELECT * FROM marketplace_order_checkpoints
                WHERE marketplace='MERCADOLIBRE' AND checkpoint_type=?""", (checkpoint_type,)).fetchone()

    def update_marketplace_checkpoint(self, checkpoint_type: str, covered_through: str | None,
                                      last_order_id: str | None = None, status: str = "SUCCESS",
                                      error: str | None = None, pages: int = 0, orders: int = 0):
        with self.session() as con:
            con.execute("""INSERT INTO marketplace_order_checkpoints
                (marketplace,checkpoint_type,covered_through,last_order_id,last_success_at,last_run_status,
                 last_error,pages_count,orders_count,updated_at)
                VALUES ('MERCADOLIBRE',?,?,?,?,?,?,?,?,?)
                ON CONFLICT(marketplace,checkpoint_type) DO UPDATE SET
                  covered_through=COALESCE(excluded.covered_through,marketplace_order_checkpoints.covered_through),
                  last_order_id=COALESCE(excluded.last_order_id,marketplace_order_checkpoints.last_order_id),
                  last_success_at=CASE WHEN excluded.last_run_status='SUCCESS' THEN excluded.last_success_at
                                       ELSE marketplace_order_checkpoints.last_success_at END,
                  last_run_status=excluded.last_run_status,last_error=excluded.last_error,
                  pages_count=excluded.pages_count,orders_count=excluded.orders_count,updated_at=excluded.updated_at""",
                (checkpoint_type,covered_through,last_order_id,now_text() if status == "SUCCESS" else None,
                 status,error,pages,orders,now_text()))

    def store_marketplace_orders(self, orders: list[dict]) -> int:
        """Guarda órdenes antes de procesarlas. Reabre sólo cambios reales de una orden ya resuelta."""
        stored = 0
        with self.transaction() as con:
            for order in orders:
                order_id = str(order.get("id") or "")
                created = str(order.get("date_created") or order.get("date_closed") or "")
                if not order_id or not created:
                    raise StockError("MercadoLibre devolvió una orden sin ID o fecha.")
                updated = str(order.get("date_last_updated") or created)
                payload = json.dumps(order, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                existing = con.execute("""SELECT payload_json,processing_status FROM marketplace_order_inbox
                    WHERE marketplace='MERCADOLIBRE' AND order_id=?""", (order_id,)).fetchone()
                if existing:
                    changed = existing["payload_json"] != payload
                    next_status = "PENDING" if changed else existing["processing_status"]
                    con.execute("""UPDATE marketplace_order_inbox SET date_created=?,date_last_updated=?,
                        payload_json=?,fetched_at=?,processing_status=?,last_error=NULL WHERE order_id=?""",
                        (created,updated,payload,now_text(),next_status,order_id))
                else:
                    con.execute("""INSERT INTO marketplace_order_inbox
                        (marketplace,order_id,date_created,date_last_updated,payload_json,processing_status,
                         attempts,fetched_at) VALUES ('MERCADOLIBRE',?,?,?,?, 'PENDING',0,?)""",
                        (order_id,created,updated,payload,now_text()))
                stored += 1
        return stored

    def process_marketplace_inbox(self) -> dict:
        """Procesa la bandeja sin dejar que una falta de stock bloquee otras ventas."""
        summary = {"pending": 0, "resolved": 0, "processed": 0, "duplicates": 0,
                   "excluded": 0, "unassociated": 0, "cancellations_pending": 0,
                   "cancellation_repeats": 0, "errors": []}
        with self.session() as con:
            rows = con.execute("""SELECT id,order_id,date_created,payload_json FROM marketplace_order_inbox
                WHERE marketplace='MERCADOLIBRE' AND processing_status='PENDING'
                ORDER BY date_created,order_id""").fetchall()
        summary["pending"] = len(rows)
        for row in rows:
            try:
                result = self.process_marketplace_order(json.loads(row["payload_json"]))
                status = "PROCESSED" if result["processed"] else "RESOLVED"
                if result["cancellations_pending"] or result["cancellation_repeats"]:
                    status = "CANCEL_REVIEW"
                elif result["excluded"]:
                    status = "EXCLUDED"
                elif result["unassociated"]:
                    status = "UNASSOCIATED"
                elif result["duplicates"]:
                    status = "DUPLICATE"
                with self.session() as con:
                    con.execute("""UPDATE marketplace_order_inbox SET processing_status=?,attempts=attempts+1,
                        processed_at=?,last_error=NULL WHERE id=?""", (status,now_text(),row["id"]))
                summary["resolved"] += 1
                for key in ("processed","duplicates","cancellations_pending","cancellation_repeats"):
                    summary[key] += result[key]
                summary["excluded"] += len(result["excluded"])
                summary["unassociated"] += len(result["unassociated"])
                if not summary["errors"]:
                    self.update_marketplace_checkpoint("PROCESSED", row["date_created"], row["order_id"])
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                with self.session() as con:
                    con.execute("""UPDATE marketplace_order_inbox SET processing_status='ERROR',
                        attempts=attempts+1,last_error=? WHERE id=?""", (error,row["id"]))
                self.update_marketplace_checkpoint("PROCESSED", None, row["order_id"], "ERROR", error)
                summary["errors"].append({"order_id": row["order_id"], "error": error})
                if not isinstance(exc, StockError):
                    break
        return summary

    def retry_marketplace_inbox_errors(self):
        with self.session() as con:
            con.execute("""UPDATE marketplace_order_inbox SET processing_status='PENDING'
                WHERE marketplace='MERCADOLIBRE' AND processing_status='ERROR'""")

    def marketplace_listing_by_id(self, association_id: int):
        with self.session() as con:
            return con.execute("SELECT * FROM marketplace_listings WHERE id=?", (association_id,)).fetchone()

    def marketplace_analytics(self):
        with self.session() as con:
            return marketplace_metrics(con)

    def marketplace_statistics(self):
        with self.session() as con:
            return marketplace_statistics(con)

    def request_marketplace_audit(self):
        with self.session() as con:
            con.execute("""UPDATE marketplace_audit_state SET status='REQUESTED',requested_at=?,
                last_error=NULL,updated_at=? WHERE id=1""",(now_text(),now_text()))

    def marketplace_audit_state(self):
        with self.session() as con:
            return con.execute("SELECT * FROM marketplace_audit_state WHERE id=1").fetchone()

    def update_marketplace_audit(self, **values):
        allowed={"status","started_at","completed_at","period_start","period_end",
                 "pages_count","orders_count","last_error"}
        fields=[key for key in values if key in allowed]
        if not fields:return
        with self.session() as con:
            sql=",".join(f"{key}=?" for key in fields)+",updated_at=?"
            con.execute(f"UPDATE marketplace_audit_state SET {sql} WHERE id=1",
                        [values[key] for key in fields]+[now_text()])

    def store_marketplace_audit_orders(self, orders):
        stored=0
        with self.session() as con:
            for order in orders:
                order_id=str(order.get("id") or "").strip()
                created=str(order.get("date_created") or order.get("date_closed") or "").strip()
                if not order_id or not created:continue
                updated=str(order.get("date_last_updated") or created)
                con.execute("""INSERT INTO marketplace_audit_orders
                    (marketplace,order_id,date_created,date_last_updated,payload_json,fetched_at)
                    VALUES('MERCADOLIBRE',?,?,?,?,?)
                    ON CONFLICT(marketplace,order_id) DO UPDATE SET
                    date_created=excluded.date_created,date_last_updated=excluded.date_last_updated,
                    payload_json=excluded.payload_json,fetched_at=excluded.fetched_at""",
                    (order_id,created,updated,json.dumps(order,ensure_ascii=False),now_text()))
                stored+=1
        return stored

    def marketplace_last_sync(self):
        with self.session() as con:
            return con.execute("""SELECT MAX(last_success_at) last_success,
                MAX(CASE WHEN last_run_status='ERROR' THEN last_error END) last_error
                FROM marketplace_order_checkpoints WHERE marketplace='MERCADOLIBRE'""").fetchone()

    def update_marketplace_listing(self, association_id: int, data: dict):
        if data["sync_mode"] not in ("SOLO_DESCONTAR_VENTAS", "SINCRONIZAR_STOCK"):
            raise ValueError("Modo de sincronización inválido.")
        with self.session() as con:
            con.execute("""UPDATE marketplace_listings SET listing_id=?,variation_id=?,product_id=?,
                units_consumed=?,presentation_type=?,listing_name=?,active=?,sync_mode=? WHERE id=?""",
                (data["listing_id"].strip().upper(), data.get("variation_id", "").strip(), data["product_id"],
                 data["units_consumed"], data.get("presentation_type", "").strip(),
                 data.get("listing_name", "").strip(), data.get("active", 1), data["sync_mode"], association_id))

    def _association_for_item(self, con, listing_id: str, variation_id: str):
        return con.execute("""SELECT ml.*,p.kind,p.name product_name FROM marketplace_listings ml
            JOIN products p ON p.id=ml.product_id
            WHERE ml.marketplace='MERCADOLIBRE' AND ml.listing_id=? AND ml.variation_id=? AND ml.active=1""",
            (listing_id.upper(), variation_id)).fetchone()

    def process_marketplace_order(self, order: dict) -> dict:
        """Aplica una orden obtenida de ML. Es idempotente y nunca cambia cantidades publicadas."""
        order_id = str(order.get("id", ""))
        if not order_id: raise StockError("La orden no tiene ID.")
        status = str(order.get("status", "")).lower()
        is_cancelled = status == "cancelled"
        result = {"processed": 0, "duplicates": 0, "skipped_before_start": 0,
                  "skipped_missing_date": 0, "unassociated": [], "excluded": [],
                  "cancellations_pending": 0, "cancellation_repeats": 0, "cancelled": is_cancelled}
        order_date = self._parse_external_date(order.get("date_created") or order.get("date_closed"))
        for index, order_item in enumerate(order.get("order_items") or []):
            item = order_item.get("item") or {}
            listing_id = str(item.get("id") or "").upper()
            variation_id = str(item.get("variation_id") or "")
            quantity = int(order_item.get("quantity") or 0)
            with self.transaction() as con:
                excluded = con.execute("""SELECT 1 FROM marketplace_listing_exclusions
                    WHERE marketplace='MERCADOLIBRE' AND listing_id=? AND variation_id=? AND active=1""",
                    (listing_id, variation_id)).fetchone()
                if excluded:
                    result["excluded"].append(f"{listing_id}/{variation_id}" if variation_id else listing_id)
                    continue
                assoc = self._association_for_item(con, listing_id, variation_id)
                if not assoc:
                    result["unassociated"].append(f"{listing_id}/{variation_id}" if variation_id else listing_id)
                    continue
                if is_cancelled:
                    review_key = f"ML:CANCEL-REVIEW:{order_id}:{listing_id}:{variation_id}:{index}"
                    existing_review = con.execute("SELECT id FROM marketplace_order_reviews WHERE review_key=?",(review_key,)).fetchone()
                    if existing_review:
                        con.execute("UPDATE marketplace_order_reviews SET last_seen_at=?,seen_count=seen_count+1 WHERE id=?",
                                    (now_text(),existing_review["id"]))
                        result["cancellation_repeats"] += 1
                    else:
                        con.execute("""INSERT INTO marketplace_order_reviews
                            (marketplace,review_key,order_id,listing_id,variation_id,review_type,status,reason,
                             created_at,last_seen_at,seen_count)
                            VALUES ('MERCADOLIBRE',?,?,?,?,?,'PENDIENTE',?,?,?,1)""",
                            (review_key,order_id,listing_id,variation_id,"CANCELACION",
                             "Cancelación sin reposición automática de stock",now_text(),now_text()))
                        result["cancellations_pending"] += 1
                    continue
                else:
                    sync_from = self._parse_external_date(assoc["sync_from"])
                    config = con.execute("SELECT activation_at FROM marketplace_processing_config WHERE id=1").fetchone()
                    activation_at = self._parse_external_date(config["activation_at"]) if config and config["activation_at"] else None
                    effective_start = max([d for d in (sync_from,activation_at) if d is not None],default=None)
                    if order_date is None:
                        result["skipped_missing_date"] += 1
                        continue
                    if effective_start is not None and order_date < effective_start:
                        result["skipped_before_start"] += 1
                        continue
                line_key = f"{order_id}:{listing_id}:{variation_id}:{index}"
                sale_key = f"ML:SALE:{line_key}"
                if status not in ("paid", "confirmed"):
                    continue
                if con.execute("SELECT 1 FROM stock_movements WHERE external_key=?", (sale_key,)).fetchone():
                    result["duplicates"] += 1
                    continue
                physical = assoc["units_consumed"] * quantity
                self._apply_movement(con, assoc["product_id"], -physical, "Venta",
                    f"Orden {order_id} · Publicación {listing_id} · {quantity} unidad(es)",
                    "MERCADOLIBRE", sale_key)
                con.execute("UPDATE marketplace_listings SET last_synced_at=? WHERE id=?", (now_text(), assoc["id"]))
                result["processed"] += 1
        return result

    @staticmethod
    def _parse_external_date(value):
        if not value: return None
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if parsed.tzinfo is None: parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except (TypeError, ValueError):
            return None

    def process_marketplace_return(self, order_id: str, listing_id: str, variation_id: str,
                                   sold_units: int, reference: str) -> bool:
        """Reintegro explícito para una devolución confirmada; reference debe ser única."""
        if sold_units <= 0 or not reference.strip(): raise StockError('Cantidad positiva y referencia de devolución requeridas.')
        with self.transaction() as con:
            assoc = self._association_for_item(con, listing_id.upper(), str(variation_id or ""))
            if not assoc: raise StockError("La publicación no está asociada.")
            key = f"ML:RETURN:{reference}"
            if con.execute("SELECT 1 FROM stock_movements WHERE external_key=?", (key,)).fetchone(): return False
            self._apply_movement(con, assoc["product_id"], assoc["units_consumed"] * sold_units,
                "Devolución MercadoLibre", f"Orden {order_id} · Devolución {reference}", "MERCADOLIBRE", key)
            return True

    def backup(self, destination: str | Path):
        source = self.connect()
        target = sqlite3.connect(str(destination))
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()
