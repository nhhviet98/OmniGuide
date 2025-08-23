from __future__ import annotations

import base64
import datetime
import hashlib
import logging
import asyncio
import os
import random
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import aiohttp

from livekit.agents.utils import http_context


class SlotUnavailableError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)


@dataclass
class AvailableSlot:
    start_time: datetime.datetime
    duration_min: int

    @property
    def unique_hash(self) -> str:
        # unique id based on the start_time & duration_min
        raw = f"{self.start_time.isoformat()}|{self.duration_min}".encode()
        digest = hashlib.blake2s(raw, digest_size=5).digest()
        return f"ST_{base64.b32encode(digest).decode().rstrip('=').lower()}"


class Calendar(Protocol):
    async def initialize(self) -> None: ...
    async def schedule_appointment(
        self,
        *,
        start_time: datetime.datetime,
        attendee_email: str,
    ) -> None: ...
    async def list_available_slots(
        self, *, start_time: datetime.datetime, end_time: datetime.datetime
    ) -> list[AvailableSlot]: ...


class GoogleCalendar(Calendar):
    def __init__(
        self,
        *,
        access_token: str | None = None,
        timezone: str = "Asia/Ho_Chi_Minh",
        calendar_id: str = "primary",
        base_url: str | None = None,
        event_duration_min: int = 60,
    ) -> None:
        self.tz = ZoneInfo(timezone)
        self._timezone_name = timezone
        self._access_token = access_token or os.environ.get("GOOGLE_CAL_ACCESS_TOKEN")
        if not self._access_token:
            raise ValueError(
                "Google Calendar access token not provided. Set GOOGLE_CAL_ACCESS_TOKEN or pass access_token."
            )
        self._calendar_id = calendar_id
        self._base_url = base_url or os.environ.get(
            "GOOGLE_CAL_BASE_URL", "https://www.googleapis.com/calendar/v3/"
        )
        self._event_duration_min = int(event_duration_min)

        try:
            self._http_session = http_context.http_session()
        except RuntimeError:
            self._http_session = aiohttp.ClientSession()

        self._logger = logging.getLogger("google.calendar")

    async def initialize(self) -> None:
        # Verify that the calendar is accessible
        url = f"{self._base_url}calendars/{self._calendar_id}"
        async with self._http_session.get(headers=self._build_headers(), url=url) as resp:
            resp.raise_for_status()
            data = await resp.json()
            cal_summary = data.get("summary", self._calendar_id)
            self._logger.info(f"using google calendar: {cal_summary}")

    async def schedule_appointment(
        self, *, start_time: datetime.datetime
    ) -> None:
        # Ensure timezone-aware UTC for checks and ISO payloads
        start_utc = start_time.astimezone(datetime.timezone.utc)
        end_utc = start_utc + datetime.timedelta(minutes=self._event_duration_min)

        # Double-check availability to avoid overlapping bookings
        busy_blocks = await self._freebusy(start_utc=start_utc, end_utc=end_utc)
        if self._is_range_busy(start_utc, end_utc, busy_blocks):
            raise SlotUnavailableError("Time slot is no longer available")

        # Create the event
        create_url = f"{self._base_url}calendars/{self._calendar_id}/events?sendUpdates=all"

        # Use local timezone name for Google API (IANA name like "America/Los_Angeles")
        start_local = start_utc.astimezone(self.tz)
        end_local = end_utc.astimezone(self.tz)

        body = {
            "summary": "AI Feature Exploration Meeting",
            "start": {"dateTime": start_local.isoformat(), "timeZone": self._timezone_name},
            "end": {"dateTime": end_local.isoformat(), "timeZone": self._timezone_name},
        }

        async with self._http_session.post(
            headers=self._build_headers(), url=create_url, json=body
        ) as resp:
            # If Google ever returns a conflict/busy, surface as SlotUnavailableError
            if resp.status in (409,):
                raise SlotUnavailableError("Time slot is no longer available")
            data = await resp.json()
            if resp.status >= 400:
                message = data.get("error", {}).get("message", "Failed to create event")
                # Conservatively map obvious busy-style errors
                if any(k in message.lower() for k in ["busy", "conflict", "overlap"]):
                    raise SlotUnavailableError(message)
                resp.raise_for_status()

    async def list_available_slots(
        self, *, start_time: datetime.datetime, end_time: datetime.datetime
    ) -> list[AvailableSlot]:
        # Normalize to UTC for API, but compute candidate slots at 30-min granularity
        start_utc = start_time.astimezone(datetime.timezone.utc)
        end_utc = end_time.astimezone(datetime.timezone.utc)

        busy_blocks = await self._freebusy(start_utc=start_utc, end_utc=end_utc)

        # Generate 30-min candidate starts from start_utc up to end_utc - duration
        slots: list[AvailableSlot] = []
        duration = datetime.timedelta(minutes=self._event_duration_min)

        # Align start to the next 30-min boundary
        aligned_start = self._align_to_interval(start_utc, minutes=self._event_duration_min)
        current = aligned_start
        while current + duration <= end_utc:
            if not self._is_range_busy(current, current + duration, busy_blocks):
                slots.append(AvailableSlot(start_time=current, duration_min=self._event_duration_min))
            current += duration

        return slots

    async def _freebusy(
        self, *, start_utc: datetime.datetime, end_utc: datetime.datetime
    ) -> list[tuple[datetime.datetime, datetime.datetime]]:
        url = f"{self._base_url}freeBusy"
        body = {
            "timeMin": start_utc.isoformat(),
            "timeMax": end_utc.isoformat(),
            "timeZone": "UTC",
            "items": [{"id": self._calendar_id}],
        }

        async with self._http_session.post(headers=self._build_headers(), url=url, json=body) as resp:
            resp.raise_for_status()
            data = await resp.json()
            cal = data.get("calendars", {}).get(self._calendar_id, {})
            busy_list = []
            for b in cal.get("busy", []):
                b_start = datetime.datetime.fromisoformat(b["start"].replace("Z", "+00:00"))
                b_end = datetime.datetime.fromisoformat(b["end"].replace("Z", "+00:00"))
                busy_list.append((b_start, b_end))
            return busy_list

    def _is_range_busy(
        self,
        start_dt: datetime.datetime,
        end_dt: datetime.datetime,
        busy_blocks: list[tuple[datetime.datetime, datetime.datetime]],
    ) -> bool:
        return any(start_dt < b_end and end_dt > b_start for b_start, b_end in busy_blocks)

    def _align_to_interval(self, dt: datetime.datetime, *, minutes: int) -> datetime.datetime:
        # Aligns dt forward to the next interval boundary in UTC
        minute = (dt.minute // minutes) * minutes
        base = dt.replace(minute=minute, second=0, microsecond=0)
        if base < dt:
            base += datetime.timedelta(minutes=minutes)
        return base

    def _build_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json",
        }

    @property
    def event_duration_min(self) -> int:
        return self._event_duration_min
