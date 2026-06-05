import importlib
import importlib.util
import os
import tempfile
import unittest


FLASK_AVAILABLE = importlib.util.find_spec("flask") is not None


@unittest.skipUnless(FLASK_AVAILABLE, "Flask is not installed in this test environment")
class PanelSecurityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db_file = tempfile.NamedTemporaryFile(suffix=".db", dir=".", delete=False)
        self.db_file.close()
        self.previous_env = {
            key: os.environ.get(key)
            for key in (
                "BARRIER_DB_PATH",
                "BARRIER_PANEL_HOST",
                "BARRIER_PANEL_PASSWORD",
                "BARRIER_FLASK_SECRET_KEY",
            )
        }
        os.environ["BARRIER_DB_PATH"] = self.db_file.name
        os.environ["BARRIER_PANEL_HOST"] = "0.0.0.0"
        os.environ["BARRIER_PANEL_PASSWORD"] = "secret"
        os.environ["BARRIER_FLASK_SECRET_KEY"] = "test-secret-key"

        import panel

        self.panel = importlib.reload(panel)
        self.client = self.panel.app.test_client()

    def tearDown(self) -> None:
        for key, value in self.previous_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        try:
            os.remove(self.db_file.name)
        except OSError:
            pass

    def csrf_token_from_session(self) -> str:
        with self.client.session_transaction() as sess:
            token = sess.get("csrf_token")
        assert isinstance(token, str)
        return token

    def test_post_requires_csrf_token(self) -> None:
        response = self.client.post("/manual-open")
        self.assertEqual(response.status_code, 400)

    def test_missing_public_password_locks_panel(self) -> None:
        os.environ["BARRIER_PANEL_PASSWORD"] = ""
        self.panel = importlib.reload(self.panel)
        self.client = self.panel.app.test_client()

        response = self.client.get("/")

        self.assertEqual(response.status_code, 503)

    def test_login_locks_after_repeated_failures(self) -> None:
        self.client.get("/login")
        token = self.csrf_token_from_session()

        response = None
        for _ in range(self.panel.LOGIN_ATTEMPT_LIMIT):
            response = self.client.post(
                "/login",
                data={"password": "wrong", "csrf_token": token},
            )

        assert response is not None
        self.assertEqual(response.status_code, 429)


if __name__ == "__main__":
    unittest.main()
