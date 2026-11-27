import os
import unittest
from urllib.parse import urlparse, parse_qs

from cryptography.fernet import Fernet

from app.services.google_creator_oauth import (
    encrypt_token, decrypt_token, new_authorization, parse_money,
    start_loopback_listener, API_CALLBACK_URI,
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

    def test_random_loopback_listener_forwards_once_to_local_callback(self):
        from urllib.error import HTTPError
        from urllib.request import build_opener, HTTPRedirectHandler, Request, urlopen

        class NoRedirect(HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None

        redirect_uri = start_loopback_listener()
        parsed = urlparse(redirect_uri)
        self.assertEqual(parsed.hostname, "127.0.0.1")
        self.assertEqual(parsed.path, "")
        self.assertGreater(parsed.port, 0)
        opener = build_opener(NoRedirect)
        try:
            opener.open(Request(redirect_uri + "?code=sample&state=sample-state"), timeout=5)
        except HTTPError as exc:
            self.assertEqual(exc.code, 302)
            self.assertEqual(exc.headers.get("Location"), API_CALLBACK_URI + "?code=sample&state=sample-state")
        else:
            self.fail("Loopback listener should forward using HTTP 302")

    def test_parse_adsense_money_with_explicit_currency(self):
        self.assertEqual(parse_money("$1,234.56 USD"), (1234.56, "USD"))
        self.assertEqual(parse_money("€82.10 EUR"), (82.1, "EUR"))
        self.assertEqual(parse_money("£87.65"), (87.65, "GBP"))
        self.assertEqual(parse_money("R$ 120,50"), (120.5, "BRL"))
        self.assertEqual(parse_money("$1,234.57"), (1234.57, None))
        self.assertEqual(parse_money("¥1,235"), (1235.0, None))
        self.assertEqual(parse_money("¥1,235", "JPY"), (1235.0, "JPY"))
        self.assertEqual(parse_money("not-a-payment"), (None, None))


if __name__ == "__main__":
    unittest.main()
