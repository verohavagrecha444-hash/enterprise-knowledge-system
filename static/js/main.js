document.addEventListener('DOMContentLoaded', () => {
    // UI Elements
    const uploadBtn = document.getElementById('upload-btn');
    const fileInput = document.getElementById('pdf-upload');
    const uploadStatus = document.getElementById('upload-status');
    const sendBtn = document.getElementById('send-btn');
    const userInput = document.getElementById('user-input');
    const chatHistory = document.getElementById('chat-history');
    const micBtn = document.getElementById('mic-btn');

    // Voice Setup
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    const recognition = SpeechRecognition ? new SpeechRecognition() : null;

    if (recognition) {
        recognition.lang = 'en-US';
        micBtn.addEventListener('click', () => {
            micBtn.style.color = "#3b82f6";
            recognition.start();
        });
        recognition.onresult = (event) => {
            userInput.value = event.results[0][0].transcript;
            micBtn.style.color = "white";
        };
    }

    // --- SIDEBAR NAVIGATION (Screen Switching) ---
    const navLinks = document.querySelectorAll('.nav-link');
    const viewSections = document.querySelectorAll('.view-section');

    navLinks.forEach(link => {
        link.addEventListener('click', (e) => {
            e.preventDefault();
            navLinks.forEach(n => n.classList.remove('active'));
            link.classList.add('active');

            const page = link.getAttribute('data-page');

            // Hide all view panels
            viewSections.forEach(section => {
                section.style.display = 'none';
            });

            // Show target view panel
            const targetView = document.getElementById('view-' + page);
            if (targetView) {
                targetView.style.display = 'flex';
                targetView.style.flexDirection = 'column';
                targetView.style.flex = '1';
                targetView.style.overflowY = 'auto';
            }

            // Update Header Titles dynamically
            const headerTitle = document.getElementById('view-title');
            const headerSubtitle = document.getElementById('view-subtitle');

            if (page === 'ai-assistant') {
                if (headerTitle) headerTitle.textContent = "AI Document Assistant";
                if (headerSubtitle) headerSubtitle.textContent = "Your Intelligent Knowledge Partner";
            } else if (page === 'documents') {
                if (headerTitle) headerTitle.textContent = "Managed Documents";
                if (headerSubtitle) headerSubtitle.textContent = "View and audit uploaded enterprise files";
            } else if (page === 'knowledge') {
                if (headerTitle) headerTitle.textContent = "Knowledge Base";
                if (headerSubtitle) headerSubtitle.textContent = "Embedded vector index summary";
            } else if (page === 'analytics') {
                if (headerTitle) headerTitle.textContent = "System Analytics";
                if (headerSubtitle) headerSubtitle.textContent = "Query performance and processing metrics";
            } else if (page === 'reports') {
                if (headerTitle) headerTitle.textContent = "Generated Reports";
                if (headerSubtitle) headerSubtitle.textContent = "Exported summaries and audits";
            } else if (page === 'settings') {
                if (headerTitle) headerTitle.textContent = "Platform Settings";
                if (headerSubtitle) headerSubtitle.textContent = "Configure environment parameters";
            }
        });
    });

    // --- QUICK ACTIONS ---
    window.sendQuickAction = function(text) {
        userInput.value = text;
        askQuestion();
    };

    // --- UPLOAD ---
    uploadBtn.addEventListener('click', async () => {
        const file = fileInput.files[0];
        if (!file) return (uploadStatus.textContent = "Select a PDF first.");

        const formData = new FormData();
        formData.append('file', file);
        uploadStatus.textContent = "Uploading...";

        try {
            const response = await fetch('/upload', { method: 'POST', body: formData });
            const data = await response.json();
            if (response.ok) {
                uploadStatus.textContent = "Upload successful!";
                const list = document.getElementById('recent-documents-list');
                if (list) list.insertAdjacentHTML('beforeend', `<div class="doc-item">📄 ${file.name}</div>`);
            } else {
                uploadStatus.textContent = "Error: " + data.error;
            }
        } catch (e) {
            uploadStatus.textContent = "Connection error.";
        }
    });

    // --- CHAT ---
    sendBtn.addEventListener('click', () => askQuestion());
    userInput.addEventListener('keypress', (e) => { if (e.key === 'Enter') askQuestion(); });

    async function askQuestion() {
        const text = userInput.value.trim();
        if (!text) return;

        appendMessage(text, 'user-msg');
        userInput.value = '';
        const loadingId = appendMessage("Thinking...", 'ai-msg');

        try {
            const response = await fetch('/ask', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ query: text })
            });
            const data = await response.json();
            const aiMessageDiv = document.getElementById(loadingId);
            if (response.ok) {
                aiMessageDiv.innerHTML = `<p>${data.answer}</p><div class="source-badge" style="font-size: 0.75rem; color: var(--text-secondary); margin-top: 8px;">Source: ${data.source}</div>`;
            } else {
                aiMessageDiv.innerHTML = `<p style="color:#ff6b6b;">Error: ${data.error}</p>`;
            }
        } catch (e) {
            document.getElementById(loadingId).innerHTML = `<p style="color:#ff6b6b;">Server unreachable.</p>`;
        }
        chatHistory.scrollTop = chatHistory.scrollHeight;
    }

    function appendMessage(text, className) {
        const msgDiv = document.createElement('div');
        msgDiv.className = `message ${className}`;
        const uniqueId = 'msg-' + Date.now();
        msgDiv.id = uniqueId;
        msgDiv.innerHTML = `<p>${text}</p>`;
        chatHistory.appendChild(msgDiv);
        chatHistory.scrollTop = chatHistory.scrollHeight;
        return uniqueId;
    }
});