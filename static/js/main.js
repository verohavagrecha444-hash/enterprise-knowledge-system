document.addEventListener("DOMContentLoaded", () => {
    "use strict";

    // =========================================================
    // DOM HELPERS
    // =========================================================

    const $ = (selector) => document.querySelector(selector);

    const $$ = (selector) =>
        Array.from(document.querySelectorAll(selector));

    // =========================================================
    // ELEMENTS
    // =========================================================

    const navItems = $$("[data-page]");
    const sections = $$(".view-section");

    const chatForm = $("#chat-form");
    const chatInput = $("#user-input");
    const chatHistory = $("#chat-history");
    const chatList = $("#chat-list");
    const refreshChatsButton = $("#refresh-chats-btn");
    const chatStatus = $("#chat-status");
    const sendButton = $("#send-btn");
    const micButton = $("#mic-btn");

    const fileInput = $("#pdf-upload");
    const uploadButton = $("#upload-btn");
    const uploadStatus = $("#upload-status");
    const uploadDropzone = $("#upload-dropzone");

    const recentDocumentsList = $("#recent-documents-list");
    const documentsContent = $("#documents-content");
    const documentSearch = $("#document-search");

    const reportButton = $("#generate-report-btn");
    const reportStatus = $("#report-status");
    const reportOutput = $("#reports-content");
    const reportSelect = $("#report-document");

    // =========================================================
    // APPLICATION STATE
    // =========================================================

    let currentDocuments = [];
    let selectedDocument = null;

    const ACTIVE_CHAT_STORAGE_KEY = "econnect_active_chat_id";
    let currentChatId = localStorage.getItem(ACTIVE_CHAT_STORAGE_KEY) || null;
    let conversations = [];
    let isSwitchingChat = false;

    let isAsking = false;
    let isUploading = false;
    let isGeneratingReport = false;

    let requestCounter = 0;

    // =========================================================
    // PAGE INFORMATION
    // =========================================================

    const pageDetails = {
        "ai-assistant": {
            title: "AI Document Assistant",
            subtitle: "Your Intelligent Knowledge Partner"
        },

        documents: {
            title: "Managed Documents",
            subtitle: "View and manage your uploaded documents"
        },

        knowledge: {
            title: "Knowledge Base",
            subtitle: "Explore your indexed document knowledge"
        },

        analytics: {
            title: "System Analytics",
            subtitle: "View your application statistics"
        },

        reports: {
            title: "Document Reports",
            subtitle: "Generate reports from your documents"
        },

        settings: {
            title: "Settings",
            subtitle: "Manage your application"
        }
    };

    // =========================================================
    // GENERAL HELPERS
    // =========================================================

    function setText(element, value) {
        if (element) {
            element.textContent = String(value ?? "");
        }
    }

    function setChatStatus(message) {
        setText(chatStatus, message);
    }

    function setUploadStatus(message) {
        setText(uploadStatus, message);
    }

    function formatBytes(bytes) {
        const size = Number(bytes) || 0;

        if (size < 1024) {
            return `${size} B`;
        }

        if (size < 1024 * 1024) {
            return `${(size / 1024).toFixed(1)} KB`;
        }

        return `${(size / (1024 * 1024)).toFixed(2)} MB`;
    }

    function escapeText(value) {
        return String(value ?? "");
    }

    function normalizeFilename(filename) {
        return String(filename ?? "")
            .split(/[\\/]/)
            .pop();
    }

    // =========================================================
    // API HELPERS
    // =========================================================

    async function readJsonResponse(response) {
        let result;

        try {
            result = await response.json();
        } catch {
            throw new Error(
                `The server returned an invalid response. HTTP ${response.status}.`
            );
        }

        if (!response.ok) {
            const message =
                result.error ||
                result.message ||
                `Request failed. HTTP ${response.status}.`;

            throw new Error(message);
        }

        return result;
    }

    async function apiRequest(
        url,
        options = {}
    ) {
        const response = await fetch(url, {
            ...options,
            headers: {
                ...(options.body instanceof FormData
                    ? {}
                    : { "Content-Type": "application/json" }),
                ...(options.headers || {})
            }
        });

        return readJsonResponse(response);
    }

    // =========================================================
    // PAGE NAVIGATION
    // =========================================================

    function showPage(pageName) {
        const targetId = `view-${pageName}`;

        const target = document.getElementById(targetId);

        if (!target) {
            console.warn("Page not found:", targetId);
            return;
        }

        sections.forEach((section) => {
            const active = section.id === targetId;

            section.hidden = !active;

            section.style.display = active ? "" : "none";

            section.classList.toggle(
                "active-view",
                active
            );
        });

        navItems.forEach((item) => {
            const active = item.dataset.page === pageName;

            item.classList.toggle(
                "active",
                active
            );

            if (active) {
                item.setAttribute(
                    "aria-current",
                    "page"
                );
            } else {
                item.removeAttribute(
                    "aria-current"
                );
            }
        });

        const details = pageDetails[pageName];

        if (details) {
            setText(
                $("#page-title"),
                details.title
            );

            setText(
                $("#page-subtitle"),
                details.subtitle
            );
        }

        if (
            pageName === "documents" ||
            pageName === "knowledge"
        ) {
            loadDocuments();
        }

        if (
            pageName === "analytics" ||
            pageName === "knowledge"
        ) {
            loadStats();
        }

        if (pageName === "reports") {
            populateReportDocuments();
        }
    }

    navItems.forEach((item) => {
        item.addEventListener("click", (event) => {
            event.preventDefault();

            showPage(item.dataset.page);
        });
    });

    // =========================================================
    // SAVED CONVERSATIONS
    // =========================================================

    function getMessageContent(message) {
        return message?.content ?? message?.text ?? message?.message ?? "";
    }

    function clearVisibleMessages() {
        if (chatHistory) chatHistory.replaceChildren();
    }

    function setActiveChatId(chatId) {
        currentChatId = chatId || null;
        if (currentChatId) {
            localStorage.setItem(ACTIVE_CHAT_STORAGE_KEY, currentChatId);
        } else {
            localStorage.removeItem(ACTIVE_CHAT_STORAGE_KEY);
        }
    }

    function renderConversationList() {
        if (!chatList) return;
        chatList.replaceChildren();

        if (!conversations.length) {
            const empty = document.createElement("p");
            empty.className = "chat-list-empty";
            empty.textContent = "No saved conversations yet.";
            chatList.appendChild(empty);
            return;
        }

        conversations.forEach((conversation) => {
            const id = conversation.id || conversation.chat_id;
            if (!id) return;

            const row = document.createElement("div");
            row.className = "chat-list-item" + (id === currentChatId ? " active" : "");
            row.dataset.chatId = id;

            const openButton = document.createElement("button");
            openButton.type = "button";
            openButton.className = "chat-list-title";
            openButton.textContent = conversation.title || "New Chat";
            openButton.title = conversation.title || "New Chat";
            openButton.disabled = isAsking || isSwitchingChat;
            openButton.addEventListener("click", () => restoreChat(id));

            const deleteButton = document.createElement("button");
            deleteButton.type = "button";
            deleteButton.className = "chat-delete-btn";
            deleteButton.textContent = "×";
            deleteButton.title = "Delete conversation";
            deleteButton.setAttribute("aria-label", "Delete conversation");
            deleteButton.disabled = isAsking || isSwitchingChat;
            deleteButton.addEventListener("click", (event) => {
                event.stopPropagation();
                deleteConversation(id);
            });

            row.append(openButton, deleteButton);
            chatList.appendChild(row);
        });
    }

    async function loadConversations() {
        try {
            const result = await apiRequest("/api/chats");
            conversations = Array.isArray(result.chats)
                ? result.chats
                : Array.isArray(result.conversations)
                    ? result.conversations
                    : [];
            renderConversationList();
            return conversations;
        } catch (error) {
            console.error("Could not load conversations:", error);
            if (chatList) {
                chatList.replaceChildren();
                const empty = document.createElement("p");
                empty.className = "chat-list-empty";
                empty.textContent = "Could not load conversations. Check the server.";
                chatList.appendChild(empty);
            }
            return [];
        }
    }

    async function restoreChat(chatId) {
        if (!chatId || isAsking || isSwitchingChat) return;
        isSwitchingChat = true;
        requestCounter++;
        renderConversationList();
        setChatStatus("Loading conversation...");

        try {
            const result = await apiRequest(`/api/chats/${encodeURIComponent(chatId)}`);
            const messages = Array.isArray(result.messages)
                ? result.messages
                : Array.isArray(result.history)
                    ? result.history
                    : Array.isArray(result.chat?.messages)
                        ? result.chat.messages
                        : [];
            setActiveChatId(chatId);
            clearVisibleMessages();

            if (!messages.length) {
                addWelcomeMessage();
            } else {
                messages.forEach((message) => {
                    const role = String(message.role || message.sender || "assistant").toLowerCase();
                    addMessage(role === "user" ? "user" : "assistant", getMessageContent(message));
                });
            }

            if (chatInput) {
                chatInput.value = "";
                chatInput.placeholder = "Ask anything about your documents...";
            }
            showPage("ai-assistant");
            setChatStatus("Conversation loaded.");
            renderConversationList();
        } catch (error) {
            console.error("Could not restore conversation:", error);
            setChatStatus("Could not load this conversation.");
            alert("Could not load conversation: " + error.message);
        } finally {
            isSwitchingChat = false;
            renderConversationList();
        }
    }

    async function createNewChat() {
        if (isAsking || isSwitchingChat) return;
        isSwitchingChat = true;
        requestCounter++;
        try {
            const result = await apiRequest("/api/new-chat", { method: "POST", body: JSON.stringify({}) });
            const chat = result.chat || result.conversation || result;
            const chatId = chat.id || chat.chat_id || result.chat_id;
            if (!chatId) throw new Error("The server did not return a conversation ID.");

            setActiveChatId(chatId);
            clearVisibleMessages();
            addWelcomeMessage();
            if (chatInput) {
                chatInput.value = "";
                chatInput.placeholder = "Ask anything about your documents...";
                chatInput.focus();
            }
            showPage("ai-assistant");
            setChatStatus("New conversation started.");
            await loadConversations();
        } catch (error) {
            console.error("Could not create a new conversation:", error);
            alert("Could not start a new conversation: " + error.message);
        } finally {
            isSwitchingChat = false;
            renderConversationList();
        }
    }

    async function deleteConversation(chatId) {
        if (!chatId || isAsking || isSwitchingChat) return;
        const conversation = conversations.find((item) => (item.id || item.chat_id) === chatId);
        const title = conversation?.title || "this conversation";
        if (!confirm(`Delete ${title}? This cannot be undone.`)) return;

        try {
            await apiRequest(`/api/chats/${encodeURIComponent(chatId)}`, { method: "DELETE" });
            if (chatId === currentChatId) {
                setActiveChatId(null);
                clearVisibleMessages();
                addWelcomeMessage();
            }
            await loadConversations();
            if (chatId === currentChatId || !currentChatId) {
                if (conversations.length) {
                    const nextId = conversations[0].id || conversations[0].chat_id;
                    if (nextId) await restoreChat(nextId);
                    else await createNewChat();
                } else {
                    await createNewChat();
                }
            }
        } catch (error) {
            console.error("Could not delete conversation:", error);
            alert("Could not delete conversation: " + error.message);
        }
    }

    async function loadChatHistory() {
        const chats = await loadConversations();
        if (currentChatId && chats.some((item) => (item.id || item.chat_id) === currentChatId)) {
            await restoreChat(currentChatId);
            return;
        }
        if (chats.length) {
            const firstId = chats[0].id || chats[0].chat_id;
            if (firstId) {
                await restoreChat(firstId);
                return;
            }
        }
        await createNewChat();
    }

    // =========================================================
    // CHAT MESSAGE DISPLAY
    // =========================================================

    function addMessage(role, text) {
        if (!chatHistory) {
            return;
        }

        const message = document.createElement("div");

        const isUser = role === "user";

        message.className = isUser
            ? "message user-message"
            : "message assistant-message";

        const paragraph = document.createElement("p");

        paragraph.textContent = escapeText(text);

        message.appendChild(paragraph);

        chatHistory.appendChild(message);

        chatHistory.scrollTop =
            chatHistory.scrollHeight;
    }

    function addWelcomeMessage() {
        if (!chatHistory) {
            return;
        }

        const welcome = document.createElement("div");

        welcome.className = "message ai-msg";

        const heading = document.createElement("h3");

        heading.textContent = "Welcome!";

        const paragraph = document.createElement("p");

        paragraph.textContent =
            "Ask me questions about your uploaded documents, " +
            "request summaries, count words, or ask general questions.";

        welcome.appendChild(heading);
        welcome.appendChild(paragraph);

        chatHistory.appendChild(welcome);
    }

    // =========================================================
    // SOURCE REFERENCES
    // =========================================================

    function getSourceText(sources) {
        if (!Array.isArray(sources)) {
            return "";
        }

        const formatted = sources.map((source) => {
            if (typeof source === "string") {
                return source;
            }

            if (!source || typeof source !== "object") {
                return "";
            }

            const filename =
                source.file ||
                source.filename ||
                source.source ||
                "PDF";

            if (
                source.page !== undefined &&
                source.page !== null
            ) {
                return `${filename} — Page ${source.page}`;
            }

            return filename;
        });

        return [
            ...new Set(
                formatted.filter(Boolean)
            )
        ].join("\n");
    }

    function displaySources(sources) {
        const sourceText = getSourceText(sources);

        if (!sourceText) {
            return;
        }

        addMessage(
            "assistant",
            "Sources:\n" +
            sourceText
                .split("\n")
                .map((source) => `• ${source}`)
                .join("\n")
        );
    }

    // =========================================================
    // SELECTED DOCUMENT
    // =========================================================

    function updateSelectedDocumentLabel() {
        let label = $("#selected-document-label");

        if (!label && chatForm) {
            label = document.createElement("p");

            label.id = "selected-document-label";

            label.className = "status-message";

            chatForm.parentNode.insertBefore(
                label,
                chatForm
            );
        }

        if (!label) {
            return;
        }

        if (selectedDocument) {
            label.textContent =
                `Using document: ${selectedDocument.name}. ` +
                "Select another document to switch.";

            label.hidden = false;
        } else {
            label.textContent = "";

            label.hidden = true;
        }
    }

    function selectDocument(doc) {
        if (!doc || !doc.stored_name) {
            setChatStatus(
                "The document filename is missing. Refresh the document list."
            );

            return;
        }

        selectedDocument = doc;

        updateSelectedDocumentLabel();

        showPage("ai-assistant");

        setChatStatus(
            `Selected ${doc.name || doc.stored_name}.`
        );

        if (chatInput) {
            chatInput.placeholder =
                `Ask a question about ${doc.name || doc.stored_name}...`;

            chatInput.focus();
        }
    }

    // =========================================================
    // ASK THE AI
    // =========================================================

    async function askQuestion(question) {
        question = String(question || "").trim();

        if (!question || isAsking) {
            return;
        }

        if (!currentChatId) {
            await createNewChat();
            if (!currentChatId) {
                setChatStatus("Could not start a conversation.");
                return;
            }
        }

        const requestId = ++requestCounter;
        const requestChatId = currentChatId;

        isAsking = true;

        if (chatInput) {
            chatInput.disabled = true;
        }

        if (sendButton) {
            sendButton.disabled = true;
        }

        addMessage("user", question);

        setChatStatus("Thinking...");

        try {
            const result = await apiRequest(
                "/ask",
                {
                    method: "POST",

                    body: JSON.stringify({
                        question,
                        chat_id: requestChatId,

                        selected_document:
                            selectedDocument
                                ? selectedDocument.stored_name
                                : null
                    })
                }
            );

            if (requestId !== requestCounter || currentChatId !== requestChatId) {
                return;
            }

            addMessage(
                "assistant",
                result.answer ||
                "The AI did not return an answer."
            );

            displaySources(
                result.sources
            );

            setChatStatus("Answer received.");

            await Promise.all([loadStats(), loadConversations()]);

        } catch (error) {
            console.error(
                "Question answering error:",
                error
            );

            addMessage(
                "assistant",
                "Sorry, I could not process your question.\n\n" +
                error.message
            );

            setChatStatus(
                "Could not process your question."
            );

        } finally {
            isAsking = false;

            if (chatInput) {
                chatInput.disabled = false;

                chatInput.focus();
            }

            if (sendButton) {
                sendButton.disabled = false;
            }
        }
    }

    if (chatForm) {
        chatForm.addEventListener("submit", (event) => {
            event.preventDefault();

            if (!chatInput) {
                return;
            }

            const question = chatInput.value.trim();

            if (!question) {
                return;
            }

            chatInput.value = "";

            askQuestion(question);
        });
    }

    // =========================================================
    // CLEAR CURRENT CHAT / NEW CHAT
    // =========================================================

    async function clearChat() {
        if (!currentChatId) {
            await createNewChat();
            return;
        }

        const confirmed = confirm(
            "Delete the current conversation and its messages? Other conversations will remain saved."
        );
        if (!confirmed) return;

        const oldChatId = currentChatId;
        try {
            await apiRequest("/api/clear-chat", {
                method: "POST",
                body: JSON.stringify({ chat_id: oldChatId })
            });
            setActiveChatId(null);
            clearVisibleMessages();
            await loadConversations();
            await createNewChat();
            setChatStatus("Current conversation cleared.");
            await loadStats();
        } catch (error) {
            console.error("Clear chat error:", error);
            alert("Could not clear the conversation: " + error.message);
        }
    }

    const newChatButton = $("#new-chat-btn");
    const clearChatButton = $("#clear-chat-btn");

    if (newChatButton) {
        newChatButton.addEventListener("click", () => {
            showPage("ai-assistant");
            createNewChat();
        });
    }

    if (clearChatButton) {
        clearChatButton.addEventListener("click", clearChat);
    }

    if (refreshChatsButton) {
        refreshChatsButton.addEventListener("click", loadConversations);
    }

    // =========================================================
    // QUICK ACTIONS
    // =========================================================

    $$("[data-action]").forEach((button) => {
        button.addEventListener("click", () => {
            const action = (
                button.dataset.action || ""
            ).toLowerCase();

            let prompt;

            if (action.includes("summar")) {
                prompt = "Summarize this document in detail.";

            } else if (action.includes("compar")) {
                prompt =
                    "Compare the available documents. " +
                    "Explain their similarities and differences.";

            } else if (action.includes("report")) {
                prompt =
                    "Generate a detailed report about this document.";

            } else if (
                action.includes("count") ||
                action.includes("word")
            ) {
                prompt =
                    "How many words are in this document?";

            } else {
                prompt = button.dataset.action;
            }

            showPage("ai-assistant");

            if (chatInput) {
                chatInput.value = prompt;
            }

            askQuestion(prompt);
        });
    });

    // =========================================================
    // PDF UPLOAD
    // =========================================================

    async function uploadPDF() {
        if (isUploading) {
            return;
        }

        if (
            !fileInput ||
            !fileInput.files ||
            !fileInput.files.length
        ) {
            setUploadStatus(
                "Please select a PDF file first."
            );

            return;
        }

        const file = fileInput.files[0];

        if (!file.name.toLowerCase().endsWith(".pdf")) {
            setUploadStatus(
                "Only PDF files are supported."
            );

            return;
        }

        const maximumSize = 50 * 1024 * 1024;

        if (file.size > maximumSize) {
            setUploadStatus(
                "The PDF exceeds the 50 MB upload limit."
            );

            return;
        }

        const formData = new FormData();

        formData.append("file", file);

        isUploading = true;

        if (uploadButton) {
            uploadButton.disabled = true;

            uploadButton.textContent = "Uploading...";
        }

        setUploadStatus(
            "Uploading and indexing your PDF. Please wait..."
        );

        try {
            const result = await apiRequest(
                "/upload",
                {
                    method: "POST",
                    body: formData
                }
            );

            setUploadStatus(
                result.warning
                    ? `${result.message} ${result.warning}`
                    : result.message ||
                      "PDF uploaded successfully."
            );

            fileInput.value = "";

            await loadDocuments();

            await loadStats();

        } catch (error) {
            console.error(
                "PDF upload error:",
                error
            );

            setUploadStatus(
                "Upload failed: " + error.message
            );

        } finally {
            isUploading = false;

            if (uploadButton) {
                uploadButton.disabled = false;

                uploadButton.textContent = "Upload";
            }
        }
    }

    if (uploadButton) {
        uploadButton.addEventListener(
            "click",
            uploadPDF
        );
    }

    if (fileInput) {
        fileInput.addEventListener("change", () => {
            const file = fileInput.files?.[0];

            if (file) {
                setUploadStatus(
                    `Selected: ${file.name}`
                );
            }
        });
    }

    // =========================================================
    // DRAG AND DROP
    // =========================================================

    if (uploadDropzone && fileInput) {
        uploadDropzone.addEventListener(
            "dragover",
            (event) => {
                event.preventDefault();

                uploadDropzone.classList.add(
                    "dragging"
                );
            }
        );

        uploadDropzone.addEventListener(
            "dragleave",
            () => {
                uploadDropzone.classList.remove(
                    "dragging"
                );
            }
        );

        uploadDropzone.addEventListener(
            "drop",
            (event) => {
                event.preventDefault();

                uploadDropzone.classList.remove(
                    "dragging"
                );

                const files =
                    event.dataTransfer?.files;

                if (!files || !files.length) {
                    return;
                }

                const file = files[0];

                if (!file.name.toLowerCase().endsWith(".pdf")) {
                    setUploadStatus(
                        "Only PDF files are supported."
                    );

                    return;
                }

                try {
                    const dataTransfer =
                        new DataTransfer();

                    Array.from(files).forEach(
                        (item) => {
                            dataTransfer.items.add(item);
                        }
                    );

                    fileInput.files =
                        dataTransfer.files;

                    setUploadStatus(
                        `Selected: ${file.name}`
                    );

                } catch (error) {
                    console.error(
                        "Could not attach dropped file:",
                        error
                    );

                    setUploadStatus(
                        "Could not select the dropped file. Use the file picker instead."
                    );
                }
            }
        );

        uploadDropzone.addEventListener(
            "click",
            (event) => {
                if (
                    event.target.closest("button") ||
                    event.target.closest("input")
                ) {
                    return;
                }

                fileInput.click();
            }
        );
    }

    // =========================================================
    // LOAD DOCUMENTS
    // =========================================================

    async function loadDocuments() {
        try {
            const result = await apiRequest(
                "/api/documents"
            );

            currentDocuments = Array.isArray(
                result.documents
            )
                ? result.documents
                : [];

            renderDocuments();

            updateDocumentStatistics();

        } catch (error) {
            console.error(
                "Document loading error:",
                error
            );
        }
    }

    // =========================================================
    // RENDER DOCUMENT LISTS
    // =========================================================

    function renderDocuments() {
        const uniqueDocuments = [];

        const seen = new Set();

        currentDocuments.forEach((doc) => {
            if (!doc || typeof doc !== "object") {
                return;
            }

            const key =
                doc.stored_name ||
                doc.name;

            if (!key || seen.has(key)) {
                return;
            }

            seen.add(key);

            uniqueDocuments.push(doc);
        });

        currentDocuments = uniqueDocuments;

        if (
            selectedDocument &&
            !currentDocuments.some(
                (doc) =>
                    doc.stored_name ===
                    selectedDocument.stored_name
            )
        ) {
            selectedDocument = null;

            updateSelectedDocumentLabel();
        }

        renderDocumentList(
            recentDocumentsList,
            [...uniqueDocuments].reverse().slice(0, 5)
        );

        renderDocumentList(
            documentsContent,
            uniqueDocuments
        );

        renderDocumentList(
            $("#knowledge-content"),
            uniqueDocuments
        );

        renderDocumentList(
            $("#analytics-content"),
            uniqueDocuments
        );

        populateReportDocuments();

        applyDocumentSearch();
    }

    // =========================================================
    // DOCUMENT LIST COMPONENT
    // =========================================================

    function renderDocumentList(
        container,
        documents
    ) {
        if (!container) {
            return;
        }

        container.replaceChildren();

        if (!documents.length) {
            const empty = document.createElement("p");

            empty.className = "empty-message";

            empty.textContent =
                "No documents uploaded yet.";

            container.appendChild(empty);

            return;
        }

        documents.forEach((doc) => {
            const row = document.createElement("button");

            row.type = "button";

            row.className =
                "document-item clickable-document";

            row.title =
                `Select ${doc.name || "this PDF"}`;

            const name = document.createElement("span");

            name.textContent =
                doc.name ||
                doc.stored_name ||
                "Untitled PDF";

            const status = document.createElement("small");

            const size = formatBytes(
                doc.size_bytes
            );

            status.textContent =
                `${doc.status || "Processed"} · ${size}`;

            row.appendChild(name);

            row.appendChild(status);

            row.addEventListener("click", () => {
                selectDocument(doc);
            });

            if (
                selectedDocument &&
                selectedDocument.stored_name ===
                    doc.stored_name
            ) {
                row.classList.add("selected");
            }

            container.appendChild(row);
        });
    }

    // =========================================================
    // DOCUMENT STATISTICS
    // =========================================================

    function updateDocumentStatistics() {
        const total = currentDocuments.length;

        [
            "documents-total",
            "knowledge-documents",
            "analytics-documents"
        ].forEach((id) => {
            setText(
                document.getElementById(id),
                total
            );
        });

        const processed = currentDocuments.filter(
            (doc) => doc.status === "Processed"
        ).length;

        setText(
            $("#documents-processed"),
            processed
        );
    }

    // =========================================================
    // LOAD APPLICATION STATISTICS
    // =========================================================

    async function loadStats() {
        try {
            const stats = await apiRequest(
                "/api/stats"
            );

            const values = {
                "documents-total": stats.documents,
                "documents-processed": stats.documents,
                "knowledge-documents": stats.documents,
                "knowledge-chunks": stats.chunks,
                "analytics-documents": stats.documents,
                "analytics-questions": stats.questions,
                "analytics-uploads": stats.uploads,
                "analytics-chunks": stats.chunks
            };

            Object.entries(values).forEach(
                ([id, value]) => {
                    const element =
                        document.getElementById(id);

                    if (element) {
                        element.textContent =
                            value ?? 0;
                    }
                }
            );

        } catch (error) {
            console.error(
                "Statistics error:",
                error
            );
        }
    }

    // =========================================================
    // REFRESH BUTTONS
    // =========================================================

    [
        "#refresh-documents-btn",
        "#refresh-recent-btn",
        "#settings-refresh-btn"
    ].forEach((selector) => {
        const button = $(selector);

        if (!button) {
            return;
        }

        button.addEventListener(
            "click",
            async () => {
                await loadDocuments();

                await loadStats();

                setUploadStatus(
                    "Document information refreshed."
                );
            }
        );
    });

    // =========================================================
    // DOCUMENT SEARCH
    // =========================================================

    function applyDocumentSearch() {
        const term = (
            documentSearch?.value || ""
        ).trim().toLowerCase();

        $$(".document-item").forEach((item) => {
            item.hidden = !item.textContent
                .toLowerCase()
                .includes(term);
        });
    }

    if (documentSearch) {
        documentSearch.addEventListener(
            "input",
            () => {
                applyDocumentSearch();
            }
        );
    }

    // =========================================================
    // MICROPHONE / SPEECH-TO-TEXT
    // =========================================================

    if (micButton) {
        const SpeechRecognition =
            window.SpeechRecognition ||
            window.webkitSpeechRecognition;

        if (!SpeechRecognition) {
            micButton.addEventListener(
                "click",
                () => {
                    alert(
                        "Speech recognition is unavailable in this browser. " +
                        "Try Microsoft Edge or Google Chrome."
                    );
                }
            );

        } else {
            const recognition =
                new SpeechRecognition();

            recognition.lang = "en-IN";

            recognition.interimResults = false;

            recognition.continuous = false;

            micButton.addEventListener(
                "click",
                () => {
                    try {
                        recognition.start();

                        setChatStatus(
                            "Listening... Please speak."
                        );

                    } catch (error) {
                        console.error(
                            "Microphone error:",
                            error
                        );

                        setChatStatus(
                            "Could not start the microphone."
                        );
                    }
                }
            );

            recognition.onresult = (event) => {
                const transcript =
                    event.results[0][0].transcript;

                if (chatInput) {
                    chatInput.value = transcript;

                    chatInput.focus();
                }

                setChatStatus(
                    "Speech captured. Press Send to ask."
                );
            };

            recognition.onerror = (event) => {
                setChatStatus(
                    "Microphone error: " + event.error
                );
            };

            recognition.onend = () => {
                if (
                    chatStatus &&
                    chatStatus.textContent.startsWith(
                        "Listening"
                    )
                ) {
                    setChatStatus(
                        "Listening stopped."
                    );
                }
            };
        }
    }

    // =========================================================
    // REPORT DOCUMENT DROPDOWN
    // =========================================================

    function populateReportDocuments() {
        if (!reportSelect) {
            return;
        }

        const previousValue = reportSelect.value;

        reportSelect.replaceChildren();

        const allOption =
            document.createElement("option");

        allOption.value = "";

        allOption.textContent =
            "Most recent uploaded document";

        reportSelect.appendChild(
            allOption
        );

        currentDocuments.forEach((doc) => {
            const option =
                document.createElement("option");

            option.value =
                doc.stored_name || doc.name;

            option.textContent =
                doc.name ||
                doc.stored_name;

            reportSelect.appendChild(
                option
            );
        });

        const previousOptionExists = Array.from(
            reportSelect.options
        ).some(
            (option) =>
                option.value === previousValue
        );

        if (previousOptionExists) {
            reportSelect.value = previousValue;
        }
    }

    // =========================================================
    // GENERATE REPORT
    // =========================================================

    async function generateReport() {
        if (isGeneratingReport) {
            return;
        }

        if (!currentDocuments.length) {
            setText(
                reportStatus,
                "Upload a PDF before generating a report."
            );

            return;
        }

        const selectedValue =
            reportSelect?.value || "";

        let documentToReport = null;

        if (selectedValue) {
            documentToReport =
                currentDocuments.find(
                    (doc) =>
                        doc.stored_name === selectedValue ||
                        doc.name === selectedValue
                );

            if (!documentToReport) {
                setText(
                    reportStatus,
                    "The selected document was not found. Refresh and try again."
                );

                return;
            }
        }

        isGeneratingReport = true;

        if (reportButton) {
            reportButton.disabled = true;
        }

        setText(
            reportStatus,
            "Generating your report. Please wait..."
        );

        if (reportOutput) {
            reportOutput.textContent = "";
        }

        try {
            const result = await apiRequest(
                "/api/report",
                {
                    method: "POST",

                    body: JSON.stringify({
                        selected_document:
                            documentToReport
                                ? documentToReport.stored_name
                                : null
                    })
                }
            );

            if (reportOutput) {
                reportOutput.textContent =
                    result.answer ||
                    "No report was returned.";
            }

            setText(
                reportStatus,
                "Report generated successfully."
            );

            const reportSources =
                getSourceText(result.sources);

            const sourcesElement =
                $("#report-sources");

            if (sourcesElement) {
                sourcesElement.textContent =
                    reportSources
                        ? `Sources:\n${reportSources}`
                        : "";
            }

            await loadStats();

        } catch (error) {
            console.error(
                "Report generation error:",
                error
            );

            setText(
                reportStatus,
                "Unable to generate report: " +
                error.message
            );

        } finally {
            isGeneratingReport = false;

            if (reportButton) {
                reportButton.disabled = false;
            }
        }
    }

    if (reportButton) {
        reportButton.addEventListener(
            "click",
            generateReport
        );
    }

    // =========================================================
// INITIALIZE DASHBOARD
// =========================================================

showPage("ai-assistant");
updateSelectedDocumentLabel();
loadDocuments();
loadStats();
loadChatHistory();
});