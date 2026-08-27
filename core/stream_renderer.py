"""
Stream Renderer — Progressive Discord Response Updates

Makes the bot feel instant and alive by showing animated status while the AI
thinks, then editing the message in-place with the full response.
"""
from __future__ import annotations
import asyncio
import time
from typing import Optional
import discord
from core.logger import get_logger

log = get_logger(__name__)

# Animated thinking frames that cycle while the AI processes
_THINKING_FRAMES = [
    "🤔 Thinking...",
    "🧠 Processing...",
    "⚡ Working on it...",
    "🔍 Analyzing...",
    "💭 Almost there...",
]

# How often to update the "thinking" animation (seconds)
_ANIMATION_INTERVAL = 2.5

# Only update the animated message if AI takes longer than this (seconds)
# Short responses don't need animation
_ANIMATION_THRESHOLD = 1.5


class StreamRenderer:
    """
    Manages a live Discord message that updates progressively.

    Usage:
        renderer = StreamRenderer(channel, reply_to=message)
        await renderer.start()   # sends initial "thinking" message
        # ... do AI work ...
        await renderer.finish(response_text)  # edits to final response
        # OR
        await renderer.error("Something went wrong")  # edits to error
    """

    def __init__(
        self,
        channel: discord.TextChannel | discord.DMChannel,
        reply_to: Optional[discord.Message] = None,
    ) -> None:
        self._channel = channel
        self._reply_to = reply_to
        self._sent_message: Optional[discord.Message] = None
        self._animation_task: Optional[asyncio.Task] = None
        self._start_time = time.monotonic()
        self._frame_idx = 0
        self._finished = False

    async def start(self) -> None:
        """Send the initial thinking message and start animation loop."""
        initial = _THINKING_FRAMES[0]
        try:
            if self._reply_to:
                self._sent_message = await self._reply_to.reply(initial)
            else:
                self._sent_message = await self._channel.send(initial)
        except Exception as exc:
            log.warning("StreamRenderer: failed to send initial message: %s", exc)
            return
        # Start animation loop after threshold
        self._animation_task = asyncio.create_task(self._animate())

    async def _animate(self) -> None:
        """Cycle through thinking frames until finished."""
        await asyncio.sleep(_ANIMATION_THRESHOLD)
        while not self._finished and self._sent_message:
            elapsed = time.monotonic() - self._start_time
            self._frame_idx = (self._frame_idx + 1) % len(_THINKING_FRAMES)
            frame = _THINKING_FRAMES[self._frame_idx]
            status = f"{frame} `({elapsed:.1f}s)`"
            try:
                await self._sent_message.edit(content=status)
            except Exception:
                break  # Discord edit failed — stop animating, not fatal
            await asyncio.sleep(_ANIMATION_INTERVAL)

    async def finish(self, content: str, split_func=None) -> None:
        """
        Stop animation and edit the message with the final response.
        If content is too long for one message (>1950 chars), edit the first
        message with chunk 1 and send remaining chunks as follow-ups.
        """
        self._finished = True
        if self._animation_task and not self._animation_task.done():
            self._animation_task.cancel()
            try:
                await self._animation_task
            except asyncio.CancelledError:
                pass

        if not self._sent_message:
            return

        # Split into chunks if needed
        if split_func:
            chunks = split_func(content, max_length=1950)
        else:
            # Simple split
            chunks = [content[i:i+1950] for i in range(0, len(content), 1950)] or [content]

        try:
            await self._sent_message.edit(content=chunks[0])
        except Exception as exc:
            log.warning("StreamRenderer: failed to edit final message: %s", exc)

        # Send overflow chunks as follow-up messages
        for chunk in chunks[1:]:
            try:
                if self._reply_to:
                    await self._reply_to.channel.send(chunk)
                else:
                    await self._channel.send(chunk)
            except Exception as exc:
                log.warning("StreamRenderer: failed to send overflow chunk: %s", exc)

    async def error(self, error_text: str) -> None:
        """Stop animation and show error in the message."""
        self._finished = True
        if self._animation_task and not self._animation_task.done():
            self._animation_task.cancel()
            try:
                await self._animation_task
            except asyncio.CancelledError:
                pass
        if self._sent_message:
            try:
                await self._sent_message.edit(content=error_text)
            except Exception:
                pass
