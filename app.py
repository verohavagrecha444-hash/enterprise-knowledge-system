import os
import json
import uuid
import traceback
from datetime import datetime, timezone

from flask import Flask, render_template, request, jsonify
from flask_cors import CORS
from werkzeug.utils import secure_filename
from dotenv import load_dotenv

from rag_engine import (
    ingest_pdf,
    get_ai_response,
    get_index_stats,
    resolve_selected_document
)

from firebase_db import (
    save_document_metadata,
    get_all_documents,
    save_chat_message,
    get_chat_messages,
    create_chat_conversation,
    get_all_chat_conversations,
    get_chat_conversation,
    update_chat_conversation,
    delete_chat_conversation,
    db
)


# ============================================================
# CONFIGURATION
# ============================================================

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads")

METADATA_PATH = os.path.join(
    UPLOAD_FOLDER,
    "documents.json"
)

CHAT_PATH = os.path.join(
    UPLOAD_FOLDER,
    "chat_history.json"
)

ALLOWED_EXTENSIONS = {"pdf"}

MAX_UPLOAD_SIZE = 50 * 1024 * 1024

# Existing conversation ID.
# Keeping this ID allows the application to recover old messages.
LEGACY_CHAT_ID = "local-development"

os.makedirs(UPLOAD_FOLDER, exist_ok=True)


# ============================================================
# FLASK APPLICATION
# ============================================================

app = Flask(__name__)

app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_SIZE

CORS(app)

# Cache only for the currently processed conversation.
# Firestore remains the primary source of saved messages.
chat_history = []


# ============================================================
# GENERAL HELPERS
# ============================================================

def current_timestamp():
    """Return the current UTC timestamp as an ISO string."""
    return datetime.now(timezone.utc).isoformat()


def read_json(path, default=None):
    """Read a JSON file safely."""

    if default is None:
        default = []

    try:
        with open(path, "r", encoding="utf-8") as file:
            return json.load(file)

    except (
        FileNotFoundError,
        json.JSONDecodeError,
        OSError
    ):
        return default


def write_json(path, data):
    """Write JSON safely using a temporary file."""

    temporary_path = path + ".tmp"

    with open(
        temporary_path,
        "w",
        encoding="utf-8"
    ) as file:
        json.dump(
            data,
            file,
            indent=2,
            ensure_ascii=False
        )

    os.replace(temporary_path, path)


def normalize_timestamp(value):
    """Convert Firestore or Python timestamps to JSON-safe strings."""

    if value is None:
        return ""

    if isinstance(value, datetime):
        return value.isoformat()

    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            pass

    return str(value)


def normalize_message(message):
    """Return a message in a consistent JSON-safe format."""

    if not isinstance(message, dict):
        return None

    role = message.get("role", "")
    content = message.get("content", "")

    role_aliases = {
        "human": "user",
        "ai": "assistant",
        "bot": "assistant"
    }

    role = role_aliases.get(role, role)

    if role not in ("user", "assistant"):
        return None

    if content is None:
        content = ""

    content = str(content)

    if not content.strip():
        return None

    return {
        "id": str(message.get("id") or uuid.uuid4()),
        "role": role,
        "content": content,
        "selected_document": message.get("selected_document"),
        "timestamp": normalize_timestamp(
            message.get("timestamp")
        )
    }


def get_requested_chat_id(payload=None):
    """
    Get the conversation ID supplied by the frontend.

    If the frontend does not send an ID, use the legacy
    conversation for backward compatibility.
    """

    if payload is None:
        payload = request.args

    chat_id = payload.get("chat_id")

    if chat_id is None:
        return LEGACY_CHAT_ID

    chat_id = str(chat_id).strip()

    if not chat_id:
        return LEGACY_CHAT_ID

    # Prevent invalid or unexpectedly long IDs.
    if len(chat_id) > 150:
        raise ValueError("Invalid conversation ID.")

    return chat_id


# ============================================================
# DOCUMENT HELPERS
# ============================================================

def allowed_file(filename):
    """Check whether the uploaded file is a PDF."""

    return (
        bool(filename)
        and "." in filename
        and filename.rsplit(".", 1)[1].lower()
        in ALLOWED_EXTENSIONS
    )


def local_documents():
    """
    Retrieve locally saved document metadata.

    Return only documents whose PDF files still exist.
    """

    documents = read_json(METADATA_PATH, [])

    valid_documents = []

    for document in documents:

        if not isinstance(document, dict):
            continue

        stored_name = document.get("stored_name")

        if not stored_name:
            continue

        stored_name = os.path.basename(str(stored_name))

        file_path = os.path.join(
            UPLOAD_FOLDER,
            stored_name
        )

        if os.path.isfile(file_path):

            document["stored_name"] = stored_name

            valid_documents.append(document)

    return valid_documents


def firestore_documents():
    """
    Retrieve document metadata from Firestore.

    Return only documents whose local PDF files exist.
    """

    documents = get_all_documents()

    valid_documents = []

    for document in documents:

        if not isinstance(document, dict):
            continue

        stored_name = document.get("stored_name")

        if not stored_name:
            continue

        stored_name = os.path.basename(str(stored_name))

        file_path = os.path.join(
            UPLOAD_FOLDER,
            stored_name
        )

        if os.path.isfile(file_path):

            document["stored_name"] = stored_name

            valid_documents.append(document)

    return valid_documents


def list_documents():
    """
    Use Firestore as the primary metadata source.

    Fall back to local JSON if Firestore fails or has no
    matching local documents.
    """

    try:
        documents = firestore_documents()

        if documents:
            return documents

    except Exception:
        app.logger.exception(
            "Could not retrieve documents from Firestore."
        )

    return local_documents()


def save_local_document_metadata(record):
    """Save document metadata in the local JSON backup."""

    documents = read_json(METADATA_PATH, [])

    stored_name = record.get("stored_name")

    documents = [
        document
        for document in documents
        if document.get("stored_name") != stored_name
    ]

    documents.append(record)

    write_json(METADATA_PATH, documents)


def get_valid_selected_document(selected_document):
    """
    Validate a selected PDF against available documents.

    Returns:
        A valid stored filename, or None.
    """

    if not selected_document:
        return None

    selected_name = os.path.basename(
        str(selected_document)
    )

    available_names = {
        document.get("stored_name")
        for document in list_documents()
    }

    if selected_name not in available_names:
        raise ValueError(
            "The selected PDF was not found. "
            "Refresh the document list and try again."
        )

    return selected_name


# ============================================================
# CONVERSATION HELPERS
# ============================================================

def make_conversation_title(first_message):
    """Create a readable title from the first user question."""

    title = " ".join(
        str(first_message).strip().split()
    )

    if not title:
        return "New Chat"

    max_length = 45

    if len(title) > max_length:
        title = title[:max_length - 3].rstrip() + "..."

    return title


def ensure_conversation(chat_id):
    """
    Ensure a conversation metadata document exists.

    Existing legacy conversations are retained rather than
    replaced with a new conversation.
    """

    conversation = get_chat_conversation(chat_id)

    if conversation:
        return conversation

    if chat_id == LEGACY_CHAT_ID:
        title = "Previous Conversation"
    else:
        title = "New Chat"

    return create_chat_conversation(
        chat_id,
        title
    )


def load_conversation_messages(chat_id):
    """
    Load every saved message for a conversation.

    Firestore is the primary source. For the legacy conversation,
    fall back to the previous local JSON backup if needed.
    """

    try:
        messages = get_chat_messages(chat_id)

        normalized = []

        for message in messages:
            normalized_message = normalize_message(message)

            if normalized_message:
                normalized.append(normalized_message)

        # Older messages may already exist in the legacy
        # conversation's Firestore messages subcollection.
        if normalized:
            return normalized

    except Exception:
        app.logger.exception(
            "Could not load messages for conversation %s.",
            chat_id
        )

    # The old JSON backup did not store separate conversations.
    # Therefore, only associate it with the legacy conversation.
    if chat_id == LEGACY_CHAT_ID:

        local_messages = read_json(CHAT_PATH, [])

        if isinstance(local_messages, list):

            normalized = []

            for message in local_messages:
                normalized_message = normalize_message(message)

                if normalized_message:
                    normalized.append(normalized_message)

            return normalized

    return []


def load_recent_chat_history(chat_id, limit=8):
    """
    Load the most recent messages for RAG conversational context.

    The complete history remains stored in Firestore.
    Only a limited number of recent messages are sent to the
    language model.
    """

    messages = load_conversation_messages(chat_id)

    recent_messages = messages[-limit:]

    return [
        {
            "role": message["role"],
            "content": message["content"]
        }
        for message in recent_messages
    ]


def save_conversation_message(
    chat_id,
    role,
    content,
    selected_document=None
):
    """
    Save one message to Firestore.

    Also update the conversation title when the first user
    message is saved.
    """

    ensure_conversation(chat_id)

    message_id = str(uuid.uuid4())

    message = {
        "role": role,
        "content": str(content),
        "selected_document": selected_document,
        "timestamp": current_timestamp()
    }

    # Persist the message in Firestore.
    save_chat_message(
        chat_id,
        message_id,
        message
    )

    # Give a new conversation a title based on its first question.
    if role == "user":

        conversation = get_chat_conversation(chat_id) or {}

        existing_title = conversation.get(
            "title",
            "New Chat"
        )

        existing_messages = get_chat_messages(chat_id)

        # A conversation with exactly one user message has
        # just received its first question.
        if (
            existing_title == "New Chat"
            and len(existing_messages) <= 1
        ):
            update_chat_conversation(
                chat_id,
                title=make_conversation_title(content)
            )

    # Keep an in-memory cache for the current conversation.
    chat_history.append({
        "chat_id": chat_id,
        "role": role,
        "content": str(content)
    })

    # The cache is not the permanent source of truth.
    del chat_history[:-100]

    # Maintain the existing local JSON backup for the legacy chat.
    if chat_id == LEGACY_CHAT_ID:

        try:
            local_history = read_json(CHAT_PATH, [])

            if not isinstance(local_history, list):
                local_history = []

            local_history.append({
                "role": role,
                "content": str(content),
                "selected_document": selected_document,
                "timestamp": message["timestamp"]
            })

            # Preserve a reasonable local backup size.
            local_history = local_history[-500:]

            write_json(CHAT_PATH, local_history)

        except Exception:
            app.logger.exception(
                "Could not save the local legacy chat backup."
            )

    return {
        "id": message_id,
        **message
    }


# ============================================================
# CONVERSATION API: LIST CHATS
# ============================================================

@app.route("/api/chats", methods=["GET"])
def api_list_chats():
    """
    Return saved conversations for the sidebar.

    The response includes conversation IDs, titles, and
    timestamps, newest conversations first.
    """

    try:

        # Load saved conversations without recreating the legacy chat.
        # Otherwise, deleting the legacy conversation would cause an
        # empty "Previous Conversation" entry to reappear on refresh.
        conversations = get_all_chat_conversations()

        results = []

        for conversation in conversations:

            if not isinstance(conversation, dict):
                continue

            chat_id = conversation.get("id")

            if not chat_id:
                continue

            results.append({
                "id": str(chat_id),
                "title": conversation.get("title") or "New Chat",
                "created_at": normalize_timestamp(
                    conversation.get("created_at")
                ),
                "updated_at": normalize_timestamp(
                    conversation.get("updated_at")
                )
            })

        # Include the legacy conversation if Firestore cannot
        # return it in the ordered query.
        if not any(
            item["id"] == LEGACY_CHAT_ID
            for item in results
        ):

            legacy_messages = load_conversation_messages(
                LEGACY_CHAT_ID
            )

            if legacy_messages:
                results.append({
                    "id": LEGACY_CHAT_ID,
                    "title": "Previous Conversation",
                    "created_at": "",
                    "updated_at": legacy_messages[-1].get(
                        "timestamp",
                        ""
                    )
                })

        results.sort(
            key=lambda item: item.get("updated_at", ""),
            reverse=True
        )

        return jsonify({
            "conversations": results
        }), 200

    except Exception as error:

        app.logger.exception(
            "Could not retrieve conversation history."
        )

        return jsonify({
            "error": "Could not load chat history.",
            "details": str(error)
        }), 500


# ============================================================
# CONVERSATION API: CREATE NEW CHAT
# ============================================================

@app.route("/api/new-chat", methods=["POST"])
def api_new_chat():
    """
    Create a new independent conversation.

    Previous conversations are never deleted by this endpoint.
    """

    try:

        chat_id = str(uuid.uuid4())

        conversation = create_chat_conversation(
            chat_id,
            "New Chat"
        )

        return jsonify({
            "message": "New conversation created.",
            # chat_id is used by the updated frontend.
            "chat_id": chat_id,
            "id": chat_id,
            "conversation": {
                "id": chat_id,
                "title": conversation.get(
                    "title",
                    "New Chat"
                ),
                "created_at": "",
                "updated_at": ""
            }
        }), 201

    except Exception as error:

        app.logger.exception(
            "Could not create a new conversation."
        )

        return jsonify({
            "error": "Could not create a new chat.",
            "details": str(error)
        }), 500


# ============================================================
# CONVERSATION API: RESTORE A CHAT
# ============================================================

@app.route("/api/chats/<chat_id>", methods=["GET"])
def api_get_chat(chat_id):
    """Load all messages belonging to one conversation."""

    try:

        if not chat_id or len(chat_id) > 150:
            return jsonify({
                "error": "Invalid conversation ID."
            }), 400

        conversation = get_chat_conversation(chat_id)

        messages = load_conversation_messages(chat_id)

        if not conversation and not messages:
            return jsonify({
                "error": "Conversation not found."
            }), 404

        # Restore metadata for an existing legacy conversation
        # whose messages existed before conversation metadata
        # was introduced.
        if not conversation:
            conversation = ensure_conversation(chat_id)

        return jsonify({
            "conversation": {
                "id": chat_id,
                "title": conversation.get("title", "New Chat"),
                "created_at": normalize_timestamp(
                    conversation.get("created_at")
                ),
                "updated_at": normalize_timestamp(
                    conversation.get("updated_at")
                )
            },
            # Keep history for older frontend versions and expose
            # messages for the updated main.js.
            "history": messages,
            "messages": messages
        }), 200

    except Exception as error:

        app.logger.exception(
            "Could not restore conversation %s.",
            chat_id
        )

        return jsonify({
            "error": "Could not load this conversation.",
            "details": str(error)
        }), 500


# ============================================================
# CONVERSATION API: DELETE ONE CHAT
# ============================================================

@app.route("/api/chats/<chat_id>", methods=["DELETE"])
def api_delete_chat(chat_id):
    """
    Delete one conversation and its saved messages.

    Other conversations are not affected.
    """

    try:

        if not chat_id or len(chat_id) > 150:
            return jsonify({
                "error": "Invalid conversation ID."
            }), 400

        conversation = get_chat_conversation(chat_id)
        messages = load_conversation_messages(chat_id)

        if not conversation and not messages:
            return jsonify({
                "error": "Conversation not found."
            }), 404

        # Delete only the selected conversation.
        delete_chat_conversation(chat_id)

        # If the legacy conversation was deleted, remove its
        # local backup as well.
        if chat_id == LEGACY_CHAT_ID:

            try:
                write_json(CHAT_PATH, [])
            except Exception:
                app.logger.exception(
                    "Could not clear the legacy local backup."
                )

        return jsonify({
            "message": "Conversation deleted.",
            "chat_id": chat_id
        }), 200

    except Exception as error:

        app.logger.exception(
            "Could not delete conversation %s.",
            chat_id
        )

        return jsonify({
            "error": "Could not delete this conversation.",
            "details": str(error)
        }), 500


# ============================================================
# HOME PAGE
# ============================================================

@app.route("/")
def index():
    """Render the existing dashboard."""

    return render_template("index.html")


# ============================================================
# UPLOAD PDF
# ============================================================

@app.route("/upload", methods=["POST"])
def upload_pdf():
    """
    Upload a PDF, index its text in FAISS, and save metadata.
    """

    if "file" not in request.files:

        return jsonify({
            "error": (
                "No file was attached. "
                "Please choose a PDF."
            )
        }), 400

    uploaded_file = request.files["file"]

    if not uploaded_file or not uploaded_file.filename:

        return jsonify({
            "error": "Please select a PDF first."
        }), 400

    if not allowed_file(uploaded_file.filename):

        return jsonify({
            "error": "Only PDF files are supported."
        }), 400

    safe_name = secure_filename(uploaded_file.filename)

    if not safe_name:

        return jsonify({
            "error": (
                "The filename is invalid. "
                "Rename the PDF and try again."
            )
        }), 400

    # Avoid overwriting a previously uploaded file.
    stored_name = safe_name

    name_without_extension, extension = os.path.splitext(
        safe_name
    )

    counter = 1

    while os.path.exists(
        os.path.join(UPLOAD_FOLDER, stored_name)
    ):

        stored_name = (
            f"{name_without_extension}_{counter}"
            f"{extension}"
        )

        counter += 1

    file_path = os.path.join(
        UPLOAD_FOLDER,
        stored_name
    )

    try:

        # Save the uploaded PDF.
        uploaded_file.save(file_path)

        if os.path.getsize(file_path) == 0:

            os.remove(file_path)

            return jsonify({
                "error": "The selected PDF is empty."
            }), 400

        # Extract text and index it in FAISS.
        chunk_count = ingest_pdf(stored_name)

        record = {
            "name": safe_name,
            "stored_name": stored_name,
            "size_bytes": os.path.getsize(file_path),
            "uploaded_at": current_timestamp(),
            "chunks": int(chunk_count or 0),
            "status": "Processed"
        }

        # Save a local backup first.
        save_local_document_metadata(record)

        # Save metadata to Firebase.
        try:

            save_document_metadata(
                stored_name,
                record
            )

        except Exception:

            app.logger.exception(
                "Could not save document metadata to Firebase."
            )

            return jsonify({
                "message": (
                    f"{safe_name} was uploaded and indexed locally, "
                    "but Firebase metadata could not be saved."
                ),
                "warning": (
                    "Check Firebase connectivity and Firestore "
                    "permissions. Local metadata was preserved."
                ),
                "document": record,
                "chunks": record["chunks"]
            }), 200

        return jsonify({
            "message": (
                f"{safe_name} uploaded and indexed successfully."
            ),
            "document": record,
            "chunks": record["chunks"]
        }), 200

    except Exception as error:

        app.logger.exception(
            "PDF upload or indexing failed."
        )

        return jsonify({
            "error": (
                "The PDF could not be processed. "
                "Check the VS Code terminal for details."
            ),
            "details": str(error)
        }), 500


# ============================================================
# GET DOCUMENTS
# ============================================================

@app.route("/api/documents", methods=["GET"])
def api_documents():
    """Return the uploaded PDF list."""

    try:

        documents = list_documents()

        return jsonify({
            "documents": documents
        }), 200

    except Exception as error:

        app.logger.exception(
            "Could not retrieve documents."
        )

        return jsonify({
            "error": "Could not load the document list.",
            "details": str(error)
        }), 500


# ============================================================
# APPLICATION STATISTICS
# ============================================================

@app.route("/api/stats", methods=["GET"])
def api_stats():
    """Return document, chunk, and question statistics."""

    try:

        documents = list_documents()

        total_chunks = sum(
            int(document.get("chunks", 0))
            for document in documents
        )

        try:

            # Count questions across all saved conversations.
            conversations = get_all_chat_conversations()

            question_count = 0

            for conversation in conversations:

                conversation_id = conversation.get("id")

                if not conversation_id:
                    continue

                messages = get_chat_messages(conversation_id)

                question_count += sum(
                    1
                    for message in messages
                    if message.get("role") in ("user", "human")
                )

        except Exception:

            app.logger.exception(
                "Could not retrieve Firebase chat statistics."
            )

            question_count = sum(
                1
                for message in chat_history
                if message.get("role") == "user"
            )

        return jsonify({
            "documents": len(documents),
            "chunks": total_chunks,
            "questions": question_count,
            "uploads": len(documents),
            "index": get_index_stats()
        }), 200

    except Exception as error:

        app.logger.exception(
            "Could not retrieve application statistics."
        )

        return jsonify({
            "error": "Could not load application statistics.",
            "details": str(error)
        }), 500


# ============================================================
# ASK THE AI
# ============================================================

@app.route("/ask", methods=["POST"])
def ask():
    """
    Process a user question within a specific conversation.

    The frontend should send:
        question
        selected_document
        chat_id
    """

    payload = request.get_json(silent=True) or {}

    question = str(
        payload.get("question")
        or payload.get("query")
        or ""
    ).strip()

    if not question:

        return jsonify({
            "error": "Please enter a question."
        }), 400

    try:

        chat_id = get_requested_chat_id(payload)

    except ValueError as error:

        return jsonify({
            "error": str(error)
        }), 400

    try:

        selected_document = get_valid_selected_document(
            payload.get("selected_document")
        )

    except ValueError as error:

        return jsonify({
            "error": str(error)
        }), 404

    try:

        # Ensure the selected conversation exists before saving.
        ensure_conversation(chat_id)

        # Load context only from this conversation.
        recent_history = load_recent_chat_history(
            chat_id,
            limit=8
        )

        result = get_ai_response(
            question=question,
            selected_document=selected_document,
            chat_history=recent_history
        )

        # Support both dictionary and tuple response formats.
        if isinstance(result, dict):

            answer = result.get("answer", "")
            sources = result.get("sources", [])

        elif isinstance(result, tuple):

            answer = (
                result[0]
                if len(result) > 0
                else ""
            )

            sources = (
                result[1]
                if len(result) > 1
                else []
            )

        else:

            answer = str(result or "")
            sources = []

        if not answer:

            answer = (
                "I could not generate an answer. "
                "Please try again."
            )

        if not isinstance(sources, list):
            sources = []

        # Save the user's question and AI response separately.
        # A persistence failure should be logged and returned
        # rather than silently ignored.
        save_conversation_message(
            chat_id=chat_id,
            role="user",
            content=question,
            selected_document=selected_document
        )

        save_conversation_message(
            chat_id=chat_id,
            role="assistant",
            content=answer,
            selected_document=selected_document
        )

        # Return chat_id so the frontend can keep track of the
        # conversation that contains these messages.
        return jsonify({
            "answer": answer,
            "sources": sources,
            "chat_id": chat_id
        }), 200

    except Exception as error:

        app.logger.exception(
            "Question answering failed."
        )

        return jsonify({
            "error": (
                "The AI could not answer this question. "
                "Check that Ollama is running and that the "
                "configured model is installed."
            ),
            "details": str(error)
        }), 500


# ============================================================
# GENERATE DOCUMENT REPORT
# ============================================================

@app.route("/api/report", methods=["POST"])
def generate_report():
    """
    Generate a detailed report for a selected PDF.

    If no document is selected, use the most recently modified
    PDF available in the uploads folder.
    """

    payload = request.get_json(silent=True) or {}

    try:

        selected_document = get_valid_selected_document(
            payload.get("selected_document")
        )

    except ValueError as error:

        return jsonify({
            "error": str(error)
        }), 404

    # If the user did not select a PDF, choose the most recent one.
    if not selected_document:
        selected_document = resolve_selected_document()

    if not selected_document:

        return jsonify({
            "error": (
                "Please upload a PDF before generating a report."
            )
        }), 400

    question = (
        "Create a detailed report about the selected document. "
        "Include the title or topic, purpose, introduction, "
        "main topics, important facts, definitions, examples, "
        "applications, findings, and conclusion where supported "
        "by the document. Use clear headings and well-organized "
        "paragraphs. Do not invent information."
    )

    try:

        result = get_ai_response(
            question=question,
            selected_document=selected_document,
            chat_history=[]
        )

        if isinstance(result, dict):

            answer = result.get("answer", "")
            sources = result.get("sources", [])

        elif isinstance(result, tuple):

            answer = (
                result[0]
                if len(result) > 0
                else ""
            )

            sources = (
                result[1]
                if len(result) > 1
                else []
            )

        else:

            answer = str(result or "")
            sources = []

        return jsonify({
            "answer": answer,
            "sources": (
                sources
                if isinstance(sources, list)
                else []
            ),
            "document": selected_document
        }), 200

    except Exception as error:

        app.logger.exception(
            "Report generation failed."
        )

        return jsonify({
            "error": (
                "Report generation failed. "
                "Check the VS Code terminal for details."
            ),
            "details": str(error)
        }), 500


# ============================================================
# LOAD SAVED CHAT HISTORY
# ============================================================

@app.route("/api/chat-history", methods=["GET"])
def get_chat_history():
    """
    Maintain compatibility with the previous chat-history API.

    Optional query parameter:
        /api/chat-history?chat_id=<conversation-id>

    If no ID is supplied, return the legacy conversation.
    """

    try:

        chat_id = get_requested_chat_id()

        conversation = get_chat_conversation(chat_id)

        messages = load_conversation_messages(chat_id)

        # Permit restoration of the legacy conversation when
        # it contains messages but no metadata document.
        if not conversation and not messages:

            return jsonify({
                "history": [],
                "chat_id": chat_id
            }), 200

        if not conversation:
            conversation = ensure_conversation(chat_id)

        # Refresh the in-memory cache.
        chat_history.clear()

        chat_history.extend([
            {
                "chat_id": chat_id,
                "role": message["role"],
                "content": message["content"]
            }
            for message in messages[-100:]
        ])

        return jsonify({
            "chat_id": chat_id,
            "conversation": {
                "id": chat_id,
                "title": conversation.get("title", "New Chat")
            },
            "history": messages
        }), 200

    except Exception as error:

        app.logger.exception(
            "Could not load saved chat history."
        )

        return jsonify({
            "error": "Could not load chat history.",
            "details": str(error)
        }), 500


# ============================================================
# CLEAR CURRENT CHAT
# ============================================================

@app.route("/api/clear-chat", methods=["POST"])
def clear_chat():
    """
    Delete only the specified conversation.

    This endpoint is retained for compatibility with the
    previous frontend. New Chat should use /api/new-chat
    instead of this endpoint.
    """

    payload = request.get_json(silent=True) or {}

    try:

        chat_id = get_requested_chat_id(payload)

        conversation = get_chat_conversation(chat_id)

        messages = load_conversation_messages(chat_id)

        if not conversation and not messages:

            return jsonify({
                "message": "This conversation is already empty.",
                "chat_id": chat_id
            }), 200

        delete_chat_conversation(chat_id)

        if chat_id == LEGACY_CHAT_ID:

            try:
                write_json(CHAT_PATH, [])
            except Exception:
                app.logger.exception(
                    "Could not clear the local legacy chat backup."
                )

        # Clear only cached messages for this conversation.
        chat_history[:] = [
            message
            for message in chat_history
            if message.get("chat_id") != chat_id
        ]

        return jsonify({
            "message": "Conversation cleared.",
            "chat_id": chat_id
        }), 200

    except Exception as error:

        app.logger.exception(
            "Could not clear the current conversation."
        )

        return jsonify({
            "error": "Could not clear this conversation.",
            "details": str(error)
        }), 500


# ============================================================
# FILE SIZE ERROR HANDLER
# ============================================================

@app.errorhandler(413)
def file_too_large(error):
    """Handle uploads exceeding the configured size limit."""

    return jsonify({
        "error": (
            "The PDF exceeds the 50 MB upload limit."
        )
    }), 413


# ============================================================
# GENERAL ERROR HANDLER
# ============================================================

@app.errorhandler(500)
def internal_server_error(error):
    """Return a JSON response for unexpected server errors."""

    return jsonify({
        "error": (
            "An internal server error occurred. "
            "Check the VS Code terminal for details."
        )
    }), 500


# ============================================================
# RUN APPLICATION
# ============================================================

if __name__ == "__main__":

    app.run(
        host="127.0.0.1",
        port=5000,
        debug=False,
        use_reloader=False,
        threaded=True
    )