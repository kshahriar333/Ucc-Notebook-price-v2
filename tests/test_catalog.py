import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import catalog


class AcerCatalogueTests(unittest.TestCase):
    def test_extracts_acer_title_and_part_number(self):
        html = """
        <html><head><title>Acer Aspire 15</title></head>
        <body><h1>Acer Aspire 15 AS15-42</h1>
        <div>Part No: UN.35MSI.02B</div></body></html>
        """
        title, sku, is_acer = catalog.extract_identity(html, "https://ucc.com.bd/acer-aspire")
        self.assertIn("Acer", title)
        self.assertEqual(sku, "UN.35MSI.02B")
        self.assertTrue(is_acer)

    def test_excludes_non_acer_products(self):
        html = """
        <html><head><title>MSI Z590 Motherboard</title></head>
        <body><h1>MSI Z590</h1><div>Part No: 7D11-001R</div></body></html>
        """
        title, sku, is_acer = catalog.extract_identity(html, "https://ucc.com.bd/msi-z590")
        self.assertFalse(is_acer)

    def test_category_listing_keeps_only_same_host_product_links(self):
        html = """
        <div class="product-thumb">
          <a href="/acer-aspire-15">Acer Aspire 15</a>
          <a href="https://example.com/not-ours">External</a>
        </div>
        <a href="/category/other">Category</a>
        """
        links = catalog.listing_links(html, "https://www.ryans.com/category/all-laptop-acer", "ryans.com")
        self.assertEqual(links, {"https://www.ryans.com/acer-aspire-15"})

    def test_page_url_preserves_category_query(self):
        url = catalog.page_url("https://example.com/category?sort=price", 2)
        self.assertIn("sort=price", url)
        self.assertIn("page=2", url)


if __name__ == "__main__":
    unittest.main()
