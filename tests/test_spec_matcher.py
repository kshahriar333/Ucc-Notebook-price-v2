import unittest
from spec_matcher import extract_identity, compare_laptops

class Tests(unittest.TestCase):
 def test_exact_sku(self):
  a=extract_identity("Acer Aspire 5","Part No: UN.35MSI.02B")
  b=extract_identity("Acer Aspire 5","SKU: UN.35MSI.02B")
  self.assertEqual(compare_laptops(a,b)["method"],"EXACT_SKU")
 def test_spec_fallback(self):
  a=extract_identity("Acer Aspire 5 A515-58M Core i5-1335U 16GB DDR5 512GB NVMe SSD","")
  b=extract_identity("Acer Aspire 5 A515-58M Intel Core i5-1335U 16GB DDR5 512GB SSD","")
  self.assertTrue(compare_laptops(a,b)["matched"])
  self.assertEqual(compare_laptops(a,b)["method"],"SPEC_MATCH")
 def test_cpu_conflict(self):
  a=extract_identity("Acer Aspire 5 A515-58M Core i5-1335U 16GB DDR5 512GB SSD","")
  b=extract_identity("Acer Aspire 5 A515-58M Core i7-1355U 16GB DDR5 512GB SSD","")
  self.assertFalse(compare_laptops(a,b)["matched"])
 def test_ram_conflict(self):
  a=extract_identity("Acer Aspire 5 A515-58M Core i5-1335U 16GB DDR5 512GB SSD","")
  b=extract_identity("Acer Aspire 5 A515-58M Core i5-1335U 8GB DDR5 512GB SSD","")
  self.assertFalse(compare_laptops(a,b)["matched"])
 def test_storage_conflict(self):
  a=extract_identity("Acer Aspire 5 A515-58M Core i5-1335U 16GB DDR5 512GB SSD","")
  b=extract_identity("Acer Aspire 5 A515-58M Core i5-1335U 16GB DDR5 1TB SSD","")
  self.assertFalse(compare_laptops(a,b)["matched"])
 def test_intel_core_5_model(self):
  a=extract_identity("Acer Aspire 7 A715-79G Intel Core 5 210H 16GB DDR4 512GB SSD","")
  b=extract_identity("Acer Aspire 7 A715-79G Core 5 210H 16GB DDR4 512GB SSD","")
  self.assertTrue(compare_laptops(a,b)["matched"])
 def test_insufficient_data(self):
  a=extract_identity("Acer Aspire 5","")
  self.assertFalse(compare_laptops(a,a)["matched"])
 def test_different_sku_blocks_fallback(self):
  a=extract_identity("Acer Aspire 5 A515-58M Core i5-1335U 16GB DDR5 512GB SSD","Part No: UN.35MSI.02B")
  b=extract_identity("Acer Aspire 5 A515-58M Core i5-1335U 16GB DDR5 512GB SSD","Part No: UN.35MSI.03C")
  self.assertFalse(compare_laptops(a,b)["matched"])
if __name__=="__main__": unittest.main()
