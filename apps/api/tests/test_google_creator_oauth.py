import os
import unittest
from urllib.parse import urlparse, parse_qs

from cryptography.fernet import Fernet

from app.services.google_creator_oauth import (
    encrypt_token, decrypt_token, new_authorization, parse_money,
)


class GoogleCreatorOAuthTests(unittest.TestCase):
    def setUp(self):
        self.old_client = os.environ.get("GOOGLE_OAUTH_CLIENT_ID")
        self.old_key = os.environ.get("AI_COMIC_TOKEN_ENCRYPTION_KEY")
        os.environ["GOOGLE_OAUTH_CLIENT_ID"] = "test-desktop-client.apps.googleusercontent.com"
        os.environ["AI_COMIC_TOKEN_ENCRYPTION_KEY"] = Fernet.generate_key().decode("ascii")

    def tearDown(self):
        if self.old_client is None:
            os.environ.pop("GOOGLE_OAUTH_CLIENT_ID", None)
        else:
            os.environ["GOOGLE_OAUTH_CLIENT_ID"] = self.old_client
        if self.old_key is None:
            os.environ.pop("AI_COMIC_TOKEN_ENCRYPTION_KEY", None)
        else:
            os.environ["AI_COMIC_TOKEN_ENCRYPTION_KEY"] = self.old_key

    def test_token_is_encrypted_and_round_trips(self):
        token = "refresh-token-test-value"
        encrypted = encrypt_token(token)
        self.assertNotEqual(encrypted, token)
        self.assertNotIn(token, encrypted)
        self.assertEqual(decrypt_token(encrypted), token)

    def test_authorization_uses_state_pkce_and_readonly_scopes(self):
        redirect_uri = "http://127.0.0.1:49152"
        url, state, verifier = new_authorization(redirect_uri)
        query = parse_qs(urlparse(url).query)
        self.assertEqual(query["state"][0], state)
        self.assertEqual(query["redirect_uri"][0], redirect_uri)
        self.assertEqual(urlparse(query["redirect_uri"][0]).path, "")
        self.assertEqual(query["code_challenge_method"][0], "S256")
        self.assertNotEqual(query["code_challenge"][0], verifier)
        scopes = query["scope"][0].split()
        self.assertIn("https://www.googleapis.com/auth/yt-analytics-monetary.readonly", scopes)
        self.assertIn("https://www.googleapis.com/auth/adsense.readonly", scopes)
        self.assertNotIn("https://www.googleapis.com/auth/adsense", scopes)

    def test_parse_adsense_money_with_explicit_currency(self):
        self.assertEqual(parse_money("$1,234.56 USD"), (1234.56, "USD"))
        self.assertEqual(parse_money("€82.10 EUR"), (82.1, "EUR"))
        self.assertEqual(parse_money("not-a-payment"), (None, None))


if __name__ == "__main__":
    unittest.main()
