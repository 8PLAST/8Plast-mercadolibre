import csv
import io
import re
import unittest
from production_recommended import production_row, build_recommended_plan, spreadsheet_safe
from stock_objectives import recommend
from test_stock_objectives import IntegrationTests
from readonly_portal.app import create_app


class ProductionCalculationTests(unittest.TestCase):
    def product(self,stock=100,pid=1):
        return dict(id=pid,name=f'Bolsa {pid}',sku=f'CON-{pid}',kind='BAG',
                    category='Consorcio',color_material='Negra',stock=stock,target_stock=40,storage_pack=50)

    def row(self,stock=100,reserved=0,receipts=()):
        p=self.product(stock)
        return production_row(p,recommend(p,[10]*90,[True]*90,0),reserved,receipts)

    def test_projection_preserves_sales_during_lead_time(self):
        r=self.row()
        self.assertEqual(r['projected_stock'],50)
        self.assertEqual(r['suggested'],100)
        self.assertEqual(r['packs'],2)
        self.assertEqual(r['monthly_average'],300)

    def test_pending_and_reserves_count_once(self):
        receipt=dict(id='batch1',quantity=40,days=4)
        r=self.row(reserved=20,receipts=[receipt,receipt])
        self.assertEqual(r['pending_due'],40)
        self.assertEqual(r['projected_stock'],30)
        self.assertEqual(r['suggested'],80)
        with self.assertRaises(ValueError):
            self.row(receipts=[receipt,dict(id='batch1',quantity=50,days=4)])

    def test_pending_after_receipt_is_not_subtracted(self):
        r=self.row(receipts=[dict(id='late',quantity=1000,days=6)])
        self.assertEqual(r['suggested'],100)
        self.assertEqual(r['pending_later'],1000)

    def test_pending_cannot_hide_stockout_before_its_arrival(self):
        r=self.row(stock=20,receipts=[dict(id='batch',quantity=1000,days=5)])
        self.assertEqual(r['priority'],'CRÍTICO')
        self.assertEqual(r['days_to_stockout'],2)
        self.assertEqual(r['suggested'],0)

    def test_pending_before_stockout_prevents_false_critical(self):
        r=self.row(stock=30,receipts=[dict(id='batch',quantity=1000,days=2)])
        self.assertEqual(r['priority'],'SIN NECESIDAD')
        self.assertGreater(r['days_to_stockout'],5)

    def test_no_negative_quantity_and_unknowns_are_visible(self):
        self.assertEqual(self.row(stock=1000)['suggested'],0)
        p=self.product()
        r=production_row(p,recommend(p,[10]*90,[None]*90,observed_only=True))
        self.assertTrue(r['provisional'])
        self.assertIsNone(r['reserved'])
        self.assertFalse(r['pending_known'])
        self.assertEqual(r['suggested'],100)
        self.assertEqual(self.row(stock=-10)['priority'],'REVISAR DATOS')
        self.assertIsNone(self.row(stock=-10)['suggested'])

    def test_sort_and_pack_rounding(self):
        products=[self.product(1000,1),self.product(10,2),self.product(49,3)]
        obj={p['id']:recommend(p,[10]*90,[True]*90,0) for p in products}
        rows=build_recommended_plan(products,obj)
        self.assertEqual([r['id'] for r in rows],[2,3,1])
        self.assertEqual(rows[0]['suggested'],190)
        self.assertEqual(rows[0]['packs'],4)
        self.assertEqual(spreadsheet_safe('=HYPERLINK("x")'),"'=HYPERLINK(\"x\")")


class ProductionPortalTests(unittest.TestCase):
    setUp=IntegrationTests.setUp
    tearDown=IntegrationTests.tearDown
    add_order=IntegrationTests.add_order

    def prepare(self):
        with self.db.transaction() as c:
            self.add_order(c,'1','red',3)
            self.add_order(c,'2','green',2)
        return create_app(dict(TESTING=True,DATABASE_PATH=str(self.path))).test_client()

    def test_filters_export_and_no_inventory_mutations(self):
        client=self.prepare(); before=self.path.read_bytes()
        response=client.get('/produccion-recomendada')
        self.assertEqual(response.status_code,200)
        self.assertIn('PRODUCCIÓN RECOMENDADA',response.get_data(as_text=True))
        for query in ('q=Roja','color=Roja','family=CONSORCIO','priority=CR%C3%8DTICO'):
            self.assertEqual(client.get('/produccion-recomendada?'+query).status_code,200)
        response=client.get('/produccion-recomendada?color=Roja&export=csv')
        rows=list(csv.reader(io.StringIO(response.data.decode('utf-8-sig')),delimiter=';'))
        self.assertEqual(len(rows),2)
        self.assertIn('Roja',rows[1])
        self.assertNotIn('Verde',rows[1])
        self.assertIn('attachment',response.headers['Content-Disposition'])
        self.assertEqual(before,self.path.read_bytes())
        self.assertEqual(client.post('/produccion-recomendada').status_code,405)

    def test_manual_production_recalculates_without_counting_again(self):
        client=self.prepare()
        def suggested():
            response=client.get('/produccion-recomendada?q=Roja&export=csv')
            row=list(csv.reader(io.StringIO(response.data.decode('utf-8-sig')),delimiter=';'))[1]
            return int(row[13])
        before=suggested()
        self.db.move_stock(self.pids[0],10,'Entrada de producción','Prueba temporal')
        self.assertEqual(suggested(),max(0,before-10))

    def test_production_invalidates_old_pending_simulation(self):
        client=self.prepare()
        page=client.get('/produccion-recomendada').get_data(as_text=True)
        snapshot=re.search(r'name="scenario_snapshot" value="([a-f0-9]+)"',page).group(1)
        query=f'scenario_product={self.pids[0]}&reserved=0&pending=50&pending_day=3&scenario_snapshot={snapshot}'
        self.assertIn('Simulación activa',client.get('/produccion-recomendada?'+query).get_data(as_text=True))
        self.db.move_stock(self.pids[0],50,'Entrada de producción','Ingreso temporal del pendiente')
        page=client.get('/produccion-recomendada?'+query).get_data(as_text=True)
        self.assertIn('se descartó porque cambió el inventario',page)
        self.assertNotIn('Simulación activa',page)
        response=client.get('/produccion-recomendada?'+query+'&q=Roja&export=csv')
        row=list(csv.reader(io.StringIO(response.data.decode('utf-8-sig')),delimiter=';'))[1]
        self.assertEqual(row[11],'')  # no se vuelve a descontar lo ya ingresado

    def test_scenario_is_transient_and_export_matches(self):
        client=self.prepare(); before=self.path.read_bytes()
        query=f'scenario_product={self.pids[0]}&reserved=20&pending=30&pending_day=3'
        response=client.get('/produccion-recomendada?'+query)
        self.assertEqual(response.status_code,200)
        self.assertIn('Simulación activa',response.get_data(as_text=True))
        response=client.get('/produccion-recomendada?'+query+'&q=Roja&export=csv')
        row=list(csv.reader(io.StringIO(response.data.decode('utf-8-sig')),delimiter=';'))[1]
        self.assertEqual(row[7],'20')
        self.assertEqual(row[11],'30')
        self.assertEqual(before,self.path.read_bytes())
        self.assertEqual(client.get(f'/produccion-recomendada?scenario_product={self.pids[0]}&reserved=-1').status_code,400)
        self.assertEqual(client.get(f'/produccion-recomendada?scenario_product={self.pids[0]}&pending=NaN').status_code,400)
        self.assertEqual(client.get(f'/produccion-recomendada?scenario_product={self.pids[0]}&pending_day=366').status_code,400)
        self.assertEqual(client.get('/produccion-recomendada?scenario_product=9999').status_code,400)


if __name__=='__main__': unittest.main()
