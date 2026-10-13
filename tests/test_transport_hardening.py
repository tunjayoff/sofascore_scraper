"""
İstek katmanının (sofascore_scraper/client/transport.py) c projesinden geri taşınan üretim düzeltmeleri (S1).

c projesi (sofascore_site/rest.py) bu katmanı buradan taşıdı ve üretim benzeri koşularda (1-5) düzeltti; düzeltmeler
burada aynı yöntemle çalışır. Ağ yok: curl ve köprü sahtedir.
"""
from __future__ import annotations

import datetime as dt
from email.utils import format_datetime

import pytest

from sofascore_scraper.client import transport


# --- B11: Retry-After ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        (None, 9),
        ("", 9),
        ("7", 7),
        ("0", 1),
        ("120", 120),
        ("121", 120),  # eskiden 121 sn beklenirdi
        ("3600", 120),  # eskiden bir saat
        ("soon", 9),
    ],
)
def test_an_integer_retry_after_is_capped_like_the_date_form(header, expected):
    assert transport._parse_retry_after_seconds(header, 9) == expected


def test_a_date_retry_after_stays_capped():
    later = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=2)
    assert transport._parse_retry_after_seconds(format_datetime(later, usegmt=True), 9) == 120
    earlier = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=2)
    assert transport._parse_retry_after_seconds(format_datetime(earlier, usegmt=True), 9) == 1
