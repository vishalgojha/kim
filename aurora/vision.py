"""Multimodal screen description for computer_action (vision on `see`)."""

from __future__ import annotations

import base64
import os
from typing import Dict, Optional

import httpx

DEFAULT_IMAGE_MODEL = "gemini-2.5-flash"
DEFAULT_BRAIN_BASE = "https://generativelanguage.googleapis.com/v1beta/openai"


def is_vision_configured() -> bool:
    return vision_config() is not None


def vision_config() -> Optional[Dict[str, str]]:
    url = os.environ.get("KIM_VISION_BASE_URL", "").strip()
    key = os.environ.get("KIM_VISION_API_KEY", "").strip()
    model = os.environ.get("KIM_VISION_MODEL", "").strip()
    if url and key:
        endpoint = url.rstrip("/")
        if not endpoint.endswith("/chat/completions"):
            endpoint += "/chat/completions"
        return {"endpoint": endpoint, "model": model or DEFAULT_IMAGE_MODEL, "headers": {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}}
    brain_key = (os.environ.get("KIM_LLM_API_KEY") or os.environ.get("GEMINI_API_KEY") or os.environ.get("OPENAI_API_KEY", "")).strip()
    if brain_key:
        base = os.environ.get("KIM_LLM_BASE_URL", DEFAULT_BRAIN_BASE).rstrip("/")
        endpoint = base if base.endswith("/chat/completions") else base + "/chat/completions"
        return {"endpoint": endpoint, "model": os.environ.get("KIM_LLM_MODEL", DEFAULT_IMAGE_MODEL), "headers": {"Authorization": f"Bearer {brain_key}", "Content-Type": "application/json"}}
    return None


_default_prompt = (
    "Describe what is visible on this computer screen in detail: the open app or window, "
    "key buttons, text fields, menu items, and the approximate x,y coordinates of clickable "
    "elements so an agent can click them next."
)


async def describe_image(image_bytes: bytes, prompt: str = _default_prompt) -> str:
    cfg = vision_config()
    if not cfg:
        return "vision is not configured (set KIM_VISION_API_KEY or KIM_LLM_API_KEY)"
    b64 = base64.b64encode(image_bytes).decode("ascii")
    body = {
        "model": cfg["model"],
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
        ]}],
        "max_tokens": 1200,
        "temperature": 0.2,
    }
    async with httpx.AsyncClient(timeout=60) as client:
        try:
            response = await client.post(cfg["endpoint"], headers=cfg["headers"], json=body)
        except Exception as exc:  # noqa: BLE001
            return f"vision request failed: {str(exc)[:300]}"
        if response.status_code >= 400:
            return f"vision request failed ({response.status_code}): {response.text[:300]}"
        try:
            choice = (response.json().get("choices") or [{}])[0]
            content = str(choice.get("message", {}).get("content", "") or "").strip()
        except Exception:  # noqa: BLE001
            return "vision returned an unreadable response"
        return content[:3000] or "vision returned no description"