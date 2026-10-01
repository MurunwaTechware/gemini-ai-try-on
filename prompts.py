"""Prompt text lives here, not in the client or the UI."""

# ── Measurements ──────────────────────────────────────────────────────────────
MEASURE_VIEWS = ("front", "left", "right")   # order the photos are sent in
MEASURE_VIEW_LABELS = {
    "front": "Front view photo:",
    "left": "Left side view photo:",
    "right": "Right side view photo:",
}


def build_measure_prompt(height_cm: float, weight_kg: float, gender: str, age: int) -> str:
    return (
        "The three photos above show the same person from the front, left side, and right side. "
        f"Their stated details are: height {height_cm:g} cm, weight {weight_kg:g} kg, "
        f"gender {gender}, age {age}. "
        "Estimate their body measurements in centimetres. Use the stated height as the scale "
        "reference for everything else, and use all three views together: the front view for "
        "widths, the side views for depth, and both to estimate circumferences. Circumferences "
        "are the full distance around the body at that point. Where clothing, pose, or framing "
        "hides a region, still give your best estimate, and add a short warning saying which "
        "measurement is affected and why. Also add a warning if the photos look like different "
        "people or the stated details look inconsistent with the photos."
    )


# ── Try-on ────────────────────────────────────────────────────────────────────

# The full prompt is assembled per request from the slots that were actually uploaded,
# so the model never has to work out which instructions apply ("only if provided").
# DEFAULT_RULES is the part shown in the UI: shared rules that hold on every run.
DEFAULT_RULES = (
    "Preserve all facial features, facial expression, hairstyle, skin tone, body proportions, pose, and "
    "background exactly. Reproduce each item's colour, print, graphics, shape, and cut "
    "faithfully. The item photos are references only: take nothing from them except "
    "the specified item, and never let the people wearing them influence the result. "
    "The only person in the output is the one from the person photo. Change nothing else "
    "in the image."
)

_TASK = "Generate a high-quality virtual try-on image of the person from the person photo."
SLOT_INSTRUCTIONS = {
    "top": "Replace the person's top with the top garment.",
    "bottom": "Replace the person's bottoms with the bottom garment.",
    # Headwear is the one item that has to change the hair, so it says so itself
    # rather than the shared rules carrying an exception for it
    "headwear": (
        "Put the headwear on the person's head, replacing any they already wear. Hair may "
        "change only where the headwear covers or presses it; everywhere else it stays exactly the same."
    ),
    "eyewear": (
        "Put the eyewear on the person's face, replacing any they already wear, resting "
        "naturally on the nose and ears. Keep the eyes, eyebrows, and face shape unchanged."
    ),
}
_KEEP_REST = "Keep all of the person's other clothing, shoes, and accessories unchanged."


def build_prompt(slots, rules: str) -> str:
    """slots: the item slots that were uploaded, in config.GARMENT_SLOTS order."""
    return " ".join([_TASK, *(SLOT_INSTRUCTIONS[s] for s in slots), _KEEP_REST, rules])


# Labels sent alongside each image so the model never has to guess which is which.
# The isolation rules live here rather than only in DEFAULT_RULES, because the rules
# are user-editable in the UI and these labels are always sent.
# Each label names what else to ignore, so no label ever tells the model to ignore its own item.
_IGNORE_WEARER = (
    "This photo may show someone wearing a full outfit. Ignore the wearer's face, hair, "
    "skin tone, body shape, and pose, and ignore everything else they are wearing"
)
PERSON_LABEL = "Person photo (the only person who should appear in the result):"
GARMENT_LABELS = {
    "top": (f"Top garment reference (shirt, t-shirt, hoodie, etc.). Use ONLY the top from it. "
            f"{_IGNORE_WEARER}, including bottoms, shoes, hats, glasses, jewellery, and bags:"),
    "bottom": (f"Bottom garment reference (pants, jeans, shorts, etc.). Use ONLY the bottom from it. "
               f"{_IGNORE_WEARER}, including tops, shoes, hats, glasses, jewellery, and bags:"),
    "headwear": (f"Headwear reference (cap, hat, beanie, etc.). Use ONLY the headwear from it. "
                 f"{_IGNORE_WEARER}, including clothing, glasses, jewellery, and bags:"),
    "eyewear": (f"Eyewear reference (glasses, sunglasses). Use ONLY the eyewear from it. "
                f"{_IGNORE_WEARER}, including clothing, hats, jewellery, and bags:"),
}
