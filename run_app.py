from __future__ import annotations

import asyncio
import logging
import contextlib
import os
from dataclasses import dataclass
import httpx
from dotenv import load_dotenv
from livekit import rtc
from livekit.agents import (
    AgentSession,
    AutoSubscribe,
    JobContext,
    RoomInputOptions,
    RoomOutputOptions,
    WorkerOptions,
    cli,
    JobExecutorType,
)
from livekit.plugins import deepgram, openai, silero
from livekit.plugins.turn_detector.multilingual import MultilingualModel

from agent.screen_agent import ScreenQAAgent, last_screen
from tools.calendar_api import Calendar, GoogleCalendar


load_dotenv()

logger = logging.getLogger("screen-agent")
logger.setLevel(logging.INFO)


@dataclass
class Userdata:
    cal: Calendar


async def entrypoint(ctx: JobContext):
    await ctx.connect(auto_subscribe=AutoSubscribe.SUBSCRIBE_ALL)

    # Ensure we subscribe to screen-share when available (works with Playground)
    for participant in ctx.room.remote_participants.values():
        for pub in participant.track_publications.values():
            with contextlib.suppress(Exception):
                if (
                    pub.kind == rtc.TrackKind.KIND_VIDEO
                    and getattr(pub, "source", None) == rtc.TrackSource.SOURCE_SCREENSHARE
                ):
                    pub.set_subscribed(True)

    @ctx.room.on("track_subscribed")
    def on_track_subscribed(track: rtc.Track, pub: rtc.RemoteTrackPublication, participant: rtc.RemoteParticipant):
        # Prefer checking track source when available to detect screen share
        if isinstance(track, rtc.RemoteVideoTrack) and (
            pub.source == rtc.TrackSource.SOURCE_SCREENSHARE
            or "screen" in (pub.name or "").lower()
        ):
            async def consume():
                async for ev in rtc.VideoStream(track):
                    # ev.frame is an rtc.VideoFrame (suitable for ChatImage)
                    last_screen.update(ev.frame)
            asyncio.create_task(consume())

    # Nothing to reset for image injection; state-less

    # Setup GoogleCalendar
    cal = GoogleCalendar()
    await cal.initialize()

    session = AgentSession[Userdata](
        userdata=Userdata(cal=cal),
        stt=deepgram.STT(),
        llm=openai.LLM(model="gpt-4o"),
        tts=openai.TTS(model="gpt-4o-mini-tts"),
        turn_detection=MultilingualModel(),
        vad=silero.VAD.load(),
    )

    await session.start(
        agent=ScreenQAAgent(),
        room=ctx.room,
        room_input_options=RoomInputOptions(
            audio_enabled=True,
            text_enabled=True,
            video_enabled=True,
        ),
        room_output_options=RoomOutputOptions(transcription_enabled=True),
    )

    # Greet and instruct user to share their screen for QA
    await session.generate_reply(
        instructions=(
            "Hello! Share your screen and ask a question about it. "
            "You can speak or type your question."
        )
    )


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint, job_executor_type=JobExecutorType.THREAD))


