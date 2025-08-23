from __future__ import annotations

import logging
from pathlib import Path

from livekit.agents import Agent, RunContext, ToolError, function_tool
from typing import Literal
import datetime
from zoneinfo import ZoneInfo
from tools.calendar_api import SlotUnavailableError
from livekit.agents.llm.chat_context import ChatContext, ImageContent

from utils.screen_stream import LastFrame


logger = logging.getLogger("screen-agent")
logger.setLevel(logging.INFO)


last_screen = LastFrame()


class ScreenQAAgent(Agent):
    def __init__(self) -> None:
        prompt_path = Path(__file__).parent.parent / "promts" / "system_prompt.md"
        system_prompt = prompt_path.read_text(encoding="utf-8")
        super().__init__(instructions=system_prompt)

        self._slots_map: dict[str, object] = {}
        self.tz = ZoneInfo("Asia/Ho_Chi_Minh")

    def llm_node(
        self,
        chat_ctx: ChatContext,
        tools: list,
        model_settings,
    ):
        """Attach the latest screen image to the current user turn if available."""
        try:
            last_msg = None
            for item in reversed(chat_ctx.items):
                if getattr(item, "type", None) == "message" and getattr(item, "role", None) == "user":
                    last_msg = item
                    break

            if last_msg is not None:
                frame = last_screen.frame
                if frame is not None:
                    content_list = list(getattr(last_msg, "content", []))
                    content_list.insert(0, ImageContent(image=frame, inference_detail="high"))
                    last_msg.content = content_list
                    logger.info("Attached current screen image to user turn")
                else:
                    logger.info("No screen-share available; cannot attach image")
        except Exception:
            logger.exception("Failed to inject screen image into LLM turn")

        return Agent.default.llm_node(self, chat_ctx, tools, model_settings)

    @function_tool
    async def list_available_slots(
        self, ctx: RunContext, range: Literal["+2week", "+1month", "+3month", "default"]
    ) -> str:
        """
        Return a plain-text list of available slots, one per line.

        <slot_id> – <Weekday>, <Month> <Day>, <Year> at <HH:MM> <TZ> (<relative time>)

        Infer the appropriate ``range`` implicitly from context; do not ask the user to pick.
        """
        # Use the session timezone if present; default to UTC
        now = datetime.datetime.now(self.tz)

        if range == "+2week" or range == "default":
            range_days = 14
        elif range == "+1month":
            range_days = 30
        elif range == "+3month":
            range_days = 90
        else:
            range_days = 14

        lines: list[str] = []
        self._slots_map.clear()

        for slot in await ctx.userdata.cal.list_available_slots(
            start_time=now, end_time=now + datetime.timedelta(days=range_days)
        ):
            local = slot.start_time.astimezone(self.tz)
            delta = local - now
            days = delta.days
            seconds = delta.seconds

            if local.date() == now.date():
                if seconds < 3600:
                    rel = "in less than an hour"
                else:
                    rel = "later today"
            elif local.date() == (now.date() + datetime.timedelta(days=1)):
                rel = "tomorrow"
            elif days < 7:
                rel = f"in {days} days"
            elif days < 14:
                rel = "in 1 week"
            else:
                rel = f"in {days // 7} weeks"

            lines.append(
                f"{slot.unique_hash} – {local.strftime('%A, %B %d, %Y')} at {local:%H:%M} {local.tzname()} ({rel})"
            )
            self._slots_map[slot.unique_hash] = slot

        return "\n".join(lines) or "No slots available at the moment."

    @function_tool
    async def schedule_appointment(self, ctx: RunContext, slot_id: str) -> str | None:
        """
        Schedule an appointment at the given slot.

        Args:
            slot_id: The identifier for the selected time slot (as shown in the list of available slots).
        """
        if not (slot := self._slots_map.get(slot_id)):
            raise ToolError(f"error: slot {slot_id} was not found")

        if ctx.speech_handle.interrupted:
            return

        ctx.disallow_interruptions()

        try:
            await ctx.userdata.cal.schedule_appointment(
                start_time=slot.start_time
            )
        except SlotUnavailableError:
            raise ToolError("This slot isn't available anymore") from None

        local = slot.start_time.astimezone(self.tz)
        return f"The appointment was successfully scheduled for {local.strftime('%A, %B %d, %Y at %H:%M %Z')}."

