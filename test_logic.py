"""Self-check for the date logic. Run: python test_logic.py"""
import os

os.environ.setdefault("BRIDGE_SECRET", "test")
from datetime import date, datetime  # noqa: E402
from typing import get_args  # noqa: E402

import scheduler  # noqa: E402
from agents import Intake  # noqa: E402
from calendar_service import COLORS  # noqa: E402
from config import TZ  # noqa: E402
from main import missing, span  # noqa: E402
from scheduler import due_holidays, reminder_text, reminder_times  # noqa: E402

exam = datetime(2026, 12, 10, 9, 30, tzinfo=TZ)
assert reminder_times("Uni", exam) == [datetime(2026, 11, 26, 9, 30, tzinfo=TZ), datetime(2026, 12, 7, 20, 0, tzinfo=TZ)]
assert reminder_times("Work", exam) == [datetime(2026, 12, 9, 20, 0, tzinfo=TZ)]
assert reminder_times("PPI", exam) == [datetime(2026, 12, 9, 20, 0, tzinfo=TZ)]
club = datetime(2026, 12, 11, 21, 0, tzinfo=TZ)
assert reminder_times("Social", club) == [datetime(2026, 12, 10, 20, 0, tzinfo=TZ), datetime(2026, 12, 11, 19, 0, tzinfo=TZ)]
# every category the model can return needs a calendar colour
assert set(COLORS) == set(get_args(get_args(Intake.model_fields["Category"].annotation)[0]))

# no reminder once its event has started; jobs queued before start was stored still send
sent = []
scheduler.send_whatsapp = sent.append
scheduler.send_reminder("late", start=datetime(2020, 1, 1, tzinfo=TZ))
scheduler.send_reminder("on time", start=datetime(2099, 1, 1, tzinfo=TZ))
scheduler.send_reminder("old job")
assert sent == ["on time", "old job"]
assert "Clock in at 09:30" in reminder_text("Tesco", "Work", exam)
# 14 days before, across the clocks going back, stays at the same wall-clock time
assert reminder_times("Uni", datetime(2026, 11, 3, 10, 0, tzinfo=TZ))[0] == datetime(2026, 10, 20, 10, 0, tzinfo=TZ)

hols = [{"name": "A", "date": "2026-11-24"}, {"name": "B", "date": "2026-11-25"}, {"name": "C", "date": "2026-10-01"}]
assert due_holidays(hols, date(2026, 10, 13)) == [("A", "2026-11-24", 42)]

shift = {"Event_Name": "Shift", "Date": "2026-12-10", "Time": "22:00", "End_Time": "06:00", "Category": "Work"}
assert missing(shift) == []
assert span(shift)[1] == datetime(2026, 12, 11, 6, 0)  # overnight
assert missing({**shift, "End_Time": None}) == ["End_Time"]
assert missing({"Event_Name": "Essay", "Date": "2026-12-10", "Category": "Uni", "Is_Deadline": True}) == ["Time"]
assert missing({"Event_Name": "Essay", "Date": "next week", "Time": "12:00", "Category": "Uni", "Is_Deadline": True}) == ["Date", "Time"]
deadline = {"Event_Name": "Essay", "Date": "2026-12-10", "Time": "12:00", "Category": "Uni", "Is_Deadline": True}
assert missing(deadline) == [] and span(deadline)[0] == span(deadline)[1]
print("all checks passed")
