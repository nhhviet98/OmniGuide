from __future__ import annotations

import logging
from pathlib import Path

from livekit.agents import Agent
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


