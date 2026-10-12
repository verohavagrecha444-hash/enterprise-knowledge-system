import os
import firebase_admin
from dotenv import load_dotenv
from firebase_admin import credentials, firestore

# Load environment variables from .env
load_dotenv()


def initialize_firebase():
    """Initialize Firebase Admin SDK and return Firestore client."""

    if firebase_admin._apps:
        return firestore.client()

    credentials_path = os.getenv("FIREBASE_CREDENTIALS_PATH")

    if not credentials_path:
        raise RuntimeError(
            "FIREBASE_CREDENTIALS_PATH is not configured."
        )

    if not os.path.isfile(credentials_path):
        raise FileNotFoundError(
            f"Firebase credentials file not found: {credentials_path}"
        )

    cred = credentials.Certificate(credentials_path)
    firebase_admin.initialize_app(cred)

    return firestore.client()


db = initialize_firebase()


# ============================================================
# DOCUMENT METADATA
# ============================================================

def save_document_metadata(document_id, metadata):
    """Save uploaded PDF metadata to Firestore."""

    db.collection("documents").document(document_id).set(
        metadata,
        merge=True
    )


def get_all_documents():
    """Retrieve document metadata from Firestore."""

    documents = db.collection("documents").stream()

    return [
        {"id": document.id, **document.to_dict()}
        for document in documents
    ]


# ============================================================
# CHAT CONVERSATIONS
# ============================================================

def create_chat_conversation(chat_id, title="New Chat"):
    """
    Create a new conversation in Firestore.

    Each conversation has its own ID and a separate messages
    subcollection.
    """

    conversation_data = {
        "id": chat_id,
        "title": title,
        "created_at": firestore.SERVER_TIMESTAMP,
        "updated_at": firestore.SERVER_TIMESTAMP
    }

    (
        db.collection("chat_history")
        .document(chat_id)
        .set(conversation_data, merge=True)
    )

    return conversation_data


def get_all_chat_conversations():
    """
    Retrieve all saved conversations, newest activity first.
    """

    conversations = (
        db.collection("chat_history")
        .order_by(
            "updated_at",
            direction=firestore.Query.DESCENDING
        )
        .stream()
    )

    results = []

    for conversation in conversations:
        data = conversation.to_dict() or {}

        results.append({
            "id": conversation.id,
            "title": data.get("title", "New Chat"),
            "created_at": data.get("created_at"),
            "updated_at": data.get("updated_at")
        })

    return results


def get_chat_conversation(chat_id):
    """Retrieve one conversation's metadata."""

    conversation = (
        db.collection("chat_history")
        .document(chat_id)
        .get()
    )

    if not conversation.exists:
        return None

    return {
        "id": conversation.id,
        **(conversation.to_dict() or {})
    }


def update_chat_conversation(chat_id, title=None):
    """
    Update a conversation's activity time and optionally its title.
    """

    updates = {
        "updated_at": firestore.SERVER_TIMESTAMP
    }

    if title is not None:
        updates["title"] = title

    (
        db.collection("chat_history")
        .document(chat_id)
        .set(updates, merge=True)
    )


def delete_chat_conversation(chat_id):
    """
    Delete one conversation and all its messages.

    Other conversations and uploaded documents remain untouched.
    """

    conversation_ref = (
        db.collection("chat_history")
        .document(chat_id)
    )

    messages = (
        conversation_ref
        .collection("messages")
        .stream()
    )

    # Delete each message belonging to this conversation.
    batch = db.batch()
    batch_count = 0

    for message in messages:
        batch.delete(message.reference)
        batch_count += 1

        # Firestore batches support at most 500 operations.
        if batch_count == 450:
            batch.commit()
            batch = db.batch()
            batch_count = 0

    if batch_count:
        batch.commit()

    # Delete the conversation metadata after its messages.
    conversation_ref.delete()


# ============================================================
# CHAT MESSAGES
# ============================================================

def save_chat_message(chat_id, message_id, message):
    """
    Save a message inside its conversation.

    Existing message-storage structure is preserved:
    chat_history/{chat_id}/messages/{message_id}
    """

    (
        db.collection("chat_history")
        .document(chat_id)
        .collection("messages")
        .document(message_id)
        .set(message)
    )

    # Update the conversation's last activity.
    update_chat_conversation(chat_id)


def get_chat_messages(chat_id):
    """Retrieve messages in chronological order."""

    messages = (
        db.collection("chat_history")
        .document(chat_id)
        .collection("messages")
        .order_by("timestamp")
        .stream()
    )

    return [
        {"id": message.id, **message.to_dict()}
        for message in messages
    ]