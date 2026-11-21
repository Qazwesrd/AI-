import unittest

from app.routers.image_model_catalog import list_image_model_catalog


class ImageModelCatalogTests(unittest.TestCase):
    def test_catalog_is_pinned_and_read_only(self):
        result = list_image_model_catalog()
        self.assertEqual(result["count"], 1)
        item = result["models"][0]
        self.assertEqual(item["revision"], "4fddeb7")
        self.assertEqual(item["filename"], "v1-5-pruned-emaonly-fp16.safetensors")
        self.assertRegex(item["sha256"], r"^[0-9a-f]{64}$")
        self.assertFalse(item["sha256_verified"])
        self.assertFalse(item["download_eligible"])
        self.assertFalse(item["install_eligible"])
        self.assertEqual(len(item["eligibility_blockers"]), 4)
        self.assertIsNone(item["size_bytes"])
        self.assertEqual(item["license_status"], "review_required")
        self.assertEqual(item["hardware_status"], "cpu_not_recommended")
        self.assertFalse(result["network_used"])
        self.assertFalse(result["download_started"])
        self.assertFalse(result["installation_started"])
        self.assertFalse(result["download_available"])
        self.assertFalse(result["install_available"])

    def test_catalog_returns_independent_objects(self):
        first = list_image_model_catalog()
        first["models"][0]["name"] = "tampered"
        second = list_image_model_catalog()
        self.assertNotEqual(second["models"][0]["name"], "tampered")


if __name__ == "__main__":
    unittest.main()
