"""
app.py

Flask backend for a reusable, domain-specific AI chatbot.

Routes:
    GET  /            -> serves templates/index.html
    POST /api/chat     -> processes a chat message, returns {"reply": "..."}

Conversation memory: the client (browser) keeps its own conversation history
in memory and sends the relevant recent turns with every request. This keeps
each user's conversation independent (no shared global history) without
needing server-side session storage.
"""

import os
import traceback

from flask import Flask, request, jsonify, render_template
from dotenv import load_dotenv
from google import genai
from google.genai import types

import chatbot_config
import firebase_config

load_dotenv()

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
# Latest generally available Gemini model. Override via env var if a newer
# generally-available model is released, without needing a code change.
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.6-flash")

MAX_MESSAGE_LENGTH = 4000
MAX_HISTORY_TURNS = 20  # number of past messages (user+assistant) kept

app = Flask(__name__)

_genai_client = None
if GEMINI_API_KEY:
    _genai_client = genai.Client(api_key=GEMINI_API_KEY)
else:
    print(
        "[app] WARNING: GEMINI_API_KEY is not set. /api/chat will return an "
        "error until it is configured."
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _validate_chat_request(payload):
    """Validates the incoming request body. Returns (message, history, error)."""
    if not isinstance(payload, dict):
        return None, None, "Invalid request body."

    message = payload.get("message")
    if not isinstance(message, str) or not message.strip():
        return None, None, "Field 'message' is required and must be a non-empty string."
    if len(message) > MAX_MESSAGE_LENGTH:
        return None, None, f"Field 'message' exceeds max length of {MAX_MESSAGE_LENGTH}."

    history = payload.get("history", [])
    if history is None:
        history = []
    if not isinstance(history, list):
        return None, None, "Field 'history' must be a list."

    cleaned_history = []
    for turn in history[-MAX_HISTORY_TURNS:]:
        if not isinstance(turn, dict):
            continue
        role = turn.get("role")
        content = turn.get("content")
        if role not in ("user", "assistant"):
            continue
        if not isinstance(content, str) or not content.strip():
            continue
        cleaned_history.append({"role": role, "content": content[:MAX_MESSAGE_LENGTH]})

    return message.strip(), cleaned_history, None


def _build_contents(message, history):
    """Builds the Gemini `contents` list from history + the new user message."""
    contents = []
    for turn in history:
        gemini_role = "user" if turn["role"] == "user" else "model"
        contents.append(
            types.Content(role=gemini_role, parts=[types.Part(text=turn["content"])])
        )
    contents.append(types.Content(role="user", parts=[types.Part(text=message)]))
    return contents


def _build_system_instruction():
    """Combines the fixed chatbot system prompt with live Firestore knowledge."""
    knowledge_text = firebase_config.get_knowledge_as_text()
    if knowledge_text:
        return (
            chatbot_config.SYSTEM_PROMPT
            + "\n\n--------------------------------------------------------------------------\n"
            "KNOWLEDGE BASE DATA (from Firestore, JSON format, may use abbreviated "
            "keys - interpret from context, and never expose raw field names to "
            "the user):\n"
            + knowledge_text
        )
    return (
        chatbot_config.SYSTEM_PROMPT
        + "\n\n--------------------------------------------------------------------------\n"
        "KNOWLEDGE BASE DATA: (none available right now - the developer has not "
        "yet added Firestore data, or it could not be reached. Answer using your "
        "own general knowledge within the domain, and be honest that you don't "
        "have specific records to reference.)"
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.route("/", methods=["GET"])
def index():
    return render_template("index.html", chatbot_title=chatbot_config.CHATBOT_TITLE)


@app.route("/api/chat", methods=["POST"])
def chat():
    if _genai_client is None:
        return jsonify({"error": "Server is not configured. Missing GEMINI_API_KEY."}), 503

    payload = request.get_json(silent=True)
    message, history, error = _validate_chat_request(payload)
    if error:
        return jsonify({"error": error}), 400

    try:
        system_instruction = _build_system_instruction()
        contents = _build_contents(message, history)

        response = _genai_client.models.generate_content(
            model=GEMINI_MODEL,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                temperature=0.6,
            ),
        )

        reply_text = (response.text or "").strip()
        if not reply_text:
            reply_text = (
                "Sorry, I couldn't come up with a response for that. "
                "Could you rephrase your question?"
            )

        return jsonify({"reply": reply_text})

    except Exception:
        # Never leak stack traces or secrets to the client.
        traceback.print_exc()
        return jsonify({"error": "Something went wrong while generating a response."}), 500


@app.errorhandler(404)
def not_found(_e):
    return jsonify({"error": "Not found."}), 404


@app.errorhandler(500)
def server_error(_e):
    return jsonify({"error": "Internal server error."}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    app.run(host="0.0.0.0", port=port, debug=debug)
