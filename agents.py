"""Intake agent: one local Qwen call turns a WhatsApp message into structured JSON."""
import json
import re
from datetime import datetime, timedelta
from typing import Literal

import ollama
from pydantic import BaseModel, ValidationError

from config import MODEL, TZ


class Intake(BaseModel):
    Intent: Literal["create", "edit", "cancel", "list", "other"]
    Event_Name: str | None = None
    Date: str | None = None
    Time: str | None = None
    End_Time: str | None = None
    Category: Literal["Uni", "Work", "PPI", "Sport", "Social"] | None = None
    Is_Deadline: bool = False
    Target_Ids: list[int] = []
    List_Days: int | None = None


class IntakeError(Exception):
    pass


PROMPT = """You turn WhatsApp messages into JSON for a personal scheduling assistant.
Today is {today}. Timezone: Europe/London.

Intent:
- create: add a new event. A message that just describes an event is create, even if a similar event is listed below.
- edit: ONLY when the user asks to change, move or reschedule an existing event. Fill only the fields being changed,
  with their NEW values: "move the meeting to 4pm" -> Time "16:00".
- cancel: ONLY when the user asks to cancel, delete or remove an existing event.
- list: asking what is coming up. List_Days = how many days ahead (default 7).
- other: anything else.

Fields:
- Event_Name: short title, e.g. "Statistics exam", "Shift at Tesco", "PPI committee meeting".
- Date: YYYY-MM-DD. Look relative dates ("Friday", "tomorrow", "next Monday") up in the calendar below; don't calculate.
- Time: HH:MM, 24-hour. The start time, or the due time for a deadline.
- End_Time: HH:MM, 24-hour.
- Category: Uni (exams, essays, assignments, coursework, any deadline), Work (shifts), PPI (anything that mentions PPI,
  including its socials and sports), Sport (sport you play or train: tennis, football, gym, runs), Social (club
  nights, parties, going out, watching sport). Sport and Social are only for things that don't mention PPI.
- Is_Deadline: true for anything "due" (essays, assignments, coursework); its Category is Uni.
- Target_Ids: for edit or cancel, the ids of the upcoming events below that the user means.

Rules:
- Use null for anything not clearly stated. Never guess. The user will be asked for anything you leave null.
- "next week", "sometime", "soon" with no specific day -> Date null.
- "at 3", "at 7" with no am/pm -> Time null. A range like "9 to 5" or "9-17" is clear.
- Output only the JSON object.

Examples (the ids here are made up; use the real list below):
- "Shift at Costa {ex_day} 10am to 6pm" -> {{"Intent": "create", "Event_Name": "Shift at Costa", "Date": "{ex_date}", "Time": "10:00", "End_Time": "18:00", "Category": "Work"}}
- "lab report due tomorrow 9am" -> {{"Intent": "create", "Event_Name": "Lab report", "Date": "{tomorrow}", "Time": "09:00", "Category": "Uni", "Is_Deadline": true}}
- "push my dentist appointment to 11am" (dentist is #7) -> {{"Intent": "edit", "Time": "11:00", "Target_Ids": [7]}}
- "tennis {ex_day} 7-9pm" -> {{"Intent": "create", "Event_Name": "Tennis", "Date": "{ex_date}", "Time": "19:00", "End_Time": "21:00", "Category": "Sport"}}
- "watching the Arsenal match at the pub {ex_day} 8pm to 10pm" -> {{"Intent": "create", "Event_Name": "Arsenal match at the pub", "Date": "{ex_date}", "Time": "20:00", "End_Time": "22:00", "Category": "Social"}}
- "PPI futsal night {ex_day} 6pm to 8pm" -> {{"Intent": "create", "Event_Name": "PPI futsal night", "Date": "{ex_date}", "Time": "18:00", "End_Time": "20:00", "Category": "PPI"}}
- "badminton social sometime next week at 7" -> {{"Intent": "create", "Event_Name": "Badminton social", "Category": "Sport"}} (no day, no am/pm)

Calendar:
{calendar}

Upcoming events:
{events}"""
WEEKS = ["this week", "next week", "in two weeks", "in three weeks"]
# shortcut: keyword check, because Qwen 14B often labels a new event "edit" when a similar one exists;
# an edit phrased without these words ("Friday's shift is 10-6 instead") becomes a new event. Widen the lists if that bites.
EDIT_WORDS = re.compile(r"\b(move|change|reschedule|push|postpone|bring|rename|update|edit|now|instead)", re.I)
CANCEL_WORDS = re.compile(r"\b(cancel|delete|remove|drop|scrap|called off|not going)", re.I)
# The model still guesses on vague wording, so enforce "ask, don't guess" here.
BARE_HOUR = re.compile(r"\bat\s+(1[0-2]|[1-9])\b(?!\s*(:|\.\d|am|pm|a\.m|p\.m|o'?clock))", re.I)
VAGUE_DAY = re.compile(r"\b(next week|this week|sometime|soon)\b", re.I)
SPECIFIC_DAY = re.compile(r"(mon|tue|wed|thu|fri|sat|sun)|tomorrow|today|tonight|\d{1,2}(st|nd|rd|th)?\s+[a-z]{3}|\d/\d", re.I)

FOLLOW_UP = """
The user was just asked for missing details of this unfinished new event. Unless they clearly ask for something
else, their message answers that question: Intent is create, and fill in the fields they give.
{draft}"""


def parse(text: str, upcoming: list[str], draft: dict | None = None) -> Intake:
    now = datetime.now(TZ)
    today = now.date()
    monday = today - timedelta(days=today.weekday())
    days = [today + timedelta(days=i) for i in range(22)]
    ex = today + timedelta(days=3)
    system = PROMPT.format(
        today=now.strftime("%A %Y-%m-%d %H:%M"),
        calendar="\n".join(f"{d:%A} {d} ({WEEKS[(d - monday).days // 7]})" for d in days),
        ex_day=f"{ex:%A}", ex_date=ex, tomorrow=today + timedelta(days=1),
        events="\n".join(upcoming) or "(none)",
    )
    if draft:
        system += FOLLOW_UP.format(draft=json.dumps(draft))
    try:
        resp = ollama.chat(
            model=MODEL,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": text}],
            format=Intake.model_json_schema(),  # Ollama constrains output to this schema: raw JSON, no markdown
            options={"temperature": 0},
        )
        p = Intake.model_validate_json(resp.message.content)
    except ValidationError as e:
        raise IntakeError(f"model returned unusable JSON: {e}") from e
    except Exception as e:  # connection refused, model not pulled, etc.
        raise IntakeError(f"local model unavailable: {e}") from e
    if upcoming and (p.Intent == "edit" and not EDIT_WORDS.search(text)
                     or p.Intent == "cancel" and not CANCEL_WORDS.search(text)):
        return parse(text, [], draft)  # re-read without existing events, so it can only be a new one
    if BARE_HOUR.search(text):
        p.Time = None
    if VAGUE_DAY.search(text) and not SPECIFIC_DAY.search(text):
        p.Date = None
    return p
