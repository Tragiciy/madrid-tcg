import unittest
from unittest import mock

import requests

from scrapers import arte9


class Arte9RetryTests(unittest.TestCase):
    def test_retries_timeouts_then_succeeds(self):
        ok = mock.Mock(status_code=200)
        with mock.patch.object(arte9.requests, "get",
                               side_effect=[requests.Timeout("slow"), ok]) as get, \
             mock.patch.object(arte9.time, "sleep"):
            self.assertIs(arte9._get_page(1), ok)
        self.assertEqual(get.call_count, 2)

    def test_gives_up_after_retries(self):
        with mock.patch.object(arte9.requests, "get", side_effect=requests.Timeout("slow")) as get, \
             mock.patch.object(arte9.time, "sleep"):
            with self.assertRaises(requests.Timeout):
                arte9._get_page(1)
        self.assertEqual(get.call_count, arte9.RETRIES + 1)

    def test_client_errors_are_not_retried(self):
        bad = mock.Mock(status_code=400)
        with mock.patch.object(arte9.requests, "get", return_value=bad) as get:
            self.assertIs(arte9._get_page(9), bad)
        self.assertEqual(get.call_count, 1)


if __name__ == "__main__":
    unittest.main()
