import time


class LastFrame:
    """Single-frame buffer for the most recent screen-share image.

    Maintains the latest frame and the timestamp when it was stored. The
    buffer is intentionally throttled by `update` to roughly 2 frames per
    second to avoid overloading downstream consumers (e.g., vision LLM calls).

    Attributes:
        frame: The most recent frame object or None if none received yet
        ts: Unix timestamp (float, seconds) when the frame was last updated
    """
    def __init__(self):
        self.frame = None
        self.ts = 0.0

    def update(self, f):
        """Store a new frame if the throttle interval has elapsed.

        Throttles updates to approximately 2 FPS by requiring at least 0.5
        seconds between stored frames. This reduces downstream processing
        load while still keeping the buffer reasonably fresh.

        Args:
            f: The latest video frame (e.g., a LiveKit rtc.VideoFrame instance).
        """
        # throttle to ~2 fps to avoid spamming the LLM
        now = time.time()
        if now - self.ts > 0.5:
            self.frame, self.ts = f, now

