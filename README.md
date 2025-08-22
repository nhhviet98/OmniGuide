## OmniGuide (LiveKit + OpenAI Realtime + Cal.com + Screen Share)

This example extends the `frontdesk` agent with:
- Voice + chat via OpenAI Realtime
- Screen sharing support via LiveKit (works with the Playground)
- Calendar availability + bookings via Cal.com

### Prerequisites
- Python 3.11+
- LiveKit Cloud project (or self-hosted) and Playground access
- `OPENAI_API_KEY` (for Realtime)
- Optional `CAL_API_KEY` (to book meetings)

### Setup
1. Create a virtual env and install deps:
   ```bash
   cd examples/omniguide
   python -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   ```
2. Create a `.env` with your keys (see keys below).

Env vars:
```
LIVEKIT_URL= wss://<your-livekit-host>
LIVEKIT_API_KEY=
LIVEKIT_API_SECRET=
OPENAI_API_KEY=
# optional for real bookings; if not set, a FakeCalendar is used
CAL_API_KEY=
```

### Run with LiveKit Playground
1. Start the worker:
   ```bash
   python omniguide_agent.py
   ```
2. Open the LiveKit Playground, create a room, and connect the worker.
3. In the Playground UI, enable microphone, chat, and screen share.
4. Share your calendar window/screen. Ask questions by voice or chat.

### Notes
- Booking uses Cal.com APIs and creates an event type if needed.
- If `CAL_API_KEY` is not set, the agent simulates availability and booking.

