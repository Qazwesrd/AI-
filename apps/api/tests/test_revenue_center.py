import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models.revenue import RevenueEntry, WithdrawalRecord
from app.routers.revenue import (
    RevenueInput, WithdrawalInput, WithdrawalStatusInput,
    create_revenue, create_withdrawal, update_withdrawal, summary, list_platform_capabilities,
)


class RevenueCenterTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_revenue_net_and_currency_summary(self):
        create_revenue(RevenueInput(platform="YouTube", gross_amount=100, currency="USD", fee_amount=5, tax_amount=10, status="settled"), self.db)
        result = summary(self.db)
        self.assertEqual(result["currency_totals"]["USD"]["net"], 85)
        self.assertEqual(result["currency_totals"]["USD"]["settled_balance_estimate"], 85)
        self.assertIn("不是平台实时余额", result["disclaimer"])

    def test_withdrawal_is_manual_record_and_arrival_requires_date(self):
        item = create_withdrawal(WithdrawalInput(platform="YouTube", amount=50, currency="USD", fee_amount=2), self.db)["item"]
        self.assertEqual(item["status"], "planned")
        self.assertIn("不会发起资金操作", create_withdrawal(item_input := WithdrawalInput(platform="Douyin", amount=1), self.db)["message"])
        with self.assertRaises(Exception):
            update_withdrawal(item["id"], WithdrawalStatusInput(status="paid"), self.db)
        updated = update_withdrawal(item["id"], WithdrawalStatusInput(status="paid", arrived_at="2026-11-27"), self.db)
        self.assertEqual(updated["item"]["status"], "paid")

    def test_platform_capabilities_never_advertise_unimplemented_direct_payout(self):
        result = list_platform_capabilities()
        self.assertGreaterEqual(len(result["items"]), 7)
        self.assertTrue(all(item["payout_api"] is False for item in result["items"]))
        self.assertIn("不会显示软件内直接提现按钮", result["global_notice"])

    def test_reject_invalid_currency_and_overlarge_fees(self):
        with self.assertRaises(Exception):
            RevenueInput(platform="test", gross_amount=10, currency="XYZ")
        with self.assertRaises(Exception):
            RevenueInput(platform="test", gross_amount=10, fee_amount=8, tax_amount=3)


if __name__ == "__main__":
    unittest.main()
