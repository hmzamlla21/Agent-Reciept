import unittest

import helpers  # noqa: F401
from agent_receipts.redact import redact


class Redaction(unittest.TestCase):
    def assert_hidden(self, text, secret):
        out = redact(text)
        self.assertNotIn(secret, out, out)

    def test_hides_common_secrets(self):
        cases = [
            ("export API_KEY=sk-live-EXAMPLE1234567890abcdefghij", "EXAMPLE1234567890"),
            ("token gh" "p_abcdefghijklmnopqrstuvwxyz0123456789", "abcdefghijklmnopqrstuvwxyz0123456789"),
            ("AKIA" "IOSFODNN7EXAMPLE", "AKIA" "IOSFODNN7EXAMPLE"),
            ("curl -H 'Authorization: Bearer abc123def456ghi789jkl'", "abc123def456ghi789jkl"),
            ("psql postgres://admin:hunter2pass@db.example.com/app", "hunter2pass"),
            ("PASSWORD=correcthorsebattery", "correcthorsebattery"),
            ("mysql --password=s3cr3tvalue -u root", "s3cr3tvalue"),
            ("--token abc123tokenvalue", "abc123tokenvalue"),
            ("npm" "_abcdefghijklmnopqrstuvwxyz0123456789", "abcdefghijklmnopqrstuvwxyz0123456789"),
            ("xox" "b-1234567890-abcdefghij", "abcdefghij"),
            ("sk-" "ant-api03-abcdefghijklmnop", "abcdefghijklmnop"),
            ("AIza" "SyA-1234567890abcdefghijklmnopqrstuv", "1234567890abcdefghijklmnopqrstuv"),
            ("eyJ" "hbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijk", "eyJzdWIiOiIxMjM0NTY3ODkwIn0"),
            ("-----BEGIN RSA PRIVATE KEY-----\nMIIEabc\n-----END RSA PRIVATE KEY-----", "MIIEabc"),
        ]
        for text, secret in cases:
            with self.subTest(text=text[:40]):
                self.assert_hidden(text, secret)

    def test_no_leftover_fragments(self):
        out = redact("export API_KEY=sk-live-EXAMPLE1234567890abcdefghij && python run.py")
        self.assertIn("python run.py", out)
        self.assertNotIn("api key]", out.replace("[hidden api key]", ""))

    def test_leaves_normal_text_alone(self):
        for text in ["pytest -q tests/", "git commit -m 'fix tokenizer bug'", "cat README.md",
                     "Opened a file that may hold secrets (.env)", "https://example.com/docs/page",
                     "the quick brown fox", ""]:
            with self.subTest(text=text):
                self.assertEqual(redact(text), text)


if __name__ == "__main__":
    unittest.main()
