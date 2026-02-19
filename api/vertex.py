"""
Vertex AI Gemini entity extraction for document screening.
"""
import json
import os

import vertexai
from vertexai.generative_models import GenerationConfig, GenerativeModel

_PROJECT = os.environ.get("BQ_PROJECT", "")
_REGION = os.environ.get("VERTEX_REGION", "us-central1")
_MODEL = os.environ.get("VERTEX_MODEL", "gemini-2.0-flash-001")

_ENTITY_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "entity_type": {
                "type": "string",
                "enum": ["person", "organization", "vessel", "aircraft"],
            },
        },
        "required": ["name", "entity_type"],
    },
}

_PROMPT_TEMPLATE = """Extract all named entities from the following text.
Return only persons, organizations, vessels, and aircraft — not locations or dates.
For each entity provide its name exactly as written and classify it.

Text:
{text}"""


def extract_entities(text: str) -> list[dict]:
    """Extract named entities from *text* using Vertex AI Gemini.

    Returns a list of dicts: [{"name": str, "entity_type": str}, ...]
    where entity_type is one of: person, organization, vessel, aircraft.
    """
    vertexai.init(project=_PROJECT, location=_REGION)
    model = GenerativeModel(_MODEL)
    response = model.generate_content(
        _PROMPT_TEMPLATE.format(text=text),
        generation_config=GenerationConfig(
            response_mime_type="application/json",
            response_schema=_ENTITY_SCHEMA,
        ),
    )
    return json.loads(response.text)
