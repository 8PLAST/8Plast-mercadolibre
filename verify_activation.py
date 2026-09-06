import json
import sqlite3
from pathlib import Path

db=Path(__file__).resolve().parent/"8plast_stock.db"
con=sqlite3.connect(f"file:{db}?mode=ro",uri=True)
out={
    "integrity":con.execute("PRAGMA integrity_check").fetchone()[0],
    "foreign_key_errors":len(con.execute("PRAGMA foreign_key_check").fetchall()),
    "config":con.execute("SELECT enabled,interval_seconds,activation_at FROM marketplace_processing_config WHERE id=1").fetchone(),
    "stock":con.execute("SELECT SUM(stock) FROM products").fetchone()[0],
    "movements":con.execute("SELECT COUNT(*) FROM stock_movements").fetchone()[0],
    "pending_cancellations":con.execute("SELECT COUNT(*) FROM marketplace_order_reviews WHERE status='PENDIENTE'").fetchone()[0],
    "sync_stock_modes":con.execute("SELECT COUNT(*) FROM marketplace_listings WHERE sync_mode='SINCRONIZAR_STOCK'").fetchone()[0],
    "sale_movements_after_activation":con.execute("""SELECT COUNT(*) FROM stock_movements
        WHERE external_key LIKE 'ML:SALE:%' AND created_at >=
        (SELECT activation_at FROM marketplace_processing_config WHERE id=1)""").fetchone()[0],
}
con.close()
print(json.dumps(out,ensure_ascii=False,indent=2))
