# WhatsApp assistant

Local WhatsApp bot that adds events to Google Calendar and sends reminders. All AI runs on this PC via Ollama (`qwen2.5:14b`).

## One-time setup

1. **Shared secret** (both processes read it), in PowerShell:
   `setx BRIDGE_SECRET (-join ((48..57)+(97..122) | Get-Random -Count 32 | % {[char]$_}))`
   then open a new terminal.
2. **Your number**: put it in `owner_number.txt` (digits only, with country code, e.g. `447...`). Git ignores
   this file, so the number never reaches GitHub.
3. **Google Calendar**: in Google Cloud Console, enable the Google Calendar API, create an OAuth client of type
   *Desktop app*, download it as `credentials.json` into this folder, then run
   `.\.venv\Scripts\python.exe calendar_service.py` and sign in.
4. **WhatsApp**: run `npm start` and scan the QR code with the **spare** phone (WhatsApp > Linked devices).

## Run (two terminals)

```
.\.venv\Scripts\python.exe main.py
npm start
```

Ollama must be running (it starts with Windows after install).

## Use

- `Shift at Tesco Friday 9:00 to 17:00` / `Stats exam 10 Dec 9:30-11:30` / `Essay due 12 Dec 12:00`
- `move my Friday shift to 10:00`, `cancel the stats exam` (the bot asks you to confirm)
- `what's on this week?`
- `never mind` drops whatever the bot is waiting on.

## Files

`main.py` (FastAPI + message flow), `agents.py` (Qwen intake), `calendar_service.py`, `scheduler.py`
(reminder rules + holiday check), `db_models.py`, `whatsapp_bridge.js`, `holidays.json` (update yearly),
`test_logic.py` (run it after changing date logic). State lives in `assistant.db`; don't commit it,
`token.json`, `credentials.json`, `owner_number.txt` or `.wwebjs_auth/` (all in `.gitignore`).
