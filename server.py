import os
import json
from datetime import datetime
from fastapi import FastAPI, HTTPException, Request, Depends, UploadFile, File, Form
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from typing import Optional, List

import config
import database
import agents
import report_generator
from rag_assistant import RAGAssistant

# Initialize DB on startup
database.init_db()

# Initialize RAG Assistant
rag_instance = RAGAssistant()

app = FastAPI(title="Risen PsyLabs Diagnostics Suite", version="1.0.0")

# Mount static files folder if it exists
os.makedirs("static", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")

templates = Jinja2Templates(directory="templates")
os.makedirs("templates", exist_ok=True)

# Helper function to check completed assessments
def has_completed_any_assessment(user_id):
    profile = agents.SecurityManager.get_patient_profile(user_id)
    if not profile:
        return False
    conn = database.get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT count(*) FROM assessment_sessions WHERE patient_id = ? AND status = 'completed';", (profile["patient_id"],))
    count = cursor.fetchone()[0]
    conn.close()
    return count > 0

# --- API Models ---
class LoginRequest(BaseModel):
    username: str
    password: str

class RegisterRequest(BaseModel):
    username: str
    password: str
    full_name: str
    email: str
    role: str

class ProfileUpdateRequest(BaseModel):
    user_id: int
    dob: str
    gender: str
    phone: str
    consent_given: bool

class MessageRequest(BaseModel):
    user_id: int
    session_id: Optional[int] = None
    message: str

class AssessmentStartRequest(BaseModel):
    user_id: int
    scale_key: str

class AssessmentSubmitRequest(BaseModel):
    user_id: int
    session_id: int
    question_id: str
    answer_text: str
    answer_value: int

class RagChatRequest(BaseModel):
    user_id: int
    message: str

# --- HTML Routes ---
@app.get("/", response_class=HTMLResponse)
async def get_index(request: Request):
    return templates.TemplateResponse(request, "index.html")

@app.get("/portal", response_class=HTMLResponse)
async def get_portal(request: Request):
    return templates.TemplateResponse(request, "portal.html")

@app.get("/clinician", response_class=HTMLResponse)
async def get_clinician(request: Request):
    return templates.TemplateResponse(request, "clinician.html")

@app.get("/admin", response_class=HTMLResponse)
async def get_admin(request: Request):
    return templates.TemplateResponse(request, "admin.html")

# --- Authentication APIs ---
@app.post("/api/auth/login")
async def api_login(req: LoginRequest):
    user = agents.SecurityManager.authenticate_user(req.username, req.password)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid username or password.")
    
    # Check if patient completed assessment
    has_completed = False
    if user["role"] == config.ROLE_PATIENT:
        has_completed = has_completed_any_assessment(user["id"])
        
    return {
        "success": True,
        "user": {
            "id": user["id"],
            "username": user["username"],
            "role": user["role"],
            "full_name": user["full_name"],
            "email": user["email"],
            "has_completed": has_completed
        }
    }

@app.post("/api/auth/register")
async def api_register(req: RegisterRequest):
    if req.role not in [config.ROLE_PATIENT, config.ROLE_PSYCHOLOGIST, config.ROLE_ADMIN]:
        raise HTTPException(status_code=400, detail="Invalid role specified.")
    
    user_id, error = agents.SecurityManager.register_user(
        req.username, req.password, req.role, req.full_name, req.email
    )
    if error:
        raise HTTPException(status_code=400, detail=error)
        
    return {"success": True, "user_id": user_id}

@app.get("/api/auth/profile/{user_id}")
async def api_get_profile(user_id: int):
    profile = agents.SecurityManager.get_patient_profile(user_id)
    if not profile:
        raise HTTPException(status_code=404, detail="User profile not found.")
    return profile

@app.post("/api/auth/profile/update")
async def api_update_profile(req: ProfileUpdateRequest):
    success, msg = agents.SecurityManager.update_patient_profile(
        req.user_id, req.dob, req.gender, req.phone, req.consent_given
    )
    if not success:
        raise HTTPException(status_code=400, detail=msg)
    return {"success": True, "message": msg}

# --- Intake & Diagnostic Chat APIs ---
@app.post("/api/intake/message")
async def api_intake_message(req: MessageRequest):
    profile = agents.SecurityManager.get_patient_profile(req.user_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Patient profile not found.")
        
    # Check for safety / crisis triggers first
    safety = agents.SafetyAgent.check_safety(req.message)
    if safety.get("safety_trigger", False):
        # Insert safety notes into a dummy session or log audit trail
        database.log_audit(req.user_id, "safety_crisis_intercept", "patients", profile["patient_id"])
        
        # Create an abandoned/escalated session record if not exists
        conn = database.get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO assessment_sessions (patient_id, assessment_name, status, score, severity, risk_level, safety_escalated, safety_notes, started_at, completed_at)
            VALUES (?, 'Safety Crisis Intercept', 'abandoned', 0, 'Crisis', 'high', 1, ?, ?, ?)
        """, (profile["patient_id"], safety.get("recommended_response", "Crisis Intercepted"), datetime.now().strftime("%Y-%m-%d %H:%M:%S"), datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()
        conn.close()
        
        return {
            "crisis": True,
            "response": safety.get("recommended_response", "WARNING: Safety concern identified. Please consult a qualified professional or dial 988 immediately.")
        }

    # Retrieve intake chat history from DB or temp state
    # We will query chat_messages for a simulated temp session
    session_id = req.session_id or -1 # -1 denotes intake pre-assessment chat
    
    # Save user message
    try:
        conn = database.get_connection()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO chat_messages (session_id, sender, message) VALUES (?, 'user', ?)",
                       (session_id, req.message))
        conn.commit()
        
        # Get chat history for this user/session to count user messages
        cursor.execute("SELECT sender, message FROM chat_messages WHERE session_id = ? ORDER BY id ASC", (session_id,))
        chat_history = [{"role": r["sender"], "content": r["message"]} for r in cursor.fetchall()]
        conn.close()
    except Exception as e:
        chat_history = [{"role": "user", "content": req.message}]

    user_msgs = [m for m in chat_history if m["role"] == "user"]
    user_msg_count = len(user_msgs)
    
    is_greeting = False
    if user_msg_count > 0:
        first_msg = user_msgs[0]["content"].strip().lower().rstrip(".!?")
        if first_msg in ["hi", "hello", "hey", "hi there", "hello there", "greetings"]:
            is_greeting = True
            
    threshold = 3 if is_greeting else 2
    
    # If threshold reached, recommend scale and trigger redirect
    if user_msg_count >= threshold:
        rec_scale, reason = agents.recommend_assessment_scale(chat_history)
        return {
            "redirect": True,
            "recommended_scale": rec_scale,
            "reason": reason
        }
        
    # Generate bot question
    user_clean = req.message.strip().lower().rstrip(".!?")
    if user_clean in ["hi", "hello", "hey", "hi there", "hello there", "greetings"]:
        ans = "Hi! What seems to be the problem or concern you are experiencing today? Please describe it."
    else:
        chat_str = "\n".join([f"{m['role']}: {m['content']}" for m in chat_history])
        prompt = f"""
You are CareMinds AI, a psychological diagnostic chatbot.
A user is sharing their feelings with you. Ask a single, brief, empathetic follow-up question to help narrow down whether they are experiencing anxiety, depression, general stress, or low well-being.
Keep your response short (1-2 sentences max).

Chat History:
{chat_str}
Assistant:"""
        ans = agents.gemini_client.generate(prompt)

    # Save assistant message
    try:
        conn = database.get_connection()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO chat_messages (session_id, sender, message) VALUES (?, 'assistant', ?)",
                       (session_id, ans))
        conn.commit()
        conn.close()
    except:
        pass

    return {
        "redirect": False,
        "response": ans
    }

# --- Assessment APIs ---
@app.get("/api/assessments/list")
async def api_list_assessments():
    assessments = []
    for k in ["phq9", "gad7", "who5", "pss10"]:
        path = f"data/assessments/{k}.json"
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                assessments.append({
                    "key": k,
                    "name": data["name"],
                    "description": data["description"]
                })
    return assessments

@app.post("/api/assessments/start")
async def api_start_assessment(req: AssessmentStartRequest):
    profile = agents.SecurityManager.get_patient_profile(req.user_id)
    if not profile or not profile.get("consent_given", 0):
        raise HTTPException(status_code=400, detail="You must grant consent in the 'Intake & Consent' section first.")
        
    filepath = f"data/assessments/{req.scale_key}.json"
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="Assessment scale not found.")
        
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)
        
    # Extend with DIRA questions
    dira_path = "data/assessments/dira.json"
    if os.path.exists(dira_path):
        try:
            with open(dira_path, "r", encoding="utf-8") as fd:
                dira_data = json.load(fd)
            data["questions"].extend(dira_data.get("questions", []))
        except Exception as e:
            print(f"Error loading DIRA questions: {e}")

    conn = database.get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO assessment_sessions (patient_id, assessment_name, status, started_at)
        VALUES (?, ?, 'started', ?)
    """, (profile["patient_id"], data["name"], datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    session_id = cursor.lastrowid
    conn.commit()
    
    # Save the initial pre-assessment chat messages to this session in chat_messages
    cursor.execute("""
        UPDATE chat_messages 
        SET session_id = ? 
        WHERE session_id = -1
    """, (session_id,))
    conn.commit()
    conn.close()

    return {
        "success": True,
        "session_id": session_id,
        "scale_name": data["name"],
        "questions": data["questions"],
        "total_questions": len(data["questions"])
    }

@app.post("/api/assessments/submit")
async def api_submit_answer(req: AssessmentSubmitRequest):
    profile = agents.SecurityManager.get_patient_profile(req.user_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Patient profile not found.")
        
    # Check for safety trigger on text answers
    safety = agents.SafetyAgent.check_safety(req.answer_text)
    if safety.get("safety_trigger", False):
        conn = database.get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE assessment_sessions 
            SET status = 'abandoned', safety_escalated = 1, safety_notes = ?, completed_at = ?
            WHERE id = ?
        """, (safety.get("recommended_response", "Crisis detected"), datetime.now().strftime("%Y-%m-%d %H:%M:%S"), req.session_id))
        conn.commit()
        conn.close()
        database.log_audit(req.user_id, "safety_crisis_intercept", "assessment_sessions", req.session_id)
        
        return {
            "crisis": True,
            "response": safety.get("recommended_response", "WARNING: Safety concern identified. Session suspended.")
        }
        
    # Check if option selection is required
    # Call Mapping Agent to verify index if answer_value is negative (meaning conversational text input)
    ans_value = req.answer_value
    
    # Save response
    conn = database.get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO assessment_responses (session_id, question_id, response_value, response_text)
        VALUES (?, ?, ?, ?)
    """, (req.session_id, req.question_id, ans_value, req.answer_text))
    conn.commit()
    conn.close()
    
    return {"success": True}

@app.post("/api/assessments/complete/{session_id}")
async def api_complete_assessment(session_id: int, user_id: int = Form(...)):
    conn = database.get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT assessment_name FROM assessment_sessions WHERE id = ?;", (session_id,))
    sess = cursor.fetchone()
    if not sess:
        conn.close()
        raise HTTPException(status_code=404, detail="Session not found.")
    
    assessment_name = sess["assessment_name"]
    conn.close()
    
    # Map scale key
    scale_key = "phq9"
    if "gad" in assessment_name.lower(): scale_key = "gad7"
    elif "who" in assessment_name.lower(): scale_key = "who5"
    elif "stress" in assessment_name.lower() or "pss" in assessment_name.lower(): scale_key = "pss10"
    
    # Load scale config
    with open(f"data/assessments/{scale_key}.json", "r", encoding="utf-8") as f:
        scale_data = json.load(f)
        
    # Calculate score & severity
    total_score, severity, risk_level = agents.ScoringEngine.score_session(session_id, scale_data)
    
    # Update session
    conn = database.get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE assessment_sessions
        SET status = 'completed', score = ?, severity = ?, risk_level = ?, completed_at = ?
        WHERE id = ?
    """, (total_score, severity, risk_level, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), session_id))
    conn.commit()
    conn.close()
    
    # Run Summarizer & DIRA report generator in background/sync
    try:
        agents.SessionSummarizerAgent.generate_clinical_notes(session_id)
        report_generator.ReportGenerator.generate_assessment_report(session_id)
    except Exception as e:
        print(f"Error compiling reports: {e}")
        
    database.log_audit(user_id, "assessment_completion", "assessment_sessions", session_id)
    
    # Fetch DIRA report if it was created
    conn = database.get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM transformational_reports WHERE session_id = ?;", (session_id,))
    t_report = cursor.fetchone()
    conn.close()
    
    t_data = None
    if t_report:
        t_data = dict(t_report)
        
    return {
        "success": True,
        "score": total_score,
        "severity": severity,
        "risk_level": risk_level,
        "transformational_report": t_data
    }

# --- RAG Wellness Coach Chat API ---
@app.post("/api/rag/chat")
async def api_rag_chat(req: RagChatRequest):
    # Fetch RAG index matches
    matches = rag_instance.search(req.message, top_k=3)
    
    context = ""
    citations = []
    for m in matches:
        context += f"\n---\nSource: {m['source']} (Page {m['page']})\nContent: {m['text']}\n"
        citations.append(f"{m['source']} (Page {m['page']})")
        
    # Preload RAG prompts fallback
    fallback_prompt = "You are CareMinds RAG Wellness Coach. Answer using RAG files and general LLM knowledge."
    instructions = agents.load_prompt_file("rag_prompt.txt", fallback_prompt)
    
    prompt = instructions.format(context=context, query=req.message)
    ans = agents.gemini_client.generate(prompt, "Wellness Coach Grounded RAG Chat")
    
    # Append citations if matched
    if citations:
        ans += "\n\n**Document Citations:**\n" + "\n".join(set(citations))
        
    # Log chat messages in dummy RAG sessions
    try:
        conn = database.get_connection()
        cursor = conn.cursor()
        # Using a dummy session ID -99 for patient RAG conversations
        cursor.execute("INSERT INTO chat_messages (session_id, sender, message) VALUES (-99, 'user', ?)", (req.message,))
        cursor.execute("INSERT INTO chat_messages (session_id, sender, message) VALUES (-99, 'assistant', ?)", (ans,))
        conn.commit()
        conn.close()
    except:
        pass
        
    return {"response": ans}

# --- Clinician Dashboard APIs ---
@app.get("/api/clinician/patients/{clinician_id}")
async def api_clinician_patients(clinician_id: int):
    conn = database.get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT p.id as patient_id, u.full_name, p.dob, p.age, p.gender, p.consent_given 
        FROM patients p 
        JOIN users u ON p.user_id = u.id
        WHERE p.assigned_psychologist_id = ?
    """, (clinician_id,))
    patients = [dict(r) for r in cursor.fetchall()]
    
    # Load safety alerts
    cursor.execute("""
        SELECT s.id, u.full_name, s.assessment_name, s.completed_at, s.safety_notes
        FROM assessment_sessions s
        JOIN patients p ON s.patient_id = p.id
        JOIN users u ON p.user_id = u.id
        WHERE p.assigned_psychologist_id = ? AND s.safety_escalated = 1
        ORDER BY s.completed_at DESC
    """, (clinician_id,))
    alerts = [dict(r) for r in cursor.fetchall()]
    
    # Case distributions (Risk levels & Volumes)
    cursor.execute("""
        SELECT s.risk_level, count(*) 
        FROM assessment_sessions s 
        JOIN patients p ON s.patient_id = p.id 
        WHERE p.assigned_psychologist_id = ? AND s.status = 'completed' 
        GROUP BY s.risk_level
    """, (clinician_id,))
    risk_stats = [{"risk_level": r[0], "count": r[1]} for r in cursor.fetchall()]
    
    cursor.execute("""
        SELECT s.assessment_name, count(*) 
        FROM assessment_sessions s 
        JOIN patients p ON s.patient_id = p.id 
        WHERE p.assigned_psychologist_id = ? AND s.status = 'completed' 
        GROUP BY s.assessment_name
    """, (clinician_id,))
    scale_stats = [{"assessment_name": r[0], "count": r[1]} for r in cursor.fetchall()]
    
    conn.close()
    return {
        "patients": patients,
        "alerts": alerts,
        "stats": {
            "risk_distribution": risk_stats,
            "volume_stats": scale_stats
        }
    }

@app.get("/api/clinician/patient/{patient_id}/sessions")
async def api_patient_sessions(patient_id: int):
    conn = database.get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT s.id, s.assessment_name, s.score, s.severity, s.risk_level, s.completed_at, sum.summary_text, sum.clinician_notes, sum.action_items 
        FROM assessment_sessions s 
        LEFT JOIN session_summaries sum ON s.id = sum.session_id 
        WHERE s.patient_id = ? AND s.status = 'completed'
        ORDER BY s.completed_at DESC
    """, (patient_id,))
    sessions = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return sessions

@app.get("/api/clinician/session/{session_id}/details")
async def api_session_details(session_id: int):
    conn = database.get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM transformational_reports WHERE session_id = ?;", (session_id,))
    t_report = cursor.fetchone()
    
    cursor.execute("""
        SELECT question_id, response_value, response_text 
        FROM assessment_responses 
        WHERE session_id = ? ORDER BY id ASC;
    """, (session_id,))
    responses = [dict(r) for r in cursor.fetchall()]
    conn.close()
    
    t_data = dict(t_report) if t_report else None
    return {
        "transformational_report": t_data,
        "responses": responses
    }

# --- PDF Report Downloader ---
@app.get("/api/reports/download/{session_id}")
async def download_report_pdf(session_id: int):
    pdf_path = f"reports/session_{session_id}_report.pdf"
    if not os.path.exists(pdf_path):
        # Generate the PDF if it doesn't exist yet
        try:
            report_generator.ReportGenerator.generate_assessment_report(session_id)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to generate report: {e}")
            
    if os.path.exists(pdf_path):
        return FileResponse(pdf_path, media_type="application/pdf", filename=f"report_{session_id}.pdf")
    else:
        raise HTTPException(status_code=404, detail="Clinical PDF report file could not be generated.")

# --- Admin PDF Uploader & Vector Indexer API ---
@app.post("/api/admin/upload")
async def admin_upload_pdf(
    category: str = Form(...),
    user_id: int = Form(...),
    file: UploadFile = File(...)
):
    dest_folder = f"knowledge_base/{category}"
    os.makedirs(dest_folder, exist_ok=True)
    dest_path = os.path.join(dest_folder, file.filename)
    
    with open(dest_path, "wb") as f:
        f.write(await file.read())
        
    database.log_audit(user_id, f"document_upload_{category}", "faiss_index", 0)
    
    # Index the file
    chunks = rag_instance.add_pdf(dest_path)
    return {
        "success": True,
        "filename": file.filename,
        "category": category,
        "chunks_indexed": chunks
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8501)
