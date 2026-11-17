import unittest
from unittest import mock

import requests

from shared import http_retry


def _resp(status=200, text="ok"):
    r = requests.Response()
    r.status_code = status
    r._content = text.encode()
    r.url = "https://example.test/"
    return r


class GetWithRetryTests(unittest.TestCase):
    def call(self, responses, **kw):
        sleeps = []
        with mock.patch.object(http_retry.requests, "get", side_effect=responses) as get:
            try:
                return http_retry.get_with_retry("https://example.test/", sleep=sleeps.append, **kw), get.call_count, sleeps
            except Exception as exc:  # noqa: BLE001 — returned for assertions
                return exc, get.call_count, sleeps

    def test_transient_403_then_ok(self):
        resp, calls, sleeps = self.call([_resp(403), _resp(200)])
        self.assertEqual(resp.status_code, 200)
        self.assertEqual((calls, sleeps), (2, [5.0]))

    def test_gives_up_loudly(self):
        exc, calls, _ = self.call([_resp(503)] * 3)
        self.assertIsInstance(exc, requests.HTTPError)
        self.assertEqual(calls, 3)

    def test_404_is_not_retried(self):
        exc, calls, _ = self.call([_resp(404), _resp(200)])
        self.assertIsInstance(exc, requests.HTTPError)
        self.assertEqual(calls, 1)

    def test_check_failure_is_retried_then_raised(self):
        def check(r):
            if "grid" not in r.text:
                raise ValueError("no grid")
        resp, calls, _ = self.call([_resp(text="placeholder"), _resp(text="grid")], check=check)
        self.assertEqual((resp.text, calls), ("grid", 2))
        exc, calls, _ = self.call([_resp(text="placeholder")] * 3, check=check)
        self.assertIsInstance(exc, ValueError)
        self.assertEqual(calls, 3)

    def test_network_error_is_retried(self):
        resp, calls, _ = self.call([requests.ConnectionError("reset"), _resp()])
        self.assertEqual((resp.status_code, calls), (200, 2))


if __name__ == "__main__":
    unittest.main()
