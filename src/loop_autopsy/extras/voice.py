"""ElevenLabs client for loop-autopsy (optional ``[voice]`` extra).

Install with ``pip install loop-autopsy[voice]``. The SDK reads the API key
from the ``ELEVENLABS_API_KEY`` environment variable; ``get_client`` loads it
from a ``.env`` file first if one exists. Never put the key in code.

The SDK is imported inside ``get_client`` so importing this module (or the
rest of loop_autopsy) works without the extra installed.
"""

from __future__ import annotations

from typing import Any


def get_client(env_path: str = ".env") -> Any:
    """Return an ``elevenlabs.client.ElevenLabs`` client.

    Loads ``env_path`` into the environment (existing variables win), then
    lets the SDK pick up ``ELEVENLABS_API_KEY``.
    """
    try:
        from dotenv import load_dotenv
        from elevenlabs.client import ElevenLabs
    except ImportError as exc:  # pragma: no cover - depends on the extra
        raise ImportError(
            "ElevenLabs support needs the [voice] extra: pip install loop-autopsy[voice]"
        ) from exc

    load_dotenv(env_path)
    return ElevenLabs()
