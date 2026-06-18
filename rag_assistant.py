import os
import hashlib
import json
import pypdf
from langchain_community.vectorstores import FAISS
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

class RAGAssistant:
    def __init__(self, index_dir="data/faiss_index", index_file="data/indexed_files.json"):
        self.index_dir = index_dir
        self.index_file = index_file
        self.embeddings = HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")
        self.db = None
        self.indexed_files = {}
        self.load_indexed_files_tracker()
        self.load_index()
        self.auto_index_knowledge_base()

    def auto_index_knowledge_base(self):
        kb_dir = "knowledge_base"
        if os.path.exists(kb_dir):
            for root, dirs, files in os.walk(kb_dir):
                for file in files:
                    if file.lower().endswith(".pdf"):
                        pdf_path = os.path.join(root, file)
                        try:
                            self.add_pdf(pdf_path)
                        except Exception as e:
                            print(f"Error auto-indexing {pdf_path}: {e}")

    def get_file_hash(self, filepath):
        hasher = hashlib.md5()
        with open(filepath, 'rb') as f:
            buf = f.read()
            hasher.update(buf)
        return hasher.hexdigest()

    def load_indexed_files_tracker(self):
        if os.path.exists(self.index_file):
            try:
                with open(self.index_file, 'r', encoding='utf-8') as f:
                    self.indexed_files = json.load(f)
            except Exception:
                self.indexed_files = {}
        else:
            self.indexed_files = {}

    def save_indexed_files_tracker(self):
        os.makedirs(os.path.dirname(self.index_file), exist_ok=True)
        with open(self.index_file, 'w', encoding='utf-8') as f:
            json.dump(self.indexed_files, f, indent=4)

    def load_index(self):
        if os.path.exists(os.path.join(self.index_dir, "index.faiss")):
            try:
                self.db = FAISS.load_local(self.index_dir, self.embeddings, allow_dangerous_deserialization=True)
                print("LangChain FAISS index loaded successfully.")
            except Exception as e:
                print(f"Error loading FAISS: {e}")
                self.db = None
        else:
            print("No FAISS index found. Ready for indexing.")

    def add_pdf(self, pdf_path):
        filename = os.path.basename(pdf_path)
        file_hash = self.get_file_hash(pdf_path)
        
        if filename in self.indexed_files and self.indexed_files[filename] == file_hash:
            print(f"File {filename} is already indexed. Skipping.")
            return 0
            
        pages = []
        try:
            reader = pypdf.PdfReader(pdf_path)
            for i, page in enumerate(reader.pages):
                text = page.extract_text()
                if text and len(text.strip()) > 10:
                    pages.append((text.strip(), i + 1))
        except Exception as e:
            print(f"Error reading PDF: {e}")
            return 0
            
        if not pages:
            return 0
            
        text_splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=50)
        docs = []
        for text, page_num in pages:
            chunks = text_splitter.split_text(text)
            for chunk in chunks:
                if len(chunk.strip()) > 20:
                    from langchain_core.documents import Document
                    docs.append(Document(
                        page_content=chunk,
                        metadata={"source": filename, "page": page_num}
                    ))
                    
        if not docs:
            return 0
            
        if self.db is None:
            self.db = FAISS.from_documents(docs, self.embeddings)
        else:
            self.db.add_documents(docs)
            
        self.db.save_local(self.index_dir)
        self.indexed_files[filename] = file_hash
        self.save_indexed_files_tracker()
        return len(docs)

    def search(self, query, top_k=3):
        if self.db is None:
            return []
        try:
            results = self.db.similarity_search_with_score(query, k=top_k)
            matches = []
            for doc, score in results:
                matches.append({
                    "text": doc.page_content,
                    "page": doc.metadata.get("page", 1),
                    "source": doc.metadata.get("source", "Unknown"),
                    "distance": float(score)
                })
            return matches
        except Exception:
            return []
