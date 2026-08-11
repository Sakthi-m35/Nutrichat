"""
firebase_config.py

Initializes Firebase Admin SDK using a local service-account key file
(firebase-key.json) and provides a helper to dynamically pull the chatbot's
domain knowledge base out of Firestore.

Nothing here is domain-specific: collection/document/field names are never
hardcoded. The developer manages the actual Firestore content directly in
the Firebase console (or via their own scripts); this module just reads
whatever is there at request time.
"""

import os
import json
import threading

import firebase_admin
from firebase_admin import credentials, firestore

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

FIREBASE_KEY_PATH = os.environ.get(
    "FIREBASE_KEY_PATH", os.path.join(os.path.dirname(__file__), "firebase-key.json")
)

# Safety caps so a large database can't blow up the LLM context window.
MAX_COLLECTIONS = int(os.environ.get("FIREBASE_MAX_COLLECTIONS", 25))
MAX_DOCS_PER_COLLECTION = int(os.environ.get("FIREBASE_MAX_DOCS_PER_COLLECTION", 200))

_init_lock = threading.Lock()
_db = None
_firebase_available = False


def _looks_like_placeholder(path: str) -> bool:
    """Detects whether firebase-key.json is still the unfilled template."""
    try:
        with open(path, "r") as f:
            data = json.load(f)
        return data.get("type") == "PLACEHOLDER"
    except Exception:
        return False


def init_firebase():
    """Initializes the Firebase Admin app exactly once. Safe to call repeatedly."""
    global _db, _firebase_available

    with _init_lock:
        if firebase_admin._apps:
            _db = firestore.client()
            _firebase_available = True
            return

        if not os.path.exists(FIREBASE_KEY_PATH):
            print(
                f"[firebase_config] WARNING: {FIREBASE_KEY_PATH} not found. "
                "Firestore knowledge base will be unavailable until it is added."
            )
            _firebase_available = False
            return

        if _looks_like_placeholder(FIREBASE_KEY_PATH):
            print(
                "[firebase_config] WARNING: firebase-key.json is still the "
                "placeholder template. Replace it with your real Firebase "
                "service-account JSON to enable the knowledge base."
            )
            _firebase_available = False
            return

        try:
            cred = credentials.Certificate(FIREBASE_KEY_PATH)
            firebase_admin.initialize_app(cred)
            _db = firestore.client()
            _firebase_available = True
            print("[firebase_config] Firebase initialized successfully.")
        except Exception as e:
            print(f"[firebase_config] ERROR initializing Firebase: {e}")
            _firebase_available = False


def is_available() -> bool:
    return _firebase_available


def get_all_knowledge() -> dict:
    """
    Dynamically retrieves the chatbot's entire domain knowledge base from
    Firestore: every root collection and every document within it, as plain
    JSON-serializable data. Collection and field names are read as-is
    (whatever the developer used, including abbreviations) - the LLM is
    responsible for interpreting them.

    Returns an empty dict if Firebase isn't configured/available yet, so the
    app can still run (using Gemini's general knowledge) before the
    developer has added their data.
    """
    if not _firebase_available or _db is None:
        return {}

    knowledge = {}
    try:
        collections = list(_db.collections())
        for collection_ref in collections[:MAX_COLLECTIONS]:
            collection_name = collection_ref.id
            docs = {}
            for doc in collection_ref.limit(MAX_DOCS_PER_COLLECTION).stream():
                docs[doc.id] = doc.to_dict()
            knowledge[collection_name] = docs
    except Exception as e:
        print(f"[firebase_config] ERROR reading Firestore data: {e}")
        return {}

    return knowledge


def get_knowledge_as_text() -> str:
    """Returns the knowledge base as a compact JSON string for the LLM prompt."""
    knowledge = get_all_knowledge()
    if not knowledge:
        return ""
    try:
        return json.dumps(knowledge, default=str, ensure_ascii=False)
    except Exception as e:
        print(f"[firebase_config] ERROR serializing knowledge: {e}")
        return ""


# Initialize on import so app.py can use it immediately.
init_firebase()
