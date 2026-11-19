import unittest

from app.routers.system import _health_status


class SystemDiagnosticsTests(unittest.TestCase):
    def test_all_checks_ok(self):
        self.assertEqual(_health_status([
            {"status": "ok"}, {"status": "ok"}
        ]), "ok")

    def test_warning_is_reported_without_error(self):
        self.assertEqual(_health_status([
            {"status": "ok"}, {"status": "warning"}
        ]), "warning")

    def test_error_has_highest_priority(self):
        self.assertEqual(_health_status([
            {"status": "warning"}, {"status": "error"}
        ]), "error")


if __name__ == "__main__":
    unittest.main()
