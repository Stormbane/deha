"""Narada Conversation entity (streaming).

Streams assistant text deltas from the Narada brain HTTP server's NDJSON
/converse endpoint into HA's chat_log. The Assist pipeline picks up the
deltas via intent_progress_event and begins TTS synthesis on partial
replies (sentence-batched by HA before dispatch to Wyoming TTS).

Protocol from the brain:
  Content-Type: application/x-ndjson, one JSON object per line:
    {"delta": "<partial text>"}
    ...
    {"final": {"continue_conversation": true|false}}
  Optional {"error": "..."} may appear before the final line.
"""

from __future__ import annotations

import json
import logging
from typing import AsyncIterator

import aiohttp
from homeassistant.components.conversation import (
    ConversationEntity,
    ConversationInput,
    ConversationResult,
)
from homeassistant.components.conversation.chat_log import (
    AssistantContent,
    AssistantContentDeltaDict,
    ChatLog,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import MATCH_ALL
from homeassistant.core import HomeAssistant
from homeassistant.helpers import intent
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_BRAIN_URL, DEFAULT_BRAIN_URL, DEFAULT_TIMEOUT_S, DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    brain_url = entry.data.get(CONF_BRAIN_URL, DEFAULT_BRAIN_URL).rstrip("/")
    entity = NaradaConversationEntity(entry.entry_id, brain_url)
    async_add_entities([entity])


class NaradaConversationEntity(ConversationEntity):
    """Conversation agent that streams from the Narada brain server."""

    _attr_has_entity_name = True
    _attr_name = "Narada"
    _attr_supports_streaming = True

    def __init__(self, entry_id: str, brain_url: str) -> None:
        self._attr_unique_id = entry_id
        self._brain_url = brain_url

    @property
    def supported_languages(self) -> list[str] | str:
        return MATCH_ALL

    async def _async_handle_message(
        self,
        user_input: ConversationInput,
        chat_log: ChatLog,
    ) -> ConversationResult:
        session = async_get_clientsession(self.hass)
        cid = user_input.conversation_id or "default"
        payload = {"conversation_id": cid, "text": user_input.text}
        response = intent.IntentResponse(language=user_input.language)

        # Captured by the inner generator's closure so the final line's
        # metadata reaches us after chat_log iteration ends.
        state: dict = {"continue_conversation": False, "errored": False}

        async def _stream_deltas() -> AsyncIterator[AssistantContentDeltaDict]:
            yield {"role": "assistant"}
            try:
                async with session.post(
                    f"{self._brain_url}/converse",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=DEFAULT_TIMEOUT_S),
                    headers={"Accept": "application/x-ndjson"},
                ) as r:
                    r.raise_for_status()
                    while True:
                        raw = await r.content.readline()
                        if not raw:
                            break
                        line = raw.decode("utf-8", errors="replace").strip()
                        if not line:
                            continue
                        try:
                            obj = json.loads(line)
                        except json.JSONDecodeError:
                            _LOGGER.warning(
                                "malformed line from brain: %r", line
                            )
                            continue
                        if "delta" in obj:
                            text = obj.get("delta") or ""
                            if text:
                                yield {"content": text}
                        elif "final" in obj:
                            final = obj.get("final") or {}
                            state["continue_conversation"] = bool(
                                final.get("continue_conversation")
                            )
                        elif "error" in obj:
                            state["errored"] = True
                            _LOGGER.error(
                                "brain error: %s", obj.get("error")
                            )
            except (aiohttp.ClientError, TimeoutError) as err:
                state["errored"] = True
                _LOGGER.error("Narada brain stream failed: %s", err)

        full_text_parts: list[str] = []
        try:
            async for content in chat_log.async_add_delta_content_stream(
                self.entity_id, _stream_deltas()
            ):
                if isinstance(content, AssistantContent) and content.content:
                    full_text_parts.append(content.content)
        except Exception as err:  # noqa: BLE001 - resilient outer catch
            _LOGGER.exception("delta stream consumption failed: %s", err)
            state["errored"] = True

        full_text = "".join(full_text_parts).strip()

        if state["errored"] and not full_text:
            response.async_set_error(
                intent.IntentResponseErrorCode.UNKNOWN,
                "Narada brain is unreachable.",
            )
            return ConversationResult(
                response=response,
                conversation_id=cid,
                continue_conversation=False,
            )

        if not full_text:
            response.async_set_speech("")
            return ConversationResult(
                response=response,
                conversation_id=cid,
                continue_conversation=False,
            )

        # Set speech so HA's voice pipeline fires the tts-start event to
        # the device (firmware uses it to switch to the speaking-state
        # image). HA will also dispatch a legacy Synthesize to wyoming —
        # the wyoming-side _streaming_active dedupe in wyoming_tts.py
        # drops that to prevent each sentence playing twice.
        response.async_set_speech(full_text)
        return ConversationResult(
            response=response,
            conversation_id=cid,
            continue_conversation=state["continue_conversation"],
        )
