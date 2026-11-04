from datetime import datetime, time
from zoneinfo import ZoneInfo

from django.test import SimpleTestCase

from reporting.ownership import ownership_date


class OwnershipDateTests(SimpleTestCase):
    day = (time(8, 0), time(20, 0))
    night = (time(20, 0), time(8, 0))

    def own(self, moment):
        return ownership_date(moment, *self.day, *self.night)

    def test_night_shift_belongs_to_the_day_it_opened(self):
        shanghai = ZoneInfo("Asia/Shanghai")
        evening = datetime(2026, 10, 31, 22, 0, tzinfo=shanghai)
        after_midnight = datetime(2026, 11, 1, 2, 0, tzinfo=shanghai)
        utc_same_moment = datetime(2026, 10, 31, 18, 0, tzinfo=ZoneInfo("UTC"))
        self.assertEqual(self.own(evening).isoformat(), "2026-10-31")
        self.assertEqual(self.own(after_midnight).isoformat(), "2026-10-31")
        self.assertEqual(self.own(utc_same_moment).isoformat(), "2026-10-31")

    def test_day_shift_belongs_to_that_calendar_day(self):
        shanghai = ZoneInfo("Asia/Shanghai")
        self.assertEqual(
            self.own(datetime(2026, 10, 31, 18, 0, tzinfo=shanghai)).isoformat(),
            "2026-10-31",
        )
        self.assertEqual(
            self.own(datetime(2026, 11, 1, 8, 0, tzinfo=shanghai)).isoformat(),
            "2026-11-01",
        )
        self.assertEqual(
            self.own(datetime(2026, 10, 31, 20, 0, tzinfo=shanghai)).isoformat(),
            "2026-10-31",
        )

    def test_gap_uses_the_shanghai_calendar_date(self):
        moment = datetime(2026, 10, 31, 18, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        owned = ownership_date(
            moment,
            time(8, 0),
            time(17, 0),
            time(20, 0),
            time(8, 0),
        )
        self.assertEqual(owned.isoformat(), "2026-10-31")

    def test_naive_datetime_is_rejected(self):
        from django.core.exceptions import ValidationError

        with self.assertRaises(ValidationError):
            self.own(datetime(2026, 10, 31, 22, 0))
