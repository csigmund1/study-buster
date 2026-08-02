"""Stable genanki Model definitions for study-buster exports.

Model IDs are fixed 10-digit constants (genanki convention). They must never
change once decks have been exported with them, or existing user collections
will treat re-imports as a different note type.
"""

import genanki

BASIC_MODEL_ID = 1_607_392_319
CLOZE_MODEL_ID = 1_607_392_320

BASIC_MODEL = genanki.Model(
    BASIC_MODEL_ID,
    "study-buster Basic",
    fields=[
        {"name": "Front"},
        {"name": "Back"},
    ],
    templates=[
        {
            "name": "Card 1",
            "qfmt": "{{Front}}",
            "afmt": '{{FrontSide}}<hr id="answer">{{Back}}',
        }
    ],
)

CLOZE_MODEL = genanki.Model(
    CLOZE_MODEL_ID,
    "study-buster Cloze",
    fields=[
        {"name": "Text"},
        {"name": "Extra"},
    ],
    templates=[
        {
            "name": "Cloze",
            "qfmt": "{{cloze:Text}}",
            "afmt": "{{cloze:Text}}<br>{{Extra}}",
        }
    ],
    css=genanki.CLOZE_MODEL.css,
    model_type=genanki.Model.CLOZE,
)
