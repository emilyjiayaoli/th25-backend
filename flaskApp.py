from flask import Flask, request, send_from_directory, jsonify
from flask_sqlalchemy import SQLAlchemy
import os
from datetime import datetime
import PyPDF2
from pydantic import BaseModel
from openai import OpenAI

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///files.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# Ensure upload directory exists
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

client = OpenAI()

# ================== Database Models ==================

class File(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(255), unique=True, nullable=False)
    filepath = db.Column(db.String(255), nullable=False)
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)

# Ensure database is created within an app context
with app.app_context():
    db.create_all()

# Pydantic models for OpenAI structured outputs
class FileMatch(BaseModel):
    filename: str
    matchReason: str
    score: float  # 0.0 to 1.0

class QueryResponse(BaseModel):
    query: str
    answer: str
    relevantFiles: list[FileMatch]

# ================== Helper Functions ==================

def extract_text(filepath: str) -> str:
    text = ""
    if filepath.lower().endswith(".pdf"):
        try:
            with open(filepath, "rb") as f:
                reader = PyPDF2.PdfReader(f)
                for page in reader.pages:
                    page_text = page.extract_text()
                    if page_text:
                        text += page_text + "\n"
        except Exception as e:
            text = f"Error reading PDF: {str(e)}"
    elif filepath.lower().endswith(".txt"):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                text = f.read()
        except Exception as e:
            text = f"Error reading TXT file: {str(e)}"
    else:
        text = "Unsupported file type"
    return text


# ================== API Endpoints ==================

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({"error": "No file provided"}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "Empty filename"}), 400
    
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], file.filename)
    file.save(filepath)
    
    new_file = File(filename=file.filename, filepath=filepath)
    db.session.add(new_file)
    db.session.commit()
    
    return jsonify({"message": "File uploaded successfully", "filename": file.filename})

@app.route('/files/<filename>', methods=['GET'])
def get_file(filename):
    file_record = File.query.filter_by(filename=filename).first()
    if file_record:
        return send_from_directory(app.config['UPLOAD_FOLDER'], filename)
    else:
        return jsonify({"error": "File not found"}), 404
    
# Endpoint: Search for relevant files
# Input: JSON object with a "query" field
# Output: JSON object with the structured response from OpenAI
# Response format:
# {
#   "query": "What is the capital of France?",
#   "answer": "Paris",
#   "relevantFiles": [
#     {
#       "filename": "file1.txt",
#       "matchReason": "Contains the word 'Paris'",
#       "score": 0.8
#     },
#     {
#       "filename": "file2.pdf",
#       "matchReason": "Contains the phrase 'capital city of France'",
#       "score": 0.6
#     }
#   ]
@app.route('/search', methods=['POST'])
def search():
    data = request.json
    query = data.get("query")
    if not query:
        return jsonify({"error": "Query is required"}), 400

    # Retrieve all uploaded files and extract text from each
    files = File.query.all()
    file_texts = []
    for file in files:
        text = extract_text(file.filepath)
        file_texts.append(f"# File: {file.filename}\n{text}\n")

    # Combine all file texts into one string
    combined_text = "\n".join(file_texts)

    # Build the prompt for OpenAI
    prompt = f"""
You are an AI assistant that helps find relevant files based on a query.
You are provided with a query and a list of files with their contents.
Your task is to identify which files are most relevant to the query, and what the answer to the query is
based on the information in the files as well as your own knowledge.

## Query:
{query}

## Files:
{combined_text}
    """

    # Use the beta parse method to obtain a structured response via the QueryResponse model
    completion = client.beta.chat.completions.parse(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "You are a document retrieval assistant."},
            {"role": "user", "content": prompt}
        ],
        response_format=QueryResponse,
    )

    # Get the parsed structured output
    structured_response = completion.choices[0].message.parsed

    return jsonify(structured_response.dict())

if __name__ == '__main__':
    app.run(debug=True)
