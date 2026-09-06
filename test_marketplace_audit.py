import unittest
from datetime import datetime,timezone

from ml_integration import MercadoLibreClient


class AuditDB:
    def __init__(self):self.saved=[];self.states=[]
    def update_marketplace_audit(self,**values):self.states.append(values)
    def store_marketplace_audit_orders(self,orders):self.saved.extend(orders);return len(orders)


class AuditClient(MercadoLibreClient):
    def _search_page(self,date_field,start,end,offset,limit=50):
        total=55
        results=[{"id":str(i),"date_created":"2026-08-01T00:00:00+00:00"} for i in range(offset,min(offset+limit,total))]
        return {"results":results,"paging":{"total":total}}


class MarketplaceAuditTests(unittest.TestCase):
    def test_historical_audit_paginates_and_never_processes_stock(self):
        db=AuditDB(); client=AuditClient(db)
        result=client.audit_historical_orders(days=180,cutoff=datetime(2026,8,29,tzinfo=timezone.utc),window_days=180)
        self.assertEqual(result["pages"],2)
        self.assertEqual(result["orders"],55)
        self.assertEqual(len(db.saved),55)
        self.assertEqual(db.states[-1]["status"],"COMPLETE")


if __name__=="__main__":unittest.main()
