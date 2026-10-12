import os
import re
import json
import traceback
import urllib.request
import urllib.error

from dotenv import load_dotenv

# Load project environment variables before reading model settings.
load_dotenv()

from functools import lru_cache
from difflib import get_close_matches

from langchain_community.document_loaders import PyPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.vectorstores import FAISS
from langchain_huggingface import HuggingFaceEmbeddings


# ============================================================
# CONFIGURATION
# ============================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads")
VECTORSTORE_FOLDER = os.path.join(BASE_DIR, "vectorstore")
FAISS_INDEX_PATH = os.path.join(VECTORSTORE_FOLDER, "db_faiss")

EMBEDDING_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"

OLLAMA_MODEL_NAME = os.getenv(
    "OLLAMA_MODEL_NAME",
    "qwen2.5:3b"
)

OLLAMA_API_URL = os.getenv(
    "OLLAMA_API_URL",
    "http://127.0.0.1:11434/api/generate"
)

CHUNK_SIZE = 800
CHUNK_OVERLAP = 150

# Maximum text characters sent to the model per summary section.
SUMMARY_CHUNK_CHARACTERS = 1400

# Maximum number of characters retained from recent chat history.
MAX_HISTORY_CHARACTERS = 6000

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(VECTORSTORE_FOLDER, exist_ok=True)


# ============================================================
# EMBEDDINGS
# ============================================================

@lru_cache(maxsize=1)
def get_embeddings():
    """
    Load the embedding model once and reuse it.

    Embeddings convert text into numerical representations
    so FAISS can find semantically relevant PDF passages.
    """

    return HuggingFaceEmbeddings(
        model_name=EMBEDDING_MODEL_NAME
    )


# ============================================================
# LANGUAGE MODEL
# ============================================================

def get_language_model():
    """Return the configured local Ollama model."""

    return OLLAMA_MODEL_NAME


# ============================================================
# GENERATE TEXT USING OLLAMA
# ============================================================

def generate_answer(prompt, max_new_tokens=500):
    """
    Generate a response using Ollama's local HTTP API.

    No paid AI API key is required.
    Ollama must be running on the computer.
    """

    if not prompt or not str(prompt).strip():
        return ""

    payload = {
        "model": OLLAMA_MODEL_NAME,
        "prompt": str(prompt),
        "stream": False,
        "options": {
            "num_predict": max(
                32,
                min(int(max_new_tokens), 900)
            ),
            "temperature": 0.2,
            "top_p": 0.9,
            "repeat_penalty": 1.08,
            "num_ctx": 8192
        }
    }

    request = urllib.request.Request(
        OLLAMA_API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json"
        },
        method="POST"
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=300
        ) as response:

            result = json.loads(
                response.read().decode("utf-8")
            )

        answer = str(
            result.get("response", "")
        ).strip()

        if not answer:
            raise RuntimeError(
                "Ollama returned an empty response."
            )

        return answer

    except urllib.error.HTTPError as error:
        error_body = error.read().decode(
            "utf-8",
            errors="replace"
        )

        raise RuntimeError(
            f"Ollama HTTP error {error.code}: {error_body}"
        ) from error

    except urllib.error.URLError as error:
        raise RuntimeError(
            "Cannot connect to Ollama. Start Ollama and "
            f"verify that '{OLLAMA_MODEL_NAME}' is installed. "
            f"Details: {error}"
        ) from error

    except json.JSONDecodeError as error:
        raise RuntimeError(
            "Ollama returned an invalid JSON response."
        ) from error


# ============================================================
# LOAD PDF PAGES
# ============================================================

def load_pdf_pages(filename):
    """
    Load all readable pages from a PDF inside uploads/.

    Each page receives its source filename and one-based page
    number in its metadata.
    """

    filename = os.path.basename(
        str(filename)
    )

    pdf_path = os.path.join(
        UPLOAD_FOLDER,
        filename
    )

    if not os.path.isfile(pdf_path):
        raise FileNotFoundError(
            f"PDF not found: {pdf_path}"
        )

    if not filename.lower().endswith(".pdf"):
        raise ValueError(
            "Only PDF files can be processed."
        )

    pages = PyPDFLoader(pdf_path).load()

    for page_number, page in enumerate(
        pages,
        start=1
    ):
        page.metadata["source"] = filename
        page.metadata["page"] = page_number

    return pages


# ============================================================
# SPLIT DOCUMENTS
# ============================================================

def split_documents(pages):
    """
    Split PDF pages into smaller chunks for FAISS retrieval.
    """

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        length_function=len,
        separators=[
            "\n\n",
            "\n",
            ". ",
            " ",
            ""
        ]
    )

    return splitter.split_documents(pages)


# ============================================================
# LOAD FAISS INDEX
# ============================================================

def load_vectorstore():
    """
    Load the saved FAISS index.

    Returns None if the index does not exist or cannot be loaded.
    """

    if not os.path.isdir(FAISS_INDEX_PATH):
        return None

    index_file = os.path.join(
        FAISS_INDEX_PATH,
        "index.faiss"
    )

    metadata_file = os.path.join(
        FAISS_INDEX_PATH,
        "index.pkl"
    )

    if not (
        os.path.isfile(index_file)
        and os.path.isfile(metadata_file)
    ):
        return None

    try:
        return FAISS.load_local(
            FAISS_INDEX_PATH,
            get_embeddings(),
            allow_dangerous_deserialization=True
        )

    except Exception:
        print(
            "Error loading FAISS index:\n",
            traceback.format_exc()
        )

        return None


# ============================================================
# INGEST PDF INTO FAISS
# ============================================================

def ingest_pdf(filename):
    """
    Extract a PDF, split it into chunks, and add those chunks
    to the persistent FAISS index.

    If the same PDF is uploaded again, its previous chunks
    are removed before its new chunks are inserted.
    """

    filename = os.path.basename(
        str(filename)
    )

    pages = load_pdf_pages(filename)

    if not pages:
        raise ValueError(
            "The PDF contains no readable pages."
        )

    chunks = split_documents(pages)

    if not chunks:
        raise ValueError(
            "No readable text was extracted from the PDF. "
            "Scanned PDFs may require OCR."
        )

    embeddings = get_embeddings()
    vectorstore = load_vectorstore()

    if vectorstore is None:

        vectorstore = FAISS.from_documents(
            chunks,
            embeddings
        )

    else:

        existing_ids = []

        for doc_id, document in (
            vectorstore.docstore._dict.items()
        ):

            existing_source = os.path.basename(
                str(
                    document.metadata.get(
                        "source",
                        ""
                    )
                )
            )

            if existing_source == filename:
                existing_ids.append(doc_id)

        if existing_ids:
            vectorstore.delete(
                ids=existing_ids
            )

        vectorstore.add_documents(
            chunks
        )

    vectorstore.save_local(
        FAISS_INDEX_PATH
    )

    print(
        f"Successfully indexed {len(chunks)} chunks "
        f"from {filename}"
    )

    return len(chunks)


# ============================================================
# INDEX STATISTICS
# ============================================================

def get_index_stats():
    """Return statistics for the current FAISS index."""

    vectorstore = load_vectorstore()

    if vectorstore is None:
        return {
            "total_chunks": 0,
            "index_exists": False
        }

    try:
        return {
            "total_chunks": int(
                vectorstore.index.ntotal
            ),
            "index_exists": True
        }

    except Exception:
        print(
            "Could not read FAISS statistics:\n",
            traceback.format_exc()
        )

        return {
            "total_chunks": 0,
            "index_exists": False
        }


# ============================================================
# RETRIEVE RELEVANT PDF CHUNKS
# ============================================================

def retrieve_documents(
    question,
    selected_document=None,
    k=5
):
    """
    Retrieve relevant passages from FAISS.

    If selected_document is supplied, only passages belonging
    to that PDF are returned.
    """

    vectorstore = load_vectorstore()

    if vectorstore is None:
        return []

    try:
        total_chunks = int(
            vectorstore.index.ntotal
        )

        if total_chunks == 0:
            return []

        if selected_document:
            # Filter inside FAISS retrieval so relevant chunks from the
            # selected PDF are not lost because other PDFs ranked higher.
            selected_name = os.path.basename(str(selected_document))

            try:
                results = vectorstore.similarity_search(
                    question,
                    k=min(k, total_chunks),
                    filter={"source": selected_name},
                    fetch_k=min(total_chunks, max(k * 10, 50))
                )
            except TypeError:
                # Compatibility fallback for older LangChain versions
                # that do not accept fetch_k in similarity_search().
                candidates = vectorstore.similarity_search(
                    question,
                    k=min(total_chunks, max(k * 10, 50)),
                    filter={"source": selected_name}
                )
                results = candidates[:k]
        else:
            # No PDF was explicitly selected: search across all indexed PDFs.
            results = vectorstore.similarity_search(
                question,
                k=min(k, total_chunks)
            )

        return results[:k]

    except Exception:
        print(
            "Document retrieval failed:\n",
            traceback.format_exc()
        )

        return []


# ============================================================
# RESOLVE SELECTED DOCUMENT
# ============================================================

def resolve_selected_document(selected_document=None):
    """
    Return the selected PDF filename.

    If no PDF is selected, use the most recently modified
    PDF in the uploads directory.
    """

    if selected_document:

        filename = os.path.basename(
            str(selected_document)
        )

        path = os.path.join(
            UPLOAD_FOLDER,
            filename
        )

        if os.path.isfile(path):
            return filename

        return None

    pdf_files = []

    for filename in os.listdir(UPLOAD_FOLDER):

        if filename.lower().endswith(".pdf"):

            path = os.path.join(
                UPLOAD_FOLDER,
                filename
            )

            if os.path.isfile(path):
                pdf_files.append((
                    os.path.getmtime(path),
                    filename
                ))

    if not pdf_files:
        return None

    pdf_files.sort(
        reverse=True
    )

    return pdf_files[0][1]


# ============================================================
# SOURCE FORMATTING
# ============================================================

def format_sources(documents):
    """
    Return unique source references.

    Example:
        ["report.pdf, page 2", "report.pdf, page 4"]
    """

    sources = []
    seen = set()

    for document in documents:

        metadata = getattr(
            document,
            "metadata",
            {}
        )

        filename = os.path.basename(
            str(
                metadata.get(
                    "source",
                    "Unknown document"
                )
            )
        )

        page = metadata.get(
            "page",
            metadata.get("page_number")
        )

        if page is not None:

            try:
                page = int(page)

                if page == 0:
                    page = 1

            except (ValueError, TypeError):
                pass

        if page is not None:
            source_label = (
                f"{filename}, page {page}"
            )
        else:
            source_label = filename

        if source_label not in seen:
            seen.add(source_label)
            sources.append(source_label)

    return sources


# ============================================================
# DETECT SUMMARY REQUESTS
# ============================================================

def is_summary_question(question):
    """Detect common document-summary requests."""

    question = str(
        question or ""
    ).lower().strip()

    patterns = [
        r"\bsummarize\b",
        r"\bsummarise\b",
        r"\bsummary\b",
        r"\boverview of\b",
        r"\bgive me an overview\b",
        r"\bmain points\b",
        r"\bkey points\b",
        r"\bimportant points\b",
        r"\bshort notes\b",
        r"\bexplain the whole document\b",
        r"\bwhat is this document about\b",
        r"\bwhat is this pdf about\b",
        r"\bbrief me on\b"
    ]

    return any(
        re.search(pattern, question)
        for pattern in patterns
    )


# ============================================================
# SUMMARIZE ONE SECTION
# ============================================================

def summarize_text(text):
    """
    Summarize a section of document text using Ollama.
    """

    if not text or not text.strip():
        return "No readable text was found."

    prompt = f"""
You are an enterprise document assistant.

Create a detailed and accurate summary of the document
section below.

Include:
1. The main topic and purpose.
2. Important concepts and definitions.
3. Key facts, figures, and technical details.
4. Important examples.
5. Conclusions, if present.

Use clear, simple English with headings and bullet points.
Preserve important names and numbers.
Do not invent information.
Do not claim that the section contains information
that is not present in the supplied text.

DOCUMENT SECTION:
{text}

DETAILED SUMMARY:
"""

    return generate_answer(
        prompt,
        max_new_tokens=600
    )


# ============================================================
# SUMMARIZE AN ENTIRE PDF
# ============================================================

def summarize_document(filename):
    """
    Summarize a complete PDF section by section.

    Returns a dictionary containing:
        answer
        sources
    """

    try:
        pages = load_pdf_pages(filename)

        if not pages:
            return {
                "answer": "The PDF contains no readable pages.",
                "sources": []
            }

        page_texts = []

        for page in pages:

            page_text = (
                page.page_content or ""
            ).strip()

            if page_text:

                page_number = page.metadata.get(
                    "page",
                    "?"
                )

                page_texts.append(
                    f"[Page {page_number}]\n{page_text}"
                )

        if not page_texts:
            return {
                "answer": (
                    "No readable text could be extracted from "
                    "this PDF. If it contains scanned pages, "
                    "OCR may be required."
                ),
                "sources": []
            }

        # Divide the document into manageable sections.
        full_text = "\n\n".join(
            page_texts
        )

        section_size = SUMMARY_CHUNK_CHARACTERS

        sections = [
            full_text[index:index + section_size]
            for index in range(
                0,
                len(full_text),
                section_size
            )
        ]

        section_summaries = []

        for index, section in enumerate(
            sections,
            start=1
        ):

            print(
                f"Summarizing section {index}/"
                f"{len(sections)} of {filename}"
            )

            summary = summarize_text(section)

            if summary and summary.strip():

                section_summaries.append(
                    f"Section {index}:\n{summary.strip()}"
                )

        if not section_summaries:
            return {
                "answer": (
                    "No summary could be generated. "
                    "Check that Ollama is running."
                ),
                "sources": format_sources(pages)
            }

        combined_summary = "\n\n".join(
            section_summaries
        )

        # For small documents, this combines the summaries.
        # For very large documents, the final input is limited.
        combined_summary = combined_summary[:12000]

        final_prompt = f"""
You are an enterprise document assistant.

Combine the section summaries below into one detailed,
well-organized summary of the PDF.

Requirements:
- Start with an overview of the document.
- Organize topics using headings.
- Include important facts, definitions, examples and numbers.
- Preserve technical details.
- Explain important ideas in clear language.
- Include conclusions where available.
- Avoid repeating the same points.
- Do not invent facts.
- Do not claim that information was present if it was
  not included in the section summaries.

SECTION SUMMARIES:
{combined_summary}

FINAL DETAILED SUMMARY:
"""

        final_summary = generate_answer(
            final_prompt,
            max_new_tokens=800
        )

        return {
            "answer": final_summary,
            "sources": format_sources(pages)
        }

    except Exception as error:

        print(
            "PDF summary failed:\n",
            traceback.format_exc()
        )

        return {
            "answer": (
                "I could not summarize this PDF. "
                f"Technical details: {error}"
            ),
            "sources": []
        }


# ============================================================
# DETECT WORD-COUNT QUESTIONS
# ============================================================

def is_count_question(question):
    """Detect questions asking for exact word or phrase counts."""

    question = str(
        question or ""
    ).lower().strip()

    patterns = [
        r"\bhow many times?\b",
        r"\bhow many occurrences?\b",
        r"\bcount the word\b",
        r"\bcount word\b",
        r"\bcount the phrase\b",
        r"\bcount the term\b",
        r"\bnumber of times\b",
        r"\bword count\b",
        r"\bfrequency of\b",
        r"\bhow often\b",
        r"\bcount occurrences\b",
        r"\bcount the occurrences\b"
    ]

    return any(
        re.search(pattern, question)
        for pattern in patterns
    )


# ============================================================
# EXTRACT THE WORD OR PHRASE TO COUNT
# ============================================================

def extract_count_target(question):
    """
    Extract a requested word or phrase.

    Examples:
        How many times does Instagram appear?
        How many occurrences of Big Data?
        Count the word technology.
    """

    question = str(
        question or ""
    ).strip().rstrip("?.! ")

    patterns = [
        r"""how\s+many\s+times?\s+
        (?:does|did)\s+["']?(.+?)["']?\s+
        (?:appear|appears|occur|occurs)\b""",

        r"""how\s+many\s+times?\s+
        (?:has|have|had)\s+["']?(.+?)["']?\s+
        (?:appeared|occurred|appear|occur)\b""",

        r"""how\s+many\s+occurrences?\s+of\s+
        ["']?(.+?)["']?$""",

        r"""count\s+(?:the\s+)?
        (?:word|phrase|term)\s+
        ["']?(.+?)["']?$""",

        r"""frequency\s+of\s+["']?(.+?)["']?$""",

        r"""how\s+often\s+does\s+
        ["']?(.+?)["']?\s+
        (?:appear|occur|show\s+up)\b""",

        r"""(?:find|tell\s+me)\s+(?:the\s+)?
        (?:number|count)\s+of\s+times?\s+
        ["']?(.+?)["']?\s+
        (?:appears|appeared|occurs|occurred)\b"""
    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            question,
            flags=re.IGNORECASE | re.VERBOSE
        )

        if match:

            target = match.group(1).strip()

            target = target.strip(
                " \t\n\r\"'`.,?!"
            )

            if target:
                return target

    return None


# ============================================================
# COUNT A WORD OR PHRASE IN A PDF
# ============================================================

def count_term_in_document(term, filename):
    """
    Count exact occurrences throughout a PDF.

    Returns:
        total_count
        matching_pages
    """

    if not term or not term.strip():
        raise ValueError(
            "Please provide a word or phrase to count."
        )

    pages = load_pdf_pages(filename)

    escaped_term = re.escape(
        term.strip()
    )

    # Allow one or more spaces between words in a phrase.
    escaped_term = escaped_term.replace(
        r"\ ",
        r"\s+"
    )

    pattern = (
        r"(?<!\w)"
        + escaped_term
        + r"(?!\w)"
    )

    total_count = 0
    matching_pages = []

    for page in pages:

        page_text = page.page_content or ""

        matches = re.findall(
            pattern,
            page_text,
            flags=re.IGNORECASE
        )

        if matches:

            total_count += len(matches)

            matching_pages.append({
                "source": page.metadata.get(
                    "source",
                    os.path.basename(filename)
                ),
                "page": page.metadata.get(
                    "page",
                    "?"
                ),
                "count": len(matches)
            })

    return total_count, matching_pages


# ============================================================
# FIND A SIMILAR WORD
# ============================================================

def find_closest_term_in_document(term, filename):
    """
    Suggest a similar word found in the PDF.
    """

    pages = load_pdf_pages(filename)

    document_words = set()

    for page in pages:

        words = re.findall(
            r"\b[A-Za-z][A-Za-z'-]*\b",
            page.page_content or ""
        )

        document_words.update(words)

    matches = get_close_matches(
        term,
        list(document_words),
        n=1,
        cutoff=0.75
    )

    return matches[0] if matches else None


# ============================================================
# ANSWER A QUESTION USING PDF CONTEXT
# ============================================================

def answer_question(
    question,
    documents=None,
    chat_history=None
):
    """
    Answer a question using retrieved PDF passages.

    If no passages are available, the local language model
    can still answer general questions.
    """

    documents = documents or []
    chat_history = chat_history or []

    context_parts = []

    for document in documents:

        metadata = getattr(
            document,
            "metadata",
            {}
        )

        filename = os.path.basename(
            str(
                metadata.get(
                    "source",
                    "Unknown document"
                )
            )
        )

        page_number = metadata.get(
            "page",
            "?"
        )

        page_text = (
            getattr(
                document,
                "page_content",
                ""
            ) or ""
        ).strip()

        if page_text:

            context_parts.append(
                f"Source: {filename}, page {page_number}\n"
                f"{page_text}"
            )

    context = "\n\n---\n\n".join(
        context_parts
    )

    # --------------------------------------------------------
    # CONVERSATION HISTORY
    # --------------------------------------------------------

    history_parts = []

    for item in chat_history[-8:]:

        if not isinstance(item, dict):
            continue

        role = str(
            item.get(
                "role",
                item.get("sender", "")
            )
        ).lower()

        content = str(
            item.get(
                "content",
                item.get(
                    "message",
                    item.get("text", "")
                )
            )
        ).strip()

        if not content:
            continue

        if role in ("user", "human"):
            label = "User"

        elif role in ("assistant", "ai", "bot"):
            label = "Assistant"

        else:
            continue

        history_parts.append(
            f"{label}: {content}"
        )

    history_text = "\n".join(
        history_parts
    )

    history_text = history_text[
        -MAX_HISTORY_CHARACTERS:
    ]

    # --------------------------------------------------------
    # PROMPT INSTRUCTIONS
    # --------------------------------------------------------

    if context:

        context_instructions = """
Use the supplied PDF passages when the question concerns
the uploaded document.

Do not invent document facts.

If the retrieved passages do not contain enough information,
explain that the available passages do not establish the answer.

For general-knowledge questions, you may use your existing
knowledge, but distinguish general knowledge from facts
explicitly supported by the PDF.

If the user asks a follow-up question, consider the recent
conversation history.
"""

    else:

        context_instructions = """
No PDF passages were retrieved for this question.

Answer as a helpful general-purpose assistant using your
existing knowledge.

If the question refers to a specific document that has not
been provided, explain that you need the relevant document
or text to answer from that source.

If uncertain, say so rather than inventing facts.
"""

    prompt = f"""
You are an intelligent enterprise knowledge assistant.

You can:
- Answer questions in simple language.
- Explain technical concepts with examples.
- Summarize and analyze documents.
- Translate text when requested.
- Maintain conversational context.
- Help with coding and general questions.

IMPORTANT INSTRUCTIONS:
{context_instructions}

Conversation history:
{history_text if history_text else "No earlier conversation."}

Retrieved PDF passages:
{context if context else "No PDF passages were retrieved."}

Current user question:
{question}

Answer directly and helpfully.

Use headings and numbered steps when appropriate.
Follow the language requested by the user.
Do not claim to have searched a document if no passages
from that document were provided.

ANSWER:
"""

    return generate_answer(
        prompt,
        max_new_tokens=700
    )


# ============================================================
# CONVERSATIONAL FOLLOW-UP
# ============================================================

def answer_follow_up(user_question, chat_history):
    """
    Answer a follow-up question using conversation history.
    """

    return answer_question(
        question=user_question,
        documents=[],
        chat_history=chat_history
    )


# ============================================================
# MAIN AI RESPONSE FUNCTION
# ============================================================

def get_ai_response(
    question=None,
    selected_document=None,
    chat_history=None,
    query=None,
    **kwargs
):
    """
    Main entry point used by app.py.

    Always returns a dictionary:

        {
            "answer": "...",
            "sources": [...]
        }

    Supported requests:
    - Document questions
    - General questions
    - Detailed PDF summaries
    - Exact word and phrase counting
    - Conversational follow-ups
    - Source references
    """

    user_question = str(
        question or query or kwargs.get("input") or ""
    ).strip()

    chat_history = chat_history or []

    if not user_question:

        return {
            "answer": "Please enter a question.",
            "sources": []
        }

    try:

        # Keep explicit selection separate from the default PDF.
        # General questions should search across all indexed documents
        # when the user has not selected a specific PDF.
        selected_filename = (
            resolve_selected_document(selected_document)
            if selected_document
            else None
        )

        # Summary and exact-count actions may use the most recently
        # uploaded PDF when no document has been selected.
        filename = selected_filename or resolve_selected_document()

        # ----------------------------------------------------
        # DOCUMENT SUMMARY
        # ----------------------------------------------------

        if is_summary_question(user_question):

            if not filename:

                return {
                    "answer": (
                        "No PDF was found in the uploads folder. "
                        "Please upload a PDF and try again."
                    ),
                    "sources": []
                }

            return summarize_document(
                filename
            )

        # ----------------------------------------------------
        # EXACT WORD OR PHRASE COUNT
        # ----------------------------------------------------

        if is_count_question(user_question):

            target = extract_count_target(
                user_question
            )

            if not target:

                return {
                    "answer": (
                        "Please specify the exact word or "
                        "phrase you want me to count. "
                        "For example: How many times does "
                        "technology appear?"
                    ),
                    "sources": []
                }

            if not filename:

                return {
                    "answer": (
                        "Please upload a PDF before counting "
                        "words in a document."
                    ),
                    "sources": []
                }

            count, matching_pages = count_term_in_document(
                target,
                filename
            )

            answer = (
                f'The word or phrase "{target}" appears '
                f'{count} time(s) in "{filename}".'
            )

            if count == 0:

                similar_term = find_closest_term_in_document(
                    target,
                    filename
                )

                if similar_term:

                    answer += (
                        f'\n\nI found a similar word: '
                        f'"{similar_term}". Would you like '
                        f'me to count that instead?'
                    )

            elif matching_pages:

                page_details = [
                    f'- Page {item["page"]}: '
                    f'{item["count"]} occurrence(s)'
                    for item in matching_pages
                ]

                answer += (
                    "\n\nPage-wise count:\n"
                    + "\n".join(page_details)
                )

            return {
                "answer": answer,
                "sources": [
                    {
                        "source": filename,
                        "page": item["page"]
                    }
                    for item in matching_pages
                ]
            }

        # ----------------------------------------------------
        # RETRIEVE RELEVANT PDF PASSAGES
        # ----------------------------------------------------

        documents = retrieve_documents(
            user_question,
            selected_document=selected_filename,
            k=5
        )

        # ----------------------------------------------------
        # GENERATE ANSWER
        # ----------------------------------------------------

        answer = answer_question(
            question=user_question,
            documents=documents,
            chat_history=chat_history
        )

        return {
            "answer": answer,
            "sources": format_sources(documents)
        }

    except Exception as error:

        print(
            "AI response failed:\n",
            traceback.format_exc()
        )

        return {
            "answer": (
                "The AI could not process your request. "
                "Check that Ollama is running and the configured "
                f"model '{OLLAMA_MODEL_NAME}' is installed. "
                f"Technical details: {error}"
            ),
            "sources": []
        }


# ============================================================
# COMPATIBILITY HELPER
# ============================================================

def get_answer(
    question=None,
    selected_document=None,
    chat_history=None,
    **kwargs
):
    """
    Compatibility alias for code using get_answer().
    """

    return get_ai_response(
        question=question,
        selected_document=selected_document,
        chat_history=chat_history,
        **kwargs
    )