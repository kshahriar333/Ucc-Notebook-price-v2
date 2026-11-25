import unittest
from datetime import datetime
import monitor

SKU = "UN.35MSI.02B"

def page(price=77500, sku=SKU):
    return f"""<html><head><meta property="product:price:amount" content="{price}"></head>
    <body><h1>Acer Aspire 15 AS15-42-R5</h1><p>Part No: {sku}</p></body></html>"""

class FakeResponse:
    def __init__(self, text, status=200):
        self.text, self.status_code = text, status

class FakeSession:
    def __init__(self, pages):
        self.pages = pages
    def get(self, url, headers=None, timeout=None):
        if url not in self.pages:
            return FakeResponse("Just a moment...", 403)
        return FakeResponse(self.pages[url])

class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.urls = {"UCC":"ucc", "Ryans":"ryans", "Star Tech":"star", "Techland":"tech"}
        self.product = [{"id":SKU, "name":"Acer Aspire 15", "sku":SKU, "urls":self.urls}]
        self.now = datetime(2026,11,25,14,0)
        self.pages = {url:page() for url in self.urls.values()}

    def test_sku_and_price_parse(self):
        r = monitor.check_site("Ryans","ryans",monitor.norm(SKU),FakeSession(self.pages))
        self.assertEqual(r["price"],77500)
        self.assertEqual(r["sku_status"],"VERIFIED")

    def test_wrong_sku_never_compares_price(self):
        pages=dict(self.pages); pages["star"]=page(57900,"UN.35MSI.00M")
        r=monitor.check_site("Star Tech","star",monitor.norm(SKU),FakeSession(pages))
        self.assertEqual(r["status"],"SKU_MISMATCH")
        self.assertIsNone(r["price"])

    def test_unverified_sku_never_compares_price(self):
        pages=dict(self.pages); pages["star"]="<html><h1>Acer Aspire 15</h1><p>Price ৳77,500</p></html>"
        r=monitor.check_site("Star Tech","star",monitor.norm(SKU),FakeSession(pages))
        self.assertEqual(r["status"],"SKU_UNVERIFIED")
        self.assertIsNone(r["price"])

    def test_price_drop_alert(self):
        first,_=monitor.run(self.product,{},FakeSession(self.pages),self.now,sleep=0)
        self.assertTrue(first and "Monitoring started" in first[0])
        changed=dict(self.pages); changed["star"]=page(74500)
        alerts,_=monitor.run(self.product,monitor.run(self.product,{},FakeSession(self.pages),self.now,sleep=0)[1],FakeSession(changed),self.now,sleep=0)
        self.assertTrue(any("Star Tech price DOWN" in msg for msg in alerts))

    def test_missing_competitor_is_explicit(self):
        p=[{"id":SKU,"name":"Acer Aspire 15","sku":SKU,
            "urls":{"UCC":"ucc","Ryans":"ryans","Star Tech":"","Techland":"tech"}}]
        msgs,_=monitor.run(p,{},FakeSession(self.pages),self.now,sleep=0)
        self.assertTrue(any("same-SKU product page not found" in msg for msg in msgs))

if __name__=="__main__":
    unittest.main()
