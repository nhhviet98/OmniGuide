from __future__ import annotations

import asyncio
import base64
import logging
import contextlib
from typing import Optional

from dotenv import load_dotenv
from utils.screen_stream import LastFrame
from livekit import rtc
from livekit.agents import (
    Agent,
    AgentSession,
    AutoSubscribe,
    JobContext,
    RoomInputOptions,
    RoomOutputOptions,
    WorkerOptions,
    cli,
    JobExecutorType,
)
from livekit.agents.llm.chat_context import ChatContext, ImageContent
from livekit.agents.utils.images import EncodeOptions, ResizeOptions, encode
from livekit.plugins import cartesia, deepgram, openai, silero
from livekit.plugins.turn_detector.multilingual import MultilingualModel


load_dotenv()

logger = logging.getLogger("screen-agent")
logger.setLevel(logging.INFO)

last_screen = LastFrame()


class ScreenQAAgent(Agent):
    def __init__(self) -> None:
        super().__init__(
            instructions=(
                "You are a concise, helpful assistant. "
                "Listen to the user's voice or chat. "
                "When asked about what's on the user's shared screen, "
                "take a quick snapshot and answer succinctly. "
                "If there is no active screen-share, ask the user to start sharing."
            )
        )

async def _answer_about_screen(question: str) -> str:
    frame = last_screen.frame
    if frame is None:
        return "No screen-share frame available yet. Please start sharing your screen."

    img_bytes = encode(
        frame,
        EncodeOptions(
            format="PNG",
            quality=75,
        ),
    )
    data_url = f"data:image/jpeg;base64,{base64.b64encode(img_bytes).decode('utf-8')}"

    chat_ctx = ChatContext.empty()
    chat_ctx.add_message(
        role="user",
        content=[
            ImageContent(image=data_url, inference_detail="high"),
            f"Answer concisely: {question}",
        ],
    )

    vision_llm = openai.LLM(model="gpt-4o-mini", temperature=0.4)
    parts: list[str] = []
    async for chunk in vision_llm.chat(chat_ctx=chat_ctx).to_str_iterable():
        parts.append(chunk)
    return "".join(parts).strip()


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
        if isinstance(track, rtc.RemoteVideoTrack):
            # Prefer checking track source when available to detect screen share
            if pub.source == rtc.TrackSource.SOURCE_SCREENSHARE or "screen" in (pub.name or "").lower():
                async def consume():
                    async for ev in rtc.VideoStream(track):
                        # ev.frame is an rtc.VideoFrame (suitable for ChatImage) :contentReference[oaicite:2]{index=2}
                        last_screen.update(ev.frame)
                asyncio.create_task(consume())

    session = AgentSession(
        stt=deepgram.STT(),
        llm=openai.LLM(model="gpt-4o"),
        tts=openai.TTS(model="gpt-4o-mini-tts"),
        turn_detection=MultilingualModel(),
        vad=silero.VAD.load(),
        max_tool_steps=1,
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

    # Provide a convenience command phrase for screen QA
    # The user can simply ask: "What is on my screen?" or "Summarize this page."
    @session.on("user_input_transcribed")
    def _on_transcript(evt):
        async def _handle():
            with contextlib.suppress(Exception):
                # Light intent detection: trigger only if asking about screen
                text = (getattr(evt, "transcript", "") or "").lower()
                answer = await _answer_about_screen(question=text)
                await session.generate_reply(instructions=answer)

        asyncio.create_task(_handle())


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint, job_executor_type=JobExecutorType.THREAD))


