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
from scheduler import due_holidays, recap_text, reminder_text, reminder_times  # noqa: E402

exam = datetime(2026, 12, 10, 9, 30, tzinfo=TZ)
assert reminder_times("Uni", exam) == [datetime(2026, 11, 26, 9, 30, tzinfo=TZ), datetime(2026, 12, 7, 20, 0, tzinfo=TZ)]
assert reminder_times("Work", exam) == [datetime(2026, 12, 9, 20, 0, tzinfo=TZ)]
assert reminder_times("PPI", exam) == [datetime(2026, 12, 9, 20, 0, tzinfo=TZ)]
club = datetime(2026, 12, 11, 21, 0, tzinfo=TZ)
assert reminder_times("Social", club) == [datetime(2026, 12, 10, 20, 0, tzinfo=TZ), datetime(2026, 12, 11, 19, 0, tzinfo=TZ)]
assert reminder_times("Sport", club) == reminder_times("Social", club)
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



def gev(id, summary, start, end, **extra):  # a Google Calendar event as the API returns it
    key = "date" if len(start) == 10 else "dateTime"
    return {"id": id, "summary": summary, "start": {key: start}, "end": {key: end}, **extra}


# morning recap on Thu 10 Dec (London is on UTC then), events from two calendars, out of order
day = date(2026, 12, 10)
recap_events = [
    gev("a", "[Social] Club night", "2026-12-10T21:00:00+00:00", "2026-12-11T02:00:00+00:00"),
    gev("b", "[Work] Shift", "2026-12-10T09:00:00+00:00", "2026-12-10T17:00:00+00:00"),
    gev("c", "[Uni] Essay", "2026-12-10T11:30:00+00:00", "2026-12-10T12:00:00+00:00"),  # deadline due 12:00
    gev("d", "Indonesian holiday", "2026-12-10", "2026-12-11"),
    gev("e", "Trip", "2026-12-08", "2026-12-12"),  # several days, includes today
    gev("f", "Yesterday's party", "2026-12-09T22:00:00+00:00", "2026-12-10T01:00:00+00:00"),  # started yesterday
    gev("g", "Declined meeting", "2026-12-10T10:00:00+00:00", "2026-12-10T11:00:00+00:00",
        attendees=[{"self": True, "responseStatus": "declined"}]),
    gev("h", "[Uni] Stats exam", "2026-12-15T09:30:00+00:00", "2026-12-15T11:30:00+00:00"),
    gev("i", "[Uni] Lab report", "2026-12-11T08:30:00+00:00", "2026-12-11T09:00:00+00:00"),  # deadline tomorrow
    gev("j", "[Work] Shift", "2026-12-12T09:00:00+00:00", "2026-12-12T17:00:00+00:00"),  # not Uni: no look-ahead
    gev("k", "[Uni] Too far", "2026-12-18T09:00:00+00:00", "2026-12-18T10:00:00+00:00"),  # 8 days ahead
]
assert recap_text(day, recap_events, {"c", "i"}) == """☀️ Today's agenda: Thu 10 Dec
• All day: Indonesian holiday
• All day: Trip
• 09:00–17:00 [Work] Shift
• 12:00 [Uni] Essay (due)
• 21:00–02:00 [Social] Club night

📚 Uni in the next 7 days
• Fri 11 Dec 09:00 Lab report (due)
• Tue 15 Dec 09:30–11:30 Stats exam"""
assert recap_text(day, [], set()) == "☀️ Today's agenda: Thu 10 Dec\nNothing scheduled today."

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
