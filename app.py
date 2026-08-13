import os
from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
from dotenv import load_dotenv

# Import message types for memory
from langchain_core.messages import HumanMessage, AIMessage

# IMPORT OUR NEW FAST RAG ENGINE
from rag_engine import ingest_pdf, get_ai_response

load_dotenv()

app = Flask(__name__)
CORS(app)

# Global memory storage
chat_history = []

UPLOAD_FOLDER = 'uploads'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload_document():
    # Clear history when a new document is uploaded so context remains relevant
    global chat_history
    chat_history = []
    
    if 'file' not in request.files:
        return jsonify({"error": "No file detected"}), 400
        
    file = request.files['file']
    
    if file.filename == '':
        return jsonify({"error": "No file selected"}), 400
        
    if file and file.filename.endswith('.pdf'):
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], file.filename)
        file.save(filepath)
        
        success, msg = ingest_pdf(filepath)
        
        if success:
            return jsonify({"message": f"Document '{file.filename}' processed and indexed.", "status": "success"}), 200
        else:
            return jsonify({"error": f"Failed to process PDF: {msg}"}), 500
    else:
        return jsonify({"error": "System strictly supports PDF files."}), 400

@app.route('/ask', methods=['POST'])
def ask_question():
    global chat_history
    data = request.get_json()
    user_query = data.get('query')
    
    if not user_query:
        return jsonify({"error": "Query cannot be empty"}), 400
        
    # --- TRIGGER: GET AI RESPONSE WITH HISTORY ---
    answer, source = get_ai_response(user_query, chat_history)
    
    # Update memory
    chat_history.append(HumanMessage(content=user_query))
    chat_history.append(AIMessage(content=answer))
    
    # Optional: Keep history to last 10 messages to save memory
    if len(chat_history) > 10:
        chat_history = chat_history[-10:]
    
    return jsonify({
        "answer": answer,
        "source": source,
        "status": "success"
    }), 200

if __name__ == '__main__':
    app.run(debug=True, port=5000, host='0.0.0.0')