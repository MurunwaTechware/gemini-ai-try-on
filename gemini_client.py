"""Thin async wrapper around Gemini: body measurements and try-on image editing. No FastAPI, no UI in here,
so it can be lifted into a queue worker as-is."""
import asyncio
from dataclasses import dataclass
from typing import Literal

from google import genai
from google.genai import types
from pydantic import BaseModel, Field

import config
import prompts

if config.API_KEY:
    _client = genai.Client(api_key=config.API_KEY)
else:   # Vertex AI; credentials come from ADC, not from anything in this repo
    _client = genai.Client(vertexai=True, project=config.PROJECT_ID, location=config.LOCATION)


# The SDK turns automatic function calling (AFC) on by default and logs an advisory on every
# run. No tools are ever passed here, so switch it off; each call is then a single plain request.
_NO_AFC = types.AutomaticFunctionCallingConfig(disable=True)

# Finish/block reason names that mean the model refused, rather than failed
_SAFETY_REASONS = {"SAFETY", "IMAGE_SAFETY", "PROHIBITED_CONTENT", "IMAGE_PROHIBITED_CONTENT",
                   "BLOCKLIST", "SPII", "MODEL_ARMOR", "JAILBREAK"}
_SAFETY_MESSAGE = ("The model blocked this image for safety reasons. Check that each photo is in "
                   "the right slot (for example, sunglasses under Eyewear, not Top) and try again.")


@dataclass
class TryOnResult:
    image: bytes | None = None
    mime_type: str | None = None
    model_text: str | None = None   # Gemini often explains itself when it skips the image
    error: str | None = None


@dataclass
class InputImage:
    data: bytes
    mime_type: str


def _build_contents(person: InputImage, garments: dict[str, InputImage], prompt: str) -> list:
    contents = [prompts.PERSON_LABEL, types.Part.from_bytes(data=person.data, mime_type=person.mime_type)]
    for slot in config.GARMENT_SLOTS:          # fixed order: top, then bottom
        if slot in garments:
            g = garments[slot]
            contents += [prompts.GARMENT_LABELS[slot], types.Part.from_bytes(data=g.data, mime_type=g.mime_type)]
    contents.append(prompt)
    return contents


async def _generate_one(contents: list) -> TryOnResult:
    try:
        response = await _client.aio.models.generate_content(
            model=config.MODEL,
            contents=contents,
            config=types.GenerateContentConfig(response_modalities=["TEXT", "IMAGE"],
                                               automatic_function_calling=_NO_AFC),
        )
    except Exception as e:   # one failed slot shouldn't sink the others
        return TryOnResult(error=str(e))

    result = TryOnResult()
    texts = []
    candidate = response.candidates[0] if response.candidates else None
    for part in (candidate.content.parts if candidate and candidate.content else []) or []:
        if part.inline_data and part.inline_data.data and result.image is None:
            result.image = part.inline_data.data
            result.mime_type = part.inline_data.mime_type or "image/png"
        elif part.text:
            texts.append(part.text.strip())

    result.model_text = " ".join(texts) or None
    if result.image is None:
        # A blocked prompt comes back with no candidates and the reason in prompt_feedback
        feedback = response.prompt_feedback
        reason = (candidate.finish_reason if candidate else None) or (feedback.block_reason if feedback else None)
        reason_name = getattr(reason, "name", None)
        if reason_name in _SAFETY_REASONS:
            result.error = _SAFETY_MESSAGE
        else:
            result.error = result.model_text or f"No image returned (finish reason: {reason_name or 'none'})."
    return result


# ── Measurements ──────────────────────────────────────────────────────────────
# Passed to Gemini as the response schema, so the reply is always this shape.
# Field descriptions are part of what the model sees.

class BodyShape(BaseModel):
    build: Literal["Slim", "Lean", "Average", "Athletic", "Very Muscular"]
    torso_percent: int = Field(description="Torso share of torso + legs length, as a whole percentage")


class Measurements(BaseModel):
    neck_circ: float = Field(description="Neck circumference, cm")
    shoulder_breadth: float = Field(description="Shoulder breadth, acromion to acromion, cm")
    bust_chest_circ: float = Field(description="Bust or chest circumference at the fullest point, cm")
    waist_circ: float = Field(description="Natural waist circumference, cm")
    hip_circ: float = Field(description="Hip circumference at the widest point, cm")
    thigh_circ: float = Field(description="Upper thigh circumference, cm")
    calf_circ: float = Field(description="Calf circumference at the widest point, cm")
    bicep_circ: float = Field(description="Upper arm circumference, relaxed, cm")
    forearm_circ: float = Field(description="Forearm circumference at the widest point, cm")
    wrist_circ: float = Field(description="Wrist circumference, cm")
    ankle_circ: float = Field(description="Ankle circumference, cm")
    upper_arm_length: float = Field(description="Shoulder to elbow, cm")
    lower_arm_length: float = Field(description="Elbow to wrist, cm")
    upper_leg_length: float = Field(description="Hip joint to knee, cm")
    lower_leg_length: float = Field(description="Knee to ankle, cm")
    nape_to_waist: float = Field(description="Back of neck to natural waist, cm")


class MeasurementReport(BaseModel):
    body_shape: BodyShape
    measurements: Measurements
    warnings: list[str] = Field(description="Short notes on anything that made an estimate less reliable")


class MeasurementError(Exception):
    """Carries a message that is safe to show on the page."""


async def estimate_measurements(photos: dict[str, InputImage], height_cm: float, weight_kg: float,
                                gender: str, age: int) -> MeasurementReport:
    """photos: {"front": ..., "left": ..., "right": ...}"""
    contents = []
    for view in prompts.MEASURE_VIEWS:
        p = photos[view]
        contents += [prompts.MEASURE_VIEW_LABELS[view], types.Part.from_bytes(data=p.data, mime_type=p.mime_type)]
    contents.append(prompts.build_measure_prompt(height_cm, weight_kg, gender, age))

    try:
        response = await _client.aio.models.generate_content(
            model=config.MEASURE_MODEL,
            contents=contents,
            config=types.GenerateContentConfig(response_mime_type="application/json",
                                               response_schema=MeasurementReport,
                                               automatic_function_calling=_NO_AFC),
        )
    except Exception as e:
        raise MeasurementError(f"The measurement request failed: {e}") from e

    if isinstance(response.parsed, MeasurementReport):
        return response.parsed
    candidate = response.candidates[0] if response.candidates else None
    feedback = response.prompt_feedback
    reason = (candidate.finish_reason if candidate else None) or (feedback.block_reason if feedback else None)
    if getattr(reason, "name", None) in _SAFETY_REASONS:
        raise MeasurementError("The model blocked these photos for safety reasons. Try different photos.")
    raise MeasurementError("The model didn't return usable measurements. Please try again.")


async def run_tryon(person: InputImage, garments: dict[str, InputImage],
                    prompt: str, count: int = 1) -> list[TryOnResult]:
    """garments: {"top": ..., "bottom": ...}, either key optional, at least one present."""
    contents = _build_contents(person, garments, prompt)
    # Variations come from the model's own sampling; calls run in parallel
    return await asyncio.gather(*[_generate_one(contents) for _ in range(count)])
