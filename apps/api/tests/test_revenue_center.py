import io
import os
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from cryptography.fernet import Fernet
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models.revenue import RevenueEntry, WithdrawalRecord, CreatorOAuthConnection
from app.routers.revenue import (
    RevenueInput, WithdrawalInput, WithdrawalStatusInput,
    create_revenue, create_withdrawal, update_withdrawal, summary, list_platform_capabilities,
    sync_google_creator_revenue, import_platform_report,
)
from app.services.google_creator_oauth import encrypt_token


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

    def test_payable_settled_and_paid_revenue_are_separated(self):
        create_revenue(RevenueInput(platform="NovelPlatform", gross_amount=10, currency="CNY", status="estimated"), self.db)
        create_revenue(RevenueInput(platform="NovelPlatform", gross_amount=20, currency="CNY", status="settled"), self.db)
        create_revenue(RevenueInput(platform="NovelPlatform", gross_amount=30, currency="CNY", status="payable"), self.db)
        create_revenue(RevenueInput(platform="NovelPlatform", gross_amount=40, currency="CNY", status="paid"), self.db)
        result = summary(self.db)["currency_totals"]["CNY"]
        self.assertEqual(result["estimated_net"], 10)
        self.assertEqual(result["settled_net"], 20)
        self.assertEqual(result["payable_net"], 30)
        self.assertEqual(result["paid_net"], 40)

    def test_withdrawal_is_manual_record_and_arrival_requires_date(self):
        created = create_withdrawal(WithdrawalInput(platform="YouTube", amount=50, currency="USD", fee_amount=2), self.db)
        item = created["item"]
        self.assertEqual(item["status"], "planned")
        self.assertIn("不会发起资金操作", created["message"])
        with self.assertRaises(Exception):
            update_withdrawal(item["id"], WithdrawalStatusInput(status="paid"), self.db)
        updated = update_withdrawal(item["id"], WithdrawalStatusInput(status="paid", arrived_at="2026-11-27"), self.db)
        self.assertEqual(updated["item"]["status"], "paid")

    def test_google_oauth_sync_records_estimated_and_payable_amounts_idempotently(self):
        previous_key = os.environ.get("AI_COMIC_TOKEN_ENCRYPTION_KEY")
        os.environ["AI_COMIC_TOKEN_ENCRYPTION_KEY"] = Fernet.generate_key().decode("ascii")
        try:
            connection = CreatorOAuthConnection(
                provider="google_youtube_adsense", account_label="writer@example.com",
                external_account_id="writer@example.com",
                access_token_encrypted=encrypt_token("access-token"),
                refresh_token_encrypted=encrypt_token("refresh-token"),
                token_expires_at=datetime.utcnow() + timedelta(hours=1),
                granted_scopes="https://www.googleapis.com/auth/yt-analytics-monetary.readonly https://www.googleapis.com/auth/adsense.readonly",
                status="connected",
            )
            self.db.add(connection)
            self.db.commit()
            self.db.refresh(connection)

            def fake_google_get(url, token):
                if "youtubeanalytics.googleapis.com" in url:
                    return {"rows":[["2026-11-26", 3.25]]}
                if url.endswith("/v2/accounts?pageSize=100"):
                    return {"accounts":[{"name":"accounts/pub-123"}]}
                if "/payments" in url:
                    return {"payments":[{"name":"accounts/pub-123/payments/unpaid","amount":"$12.50 USD","date":{}}]}
                raise AssertionError(url)

            with patch("app.routers.revenue.google_get_json", side_effect=fake_google_get):
                first = sync_google_creator_revenue(connection.id, self.db)
                second = sync_google_creator_revenue(connection.id, self.db)
            self.assertEqual(first["analytics_days"], 1)
            self.assertEqual(first["payment_records"], 1)
            self.assertEqual(first["inserted"], 2)
            self.assertEqual(second["inserted"], 0)
            rows = self.db.query(RevenueEntry).order_by(RevenueEntry.platform).all()
            self.assertEqual(len(rows), 2)
            self.assertEqual({row.status for row in rows}, {"estimated","payable"})
        finally:
            if previous_key is None:
                os.environ.pop("AI_COMIC_TOKEN_ENCRYPTION_KEY", None)
            else:
                os.environ["AI_COMIC_TOKEN_ENCRYPTION_KEY"] = previous_key

    def test_platform_capabilities_never_advertise_unimplemented_direct_payout(self):
        result = list_platform_capabilities()
        self.assertGreaterEqual(len(result["items"]), 7)
        self.assertTrue(all(item["payout_api"] is False for item in result["items"]))
        self.assertIn("不会显示软件内直接提现按钮", result["global_notice"])

    def test_kdp_and_google_play_report_imports_are_idempotent_and_keep_revenue_stages(self):
        google=b"Earnings Date\tTitle\tEarnings Currency\tEarnings Amount\n2026-10-31\tNovel A\tUSD\t12.50\n2026-10-31\tNovel A refund\tUSD\t-1.25\n"
        first=import_platform_report("google_play_books","earnings.tsv",google,self.db)
        second=import_platform_report("google_play_books","earnings.tsv",google,self.db)
        self.assertEqual(first["imported"],2)
        self.assertEqual(second["imported"],0)
        self.assertEqual(second["updated"],2)
        settled=self.db.query(RevenueEntry).filter_by(platform="Google Play Books",status="settled").all()
        self.assertEqual(len(settled),2)
        self.assertAlmostEqual(sum(row.gross_amount for row in settled),11.25)
        self.assertTrue(all("不是银行到账凭证" in row.notes for row in settled))

        kdp=b"Month,Title,Currency,Estimated royalties\n2026-11,Novel B,USD,$3.45\n"
        imported=import_platform_report("kdp","kdp.csv",kdp,self.db)
        self.assertEqual(imported["imported"],1)
        kdp_row=self.db.query(RevenueEntry).filter_by(platform="Amazon KDP").one()
        self.assertEqual(kdp_row.status,"estimated")
        self.assertAlmostEqual(kdp_row.gross_amount,3.45)

        payment=b"Payment Number,Payment Status,Payment Date,Marketplace,Currency,Payment Amount\\n123,Paid,2026-10-15,Amazon.com,USD,42.50\\n124,Pending,2026-10-15,Amazon.com,USD,12.00\\n"
        payment_result=import_platform_report("kdp","payments.csv",payment,self.db)
        self.assertEqual(payment_result["imported"],2)
        payment_rows=self.db.query(RevenueEntry).filter(RevenueEntry.platform=="Amazon KDP",RevenueEntry.source_ref.like("report-import:kdp:%")).all()
        stages={row.gross_amount:row.status for row in payment_rows}
        self.assertEqual(stages[42.5],"paid")
        self.assertEqual(stages[12.0],"payable")

    def test_kdp_and_google_play_books_are_official_report_only(self):
        result=list_platform_capabilities()
        items={item["platform"]:item for item in result["items"]}
        self.assertIn("Amazon KDP",items)
        self.assertIn("Google Play Books",items)
        self.assertFalse(items["Amazon KDP"]["payout_api"])
        self.assertFalse(items["Google Play Books"]["payout_api"])
        self.assertIn("kdpreports.amazon.com",items["Amazon KDP"]["official_portal"])
        self.assertIn("Earnings Reports",items["Google Play Books"]["revenue_api"])

    def test_reject_invalid_currency_and_overlarge_fees(self):
        with self.assertRaises(Exception):
            RevenueInput(platform="test", gross_amount=10, currency="XYZ")
        with self.assertRaises(Exception):
            RevenueInput(platform="test", gross_amount=10, fee_amount=8, tax_amount=3)


if __name__ == "__main__":
    unittest.main()
