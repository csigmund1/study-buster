"""Stable genanki Model definitions for study-buster exports.

Model IDs are fixed 10-digit constants (genanki convention). They must never
change once decks have been exported with them, or existing user collections
will treat re-imports as a different note type.
"""

import genanki

BASIC_MODEL_ID = 1_607_392_319
CLOZE_MODEL_ID = 1_607_392_320
IMAGE_OCCLUSION_MODEL_ID = 1_607_392_321

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

# Mirrors Anki's stock Image Occlusion notetype (Anki 23.10+): a CLOZE-type note
# with fields (Occlusions, Image, Header, Back Extra, Comments) whose Occlusions
# field holds `{{cN::image-occlusion:rect:left=..:top=..:width=..:height=..}}`
# markers in image-normalized (0-1) coordinates. Templates + CSS are copied
# verbatim from `spikes/native_io_apkg.py` (itself mirrored from Anki source),
# with a fresh model ID scoped to study-buster.
_IMAGE_OCCLUSION_FRONT = """\
{{#Header}}<div>{{Header}}</div>{{/Header}}
<div style="display: none">{{cloze:Occlusions}}</div>
<div id="err"></div>
<div id="image-occlusion-container">
    {{Image}}
    <canvas id="image-occlusion-canvas"></canvas>
</div>
<script>
try {
    anki.imageOcclusion.setup();
} catch (exc) {
    document.getElementById("err").innerHTML = `Error loading image occlusion<br><br>${exc}`;
}
</script>
"""

_IMAGE_OCCLUSION_BACK = (
    _IMAGE_OCCLUSION_FRONT
    + """
<div><button id="toggle">Toggle masks</button></div>
{{#Back Extra}}<div>{{Back Extra}}</div>{{/Back Extra}}
"""
)

_IMAGE_OCCLUSION_CSS = """\
#image-occlusion-canvas {
    --inactive-shape-color: #ffeba2;
    --active-shape-color: #ff8e8e;
    --inactive-shape-border: 1px #212121;
    --active-shape-border: 1px #212121;
    --highlight-shape-color: #ff8e8e00;
    --highlight-shape-border: 1px #ff8e8e;
}

.card {
    font-family: arial;
    font-size: 20px;
    text-align: center;
    color: black;
    background-color: white;
}
"""

IMAGE_OCCLUSION_MODEL = genanki.Model(
    IMAGE_OCCLUSION_MODEL_ID,
    "study-buster Image Occlusion",
    fields=[
        {"name": "Occlusions"},
        {"name": "Image"},
        {"name": "Header"},
        {"name": "Back Extra"},
        {"name": "Comments"},
    ],
    templates=[{"name": "Card 1", "qfmt": _IMAGE_OCCLUSION_FRONT, "afmt": _IMAGE_OCCLUSION_BACK}],
    css=_IMAGE_OCCLUSION_CSS,
    model_type=genanki.Model.CLOZE,
)
