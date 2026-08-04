from django.core.cache import cache
from django.test import SimpleTestCase

from care_im_wrapper.core.rate_limit import is_rate_limited
from tests.utils import override_test_cache


@override_test_cache()
class IsRateLimitedTests(SimpleTestCase):
    # The cache override isolates per class, not per method, and these tests share a phone
    # number -- which is the rate-limit key.
    def setUp(self):
        super().setUp()
        cache.clear()

    def test_first_call_is_not_rate_limited(self):
        self.assertFalse(is_rate_limited("+1234567890"))

    def test_exactly_ten_calls_are_not_rate_limited(self):
        results = []
        for _ in range(10):
            results.append(is_rate_limited("+1234567890"))
        self.assertTrue(all(r is False for r in results))

    def test_eleventh_call_is_rate_limited(self):
        results = []
        for _ in range(11):
            results.append(is_rate_limited("+1234567890"))
        self.assertTrue(all(r is False for r in results[:10]))
        self.assertTrue(results[10])

    def test_different_phone_numbers_have_independent_counters(self):
        # Exhaust "+911111111111" for 10 calls
        for _ in range(10):
            is_rate_limited("+911111111111")

        # One call on "+912222200000" must be False
        self.assertFalse(is_rate_limited("+912222200000"))

    def test_rate_limited_phone_number_stays_limited_on_subsequent_calls(self):
        # 11 calls on "+913333333333" (11th True)
        for _ in range(11):
            is_rate_limited("+913333333333")

        # Then a 12th call, also assert True
        self.assertTrue(is_rate_limited("+913333333333"))
