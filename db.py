from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path


DB_PATH = Path(__file__).with_name("8plast_stock.db")
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
                    stock INTEGER NOT NULL DEFAULT 0 CHECK(stock >= 0),
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
                    stock_before INTEGER NOT NULL CHECK(stock_before >= 0),
                    stock_after INTEGER NOT NULL CHECK(stock_after >= 0),
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
                UNIQUE(marketplace, listing_id, variation_id)
            )""")
            con.execute("""INSERT INTO marketplace_listings
                (id,marketplace,listing_id,variation_id,product_id,units_consumed,presentation_type,listing_name,
                 active,sync_mode,last_published_quantity,last_stock_sent,last_synced_at)
                SELECT id,marketplace,listing_id,'',product_id,units_consumed,'','',active,
                       'SOLO_DESCONTAR_VENTAS',NULL,last_stock_sent,last_synced_at
                FROM marketplace_listings_legacy""")
            con.execute("DROP TABLE marketplace_listings_legacy")

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
        if after < 0:
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

    def adjust_stock(self, product_id: int, target: int, reason: str) -> int:
        if target < 0:
            raise StockError("El stock no puede ser negativo.")
        with self.transaction() as con:
            row = con.execute("SELECT stock FROM products WHERE id=?", (product_id,)).fetchone()
            if not row:
                raise StockError("El producto no existe.")
            delta = target - row["stock"]
            if delta == 0:
                raise StockError("El stock ya tiene ese valor.")
            return self._apply_movement(con, product_id, delta, "Ajuste de inventario", reason)

    def products(self, kind: str | None = None, search: str = "", active: str = "Todos"):
        sql = "SELECT * FROM products WHERE 1=1"
        args: list = []
        if kind:
            sql += " AND kind=?"; args.append(kind)
        if search:
            sql += " AND (name LIKE ? OR sku LIKE ? OR color_material LIKE ? OR category LIKE ?)"
            term = f"%{search}%"; args.extend([term] * 4)
        if active == "Activos": sql += " AND active=1"
        elif active == "Inactivos": sql += " AND active=0"
        sql += " ORDER BY active DESC, name COLLATE NOCASE"
        with self.session() as con:
            return con.execute(sql, args).fetchall()

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

    def movements(self, search: str = "", movement_type: str = "Todos", limit: int = 500):
        sql = """SELECT m.*,p.name product_name,p.sku,p.kind FROM stock_movements m
                 JOIN products p ON p.id=m.product_id WHERE 1=1"""
        args: list = []
        if search:
            sql += " AND (p.name LIKE ? OR p.sku LIKE ? OR m.reason LIKE ?)"
            args.extend([f"%{search}%"] * 3)
        if movement_type != "Todos": sql += " AND m.movement_type=?"; args.append(movement_type)
        sql += " ORDER BY m.id DESC LIMIT ?"; args.append(limit)
        with self.session() as con:
            return con.execute(sql, args).fetchall()

    def dashboard(self):
        with self.session() as con:
            counts = con.execute("""SELECT COUNT(*) active,
                SUM(CASE WHEN active=1 AND stock=0 THEN 1 ELSE 0 END) empty,
                SUM(CASE WHEN active=1 AND stock>0 AND stock<=minimum_stock THEN 1 ELSE 0 END) low
                FROM products WHERE active=1""").fetchone()
            recent = con.execute("""SELECT m.*,p.name product_name FROM stock_movements m
                JOIN products p ON p.id=m.product_id ORDER BY m.id DESC LIMIT 10""").fetchall()
            alerts = con.execute("""SELECT * FROM products WHERE active=1 AND stock<=minimum_stock
                ORDER BY stock=0 DESC, (stock-minimum_stock), name LIMIT 15""").fetchall()
            return counts, recent, alerts

    @staticmethod
    def production_needed(product) -> int:
        return max(0, product["target_stock"] - product["stock"])

    def add_marketplace_listing(self, product_id: int, listing_id: str, units_consumed: int,
                                variation_id: str = "", presentation_type: str = "", listing_name: str = "",
                                sync_mode: str = "SOLO_DESCONTAR_VENTAS",
                                published_quantity: int | None = None) -> int:
        if sync_mode not in ("SOLO_DESCONTAR_VENTAS", "SINCRONIZAR_STOCK"):
            raise ValueError("Modo de sincronización inválido.")
        with self.session() as con:
            cur = con.execute("""INSERT INTO marketplace_listings
                (listing_id,variation_id,product_id,units_consumed,presentation_type,listing_name,sync_mode,last_published_quantity)
                VALUES (?,?,?,?,?,?,?,?)""", (listing_id.strip().upper(), variation_id.strip(), product_id,
                units_consumed, presentation_type, listing_name.strip(), sync_mode, published_quantity))
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

    def marketplace_listing_by_id(self, association_id: int):
        with self.session() as con:
            return con.execute("SELECT * FROM marketplace_listings WHERE id=?", (association_id,)).fetchone()

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
        result = {"processed": 0, "duplicates": 0, "unassociated": [], "cancelled": is_cancelled}
        for index, order_item in enumerate(order.get("order_items") or []):
            item = order_item.get("item") or {}
            listing_id = str(item.get("id") or "").upper()
            variation_id = str(item.get("variation_id") or "")
            quantity = int(order_item.get("quantity") or 0)
            with self.transaction() as con:
                assoc = self._association_for_item(con, listing_id, variation_id)
                if not assoc:
                    result["unassociated"].append(f"{listing_id}/{variation_id}" if variation_id else listing_id)
                    continue
                line_key = f"{order_id}:{listing_id}:{variation_id}:{index}"
                sale_key = f"ML:SALE:{line_key}"
                reversal_key = f"ML:CANCEL:{line_key}"
                if is_cancelled:
                    original = con.execute("SELECT * FROM stock_movements WHERE external_key=?", (sale_key,)).fetchone()
                    if not original:
                        result["duplicates"] += 1
                        continue
                    if con.execute("SELECT 1 FROM stock_movements WHERE external_key=?", (reversal_key,)).fetchone():
                        result["duplicates"] += 1
                        continue
                    self._apply_movement(con, assoc["product_id"], abs(original["quantity_delta"]),
                        "Cancelación MercadoLibre", f"Orden {order_id} · Publicación {listing_id}",
                        "MERCADOLIBRE", reversal_key)
                else:
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

    def process_marketplace_return(self, order_id: str, listing_id: str, variation_id: str,
                                   sold_units: int, reference: str) -> bool:
        """Reintegro explícito para una devolución confirmada; reference debe ser única."""
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
