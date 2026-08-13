import os
from langchain_community.document_loaders import PyPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.vectorstores import FAISS
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_ollama import ChatOllama
from langchain.chains.retrieval import create_retrieval_chain
from langchain.chains.combine_documents import create_stuff_documents_chain
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

# Initialize paths and global embeddings
DB_FAISS_PATH = 'vectorstore/db_faiss'
os.makedirs('vectorstore', exist_ok=True)

embeddings = HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")

def ingest_pdf(file_path):
    try:
        loader = PyPDFLoader(file_path)
        documents = loader.load()
        text_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)
        texts = text_splitter.split_documents(documents)
        db = FAISS.from_documents(texts, embeddings)
        db.save_local(DB_FAISS_PATH)
        return True, "Success"
    except Exception as e:
        import traceback
        print(f"DEBUG: {traceback.format_exc()}")
        return False, str(e)

# Updated: Added 'chat_history' parameter
def get_ai_response(query, chat_history=[]):
    try:
        db = FAISS.load_local(DB_FAISS_PATH, embeddings, allow_dangerous_deserialization=True)
        retriever = db.as_retriever(search_kwargs={"k": 3}) # Retrieve top 3 chunks
        
        llm = ChatOllama(model="llama3.2")
        
        system_prompt = (
            "You are an expert Enterprise Knowledge Assistant. "
            "Use the retrieved context to answer the question. "
            "If the info is not in the context, say so. "
            "ALWAYS cite the source filename provided in the context metadata."
            "\n\nContext: {context}"
        )
        
        prompt = ChatPromptTemplate.from_messages([
            ("system", system_prompt),
            MessagesPlaceholder(variable_name="chat_history"),
            ("human", "{input}"),
        ])
        
        combine_docs_chain = create_stuff_documents_chain(llm, prompt)
        retrieval_chain = create_retrieval_chain(retriever, combine_docs_chain)
        
        response = retrieval_chain.invoke({
            "input": query,
            "chat_history": chat_history
        })
        
        # --- NEW: Extract unique sources ---
        sources = set(doc.metadata.get('source', 'Unknown') for doc in response['context'])
        source_text = ", ".join(sources)
        
        return response["answer"], f"Sources: {source_text}"
        
    except Exception as e:
        import traceback
        print(f"!!! ERROR !!!\n{traceback.format_exc()}")
        return "System error: Unable to process request.", None