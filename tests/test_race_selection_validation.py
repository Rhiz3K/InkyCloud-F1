"""Shared season and round validation contract."""

import pytest

from app.utils.f1_season import get_max_supported_f1_season, race_selection_error


@pytest.mark.parametrize(
    "year,round_num,expected",
    [
        (1949, 1, "Unsupported F1 season"),
        (1949, 0, "Unsupported F1 season"),
        (9999, 1, "Unsupported F1 season"),
        (2017, 0, "Invalid race round"),
        (2017, 31, "Invalid race round"),
        (1950, 1, None),
        (2017, 30, None),
        (get_max_supported_f1_season(), 1, None),
    ],
)
def test_race_selection_error(year, round_num, expected):
    assert race_selection_error(year, round_num) == expected
