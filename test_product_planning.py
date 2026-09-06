import unittest
from product_planning import product_family,production_recommendation

class ProductPlanningTests(unittest.TestCase):
    def test_structured_families(self):
        self.assertEqual(product_family({"kind":"ROLL","category":"Bolsa tubo","sku":"ROL-X"}),"ROLLOS")
        self.assertEqual(product_family({"kind":"BAG","category":"Leña","sku":"LENA-X"}),"LEÑA_CARBON")
        self.assertEqual(product_family({"kind":"BAG","category":"Consorcio","sku":"LENA-X"}),"SIN_CLASIFICAR")

    def test_critical_coverage_dominates_and_spike_is_capped(self):
        p={"kind":"BAG","category":"Consorcio","sku":"CON-X","stock":100,"minimum_stock":50,"target_stock":500}
        m={"data_complete":True,"adjusted_velocity":50,"velocity_7":100,"velocity_90":10,"velocity_180":10,"coverage":2,
           "trend":"FUERTE CRECIMIENTO","classification":"BEST SELLER","sales_180":1800}
        r=production_recommendation(p,m)
        self.assertEqual(r["production_priority"],"FABRICAR AHORA")
        self.assertTrue(r["spike_limited"])
        self.assertLessEqual(r["suggested"],1575)

    def test_incomplete_data_never_looks_safe(self):
        p={"kind":"BAG","category":"Cristal","sku":"CRI-X","stock":0,"minimum_stock":10,"target_stock":100}
        r=production_recommendation(p,{"data_complete":False,"adjusted_velocity":10})
        self.assertEqual((r["production_priority"],r["suggested"],r["confidence"]),("DATOS INCOMPLETOS",0,"BAJA"))

if __name__=="__main__":unittest.main()
