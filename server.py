"""
CareMinds AI Platform — FastAPI REST Backend
=============================================
Replaces the Streamlit app.py with a stateless REST API.
All business logic is delegated to the existing modules:
  agents, database, config, rag_assistant
"""

import json
import os
import uuid
import shutil
from datetime import datetime
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, Header, Depends, Request, Form, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import config
import database
import agents
from rag_assistant import RAGAssistant

# ---------------------------------------------------------------------------
# Pydantic request / response models
# ---------------------------------------------------------------------------

class RegisterRequest(BaseModel):
    username: str
    password: str
    full_name: str
    email: str
    role: str = config.ROLE_PATIENT

class LoginRequest(BaseModel):
    username: str
    password: str

class ProfileUpdateRequest(BaseModel):
    dob: str
    gender: str
    phone: str
    consent_given: bool

class IntakeChatRequest(BaseModel):
    message: str

class StartAssessmentRequest(BaseModel):
    scale_key: str  # phq9, gad7, who5, pss10
    user_id: Optional[int] = None


class AssessmentRespondRequest(BaseModel):
    answer: str

class CoachChatRequest(BaseModel):
    message: str
    token: Optional[str] = None

class AuthProfileUpdateRequest(BaseModel):
    user_id: int
    dob: str
    gender: str
    phone: str
    consent_given: bool

class AuthIntakeChatRequest(BaseModel):
    user_id: int
    message: str

class AuthStartAssessmentRequest(BaseModel):
    user_id: int
    scale_key: str

class AuthSubmitAnswerRequest(BaseModel):
    user_id: int
    session_id: int
    question_id: str
    answer_text: str
    answer_value: int

class AuthCoachChatRequest(BaseModel):
    user_id: int
    message: str


# ---------------------------------------------------------------------------
# In-memory session stores  (token → data)
# ---------------------------------------------------------------------------

# auth_sessions: token → user dict (from DB row)
auth_sessions: dict[str, dict] = {}

# intake_sessions: token → { "chat_history": [...], "redirected": bool, "recommended_scale": str|None }
intake_sessions: dict[str, dict] = {}

# assessment_sessions: token → { "active": bool, "scale_key": str, "q_idx": int,
#     "session_id": int, "answers": {}, "questions": [...], "scale_data": {} }
assessment_sessions: dict[str, dict] = {}

# coach_sessions: token → { "chat_history": [...] }
coach_sessions: dict[str, dict] = {}

# Global RAG assistant instance
rag_assistant_instance: RAGAssistant | None = None

# ---------------------------------------------------------------------------
# Lifespan: initialise DB & RAG on startup
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    global rag_assistant_instance
    print("[startup] Initialising database …")
    database.init_db()
    print("[startup] Initialising RAG assistant …")
    rag_assistant_instance = RAGAssistant()
    print("[startup] Ready.")
    yield
    print("[shutdown] Cleaning up.")

# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="CareMinds AI Platform API",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost", "http://localhost:8000", "http://127.0.0.1", "http://127.0.0.1:8000", "*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Auth dependency
# ---------------------------------------------------------------------------

def get_current_user(authorization: Optional[str] = Header(None)) -> dict:
    """Extract bearer token and return user dict or raise 401."""
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Invalid Authorization header format. Use: Bearer <token>")
    user = auth_sessions.get(token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return {**user, "_token": token}

# ---------------------------------------------------------------------------
# Helper: save chat message to DB
# ---------------------------------------------------------------------------

def save_chat(session_id: int, sender: str, msg: str):
    try:
        conn = database.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO chat_messages (session_id, sender, message) VALUES (?, ?, ?)",
            (session_id, sender, msg),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass

# ---------------------------------------------------------------------------
# Helper: check if user has completed at least one assessment
# ---------------------------------------------------------------------------

def has_completed_any_assessment(user_id: int) -> bool:
    try:
        profile = agents.SecurityManager.get_patient_profile(user_id)
        if not profile or not profile.get("patient_id"):
            return False
        conn = database.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT count(*) FROM assessment_sessions WHERE patient_id = ? AND status = 'completed'",
            (profile["patient_id"],),
        )
        count = cursor.fetchone()[0]
        conn.close()
        return count > 0
    except Exception:
        return False

# ---------------------------------------------------------------------------
# Helper: load assessment JSON + append DIRA
# ---------------------------------------------------------------------------

VALID_SCALES = {"phq9", "gad7", "who5", "pss10"}

def load_scale_data(scale_key: str) -> dict:
    filepath = f"data/assessments/{scale_key}.json"
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail=f"Assessment scale '{scale_key}' not found")
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)
    # Append DIRA Layer-2 questions (mirrors app.py logic)
    if scale_key in VALID_SCALES:
        dira_path = "data/assessments/dira.json"
        if os.path.exists(dira_path):
            try:
                with open(dira_path, "r", encoding="utf-8") as fd:
                    dira_data = json.load(fd)
                data["questions"].extend(dira_data.get("questions", []))
            except Exception as e:
                print(f"Error loading DIRA questions: {e}")
    return data

# ---------------------------------------------------------------------------
# Default RAG prompt (same as app.py)
# ---------------------------------------------------------------------------

DEFAULT_RAG_PROMPT = """You are CareMinds AI, a supportive Advanced Transformational Wellness Coach.

Your responsibilities:
- Answer the user's query by blending the retrieved context with your general psychological and coaching knowledge.
- Prioritize using retrieved context when it is relevant to the user's query, and cite the document names and pages.
- If the retrieved context is not relevant or does not contain the answer, use your own coaching, CBT, and general knowledge to provide a helpful, empathetic, and reflective answer.
- Always maintain a supportive, non-diagnostic, compassionate tone.

Retrieved Context:
{context}

User Query:
{query}

Format your response in a supportive conversational style. Provide citations at the end if you referenced specific documents.
"""

# ===========================  ENDPOINTS  ===================================

# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health_check():
    return {"status": "ok", "timestamp": datetime.now().isoformat()}

# ---------------------------------------------------------------------------
# Auth: Register
# ---------------------------------------------------------------------------

@app.post("/api/register")
@app.post("/api/auth/register")
def register(req: RegisterRequest):
    if not req.username or not req.password:
        raise HTTPException(status_code=400, detail="Username and password are required")
    uid, err = agents.SecurityManager.register_user(
        req.username, req.password, req.role, req.full_name, req.email
    )
    if err:
        raise HTTPException(status_code=409, detail=err)
    return {"user_id": uid, "message": "Registration successful. Please log in."}

# ---------------------------------------------------------------------------
# Auth: Login
# ---------------------------------------------------------------------------

@app.post("/api/login")
@app.post("/api/auth/login")
def login(req: LoginRequest):
    user = agents.SecurityManager.authenticate_user(req.username, req.password)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    token = uuid.uuid4().hex
    # Strip password hash before storing
    safe_user = {k: v for k, v in user.items() if k != "password_hash"}
    auth_sessions[token] = safe_user
    return {"token": token, "user": safe_user}

# ---------------------------------------------------------------------------
# Profile: GET
# ---------------------------------------------------------------------------

@app.get("/api/profile")
def get_profile(user: dict = Depends(get_current_user)):
    profile = agents.SecurityManager.get_patient_profile(user["id"])
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    return dict(profile)

# ---------------------------------------------------------------------------
# Profile: POST (update)
# ---------------------------------------------------------------------------

@app.post("/api/profile")
def update_profile(req: ProfileUpdateRequest, user: dict = Depends(get_current_user)):
    success, msg = agents.SecurityManager.update_patient_profile(
        user["id"], req.dob, req.gender, req.phone, req.consent_given
    )
    if not success:
        raise HTTPException(status_code=400, detail=msg)
    return {"message": msg}

# ---------------------------------------------------------------------------
# Intake Chat (diagnostic → auto-redirect to assessment)
# ---------------------------------------------------------------------------

@app.post("/api/chat/intake")
def intake_chat(req: IntakeChatRequest, user: dict = Depends(get_current_user)):
    token = user["_token"]

    # Initialise per-token intake session if needed
    if token not in intake_sessions:
        intake_sessions[token] = {
            "chat_history": [
                {"role": "assistant", "content": "Hello! I am CareMinds AI. How can I support you today? Please say hello or type 'hi' to get started."}
            ],
            "redirected": False,
            "recommended_scale": None,
            "recommended_reason": None,
        }

    session = intake_sessions[token]
    if session["redirected"]:
        return {
            "reply": None,
            "redirect": True,
            "recommended_scale": session["recommended_scale"],
            "recommended_reason": session["recommended_reason"],
            "chat_history": session["chat_history"],
        }

    # Add user message
    session["chat_history"].append({"role": "user", "content": req.message})

    # Count user messages & compute threshold (mirrors app.py)
    user_msgs = [m for m in session["chat_history"] if m["role"] == "user"]
    user_msg_count = len(user_msgs)

    is_greeting = False
    if user_msg_count > 0:
        first = user_msgs[0]["content"].strip().lower().rstrip(".!?")
        if first in ["hi", "hello", "hey", "hi there", "hello there", "greetings"]:
            is_greeting = True
    threshold = 3 if is_greeting else 2

    # If threshold reached → recommend scale & flag redirect
    if user_msg_count >= threshold:
        rec_scale, reason = agents.recommend_assessment_scale(session["chat_history"])
        session["redirected"] = True
        session["recommended_scale"] = rec_scale
        session["recommended_reason"] = reason
        return {
            "reply": None,
            "redirect": True,
            "recommended_scale": rec_scale,
            "recommended_reason": reason,
            "chat_history": session["chat_history"],
        }

    # Otherwise generate follow-up
    user_clean = req.message.strip().lower().rstrip(".!?")
    if user_clean in ["hi", "hello", "hey", "hi there", "hello there", "greetings"]:
        reply = "Hi! What seems to be the problem or concern you are experiencing today? Please describe it."
    else:
        chat_str = ""
        for msg in session["chat_history"]:
            chat_str += f"{msg['role']}: {msg['content']}\n"
        prompt = f"""You are CareMinds AI, a psychological diagnostic chatbot.
A user is sharing their feelings with you. Ask a single, brief, empathetic follow-up question to help narrow down whether they are experiencing anxiety, depression, general stress, or low well-being.
Keep your response short (1-2 sentences max).

Chat History:
{chat_str}
Assistant:"""
        reply = agents.gemini_client.generate(prompt)

    session["chat_history"].append({"role": "assistant", "content": reply})

    return {
        "reply": reply,
        "redirect": False,
        "recommended_scale": None,
        "recommended_reason": None,
        "chat_history": session["chat_history"],
    }

# ---------------------------------------------------------------------------
# Assessments: List available scales
# ---------------------------------------------------------------------------

@app.get("/api/assessments")
def list_assessments(user: dict = Depends(get_current_user)):
    scales = []
    for key in VALID_SCALES:
        filepath = f"data/assessments/{key}.json"
        if os.path.exists(filepath):
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            scales.append({
                "key": key,
                "name": data.get("name", key),
                "description": data.get("description", ""),
            })
    return {"assessments": scales}

# ---------------------------------------------------------------------------
# Assessments: Start
# ---------------------------------------------------------------------------

@app.post("/api/assessments/start")
@app.post("/api/assessments/compat/start")
def start_assessment(req: StartAssessmentRequest, authorization: Optional[str] = Header(None)):
    token = None
    user_id = req.user_id
    if authorization:
        scheme, _, tok = authorization.partition(" ")
        if scheme.lower() == "bearer" and tok:
            user = auth_sessions.get(tok)
            if user:
                user_id = user["id"]
                token = tok

    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")

    key = req.scale_key.lower()
    if key not in VALID_SCALES:
        raise HTTPException(status_code=400, detail=f"Invalid scale key '{key}'. Must be one of {VALID_SCALES}")

    profile = agents.SecurityManager.get_patient_profile(user_id)
    if not profile or not profile.get("consent_given", 0):
        raise HTTPException(status_code=403, detail="You must complete your profile and grant consent before starting an assessment.")

    data = load_scale_data(key)

    conn = database.get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO assessment_sessions (patient_id, assessment_name, status, started_at) VALUES (?, ?, 'started', ?)",
        (profile["patient_id"], data["name"], datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
    )
    sid = cursor.lastrowid
    conn.commit()
    conn.close()

    database.log_audit(user_id, f"started_assessment_{key}", "assessment_sessions", sid)

    if token:
        # Token-based flow
        if token in intake_sessions:
            for msg in intake_sessions[token].get("chat_history", []):
                save_chat(sid, msg["role"], msg["content"])

        assessment_sessions[token] = {
            "active": True,
            "scale_key": key,
            "q_idx": 0,
            "session_id": sid,
            "answers": {},
            "questions": data["questions"],
            "scale_data": data,
        }

        first_q = data["questions"][0]
        is_dira = first_q["id"].startswith("dira_")
        total_clinical = len([q for q in data["questions"] if not q["id"].startswith("dira_")])
        total_dira = len([q for q in data["questions"] if q["id"].startswith("dira_")])

        return {
            "session_id": sid,
            "assessment_name": data["name"],
            "total_questions": len(data["questions"]),
            "total_clinical": total_clinical,
            "total_dira": total_dira,
            "current_question": {
                "index": 0,
                "id": first_q["id"],
                "text": first_q["text"],
                "options": first_q["options"],
                "is_dira": is_dira,
                "is_open_ended": (len(first_q["options"]) == 1 and first_q["options"][0] == "Open-ended response") or (not first_q["options"]),
            },
        }
    else:
        # Compatibility / User-ID flow
        if user_id in intake_sessions_by_user:
            for msg in intake_sessions_by_user[user_id].get("chat_history", []):
                save_chat(sid, msg["role"], msg["content"])

        assessment_sessions_by_user[user_id] = {
            "active": True,
            "scale_key": key,
            "session_id": sid,
            "answers": {},
            "questions": data["questions"],
            "scale_data": data,
        }

        return {
            "session_id": sid,
            "scale_name": data["name"],
            "questions": data["questions"]
        }


# ---------------------------------------------------------------------------
# Assessments: Respond to current question
# ---------------------------------------------------------------------------

@app.post("/api/assessments/respond")
def assessment_respond(req: AssessmentRespondRequest, user: dict = Depends(get_current_user)):
    token = user["_token"]
    state = assessment_sessions.get(token)
    if not state or not state.get("active"):
        raise HTTPException(status_code=400, detail="No active assessment session. Please start one first.")

    questions = state["questions"]
    q_idx = state["q_idx"]

    if q_idx >= len(questions):
        raise HTTPException(status_code=400, detail="Assessment already completed.")

    q = questions[q_idx]
    final_ans = req.answer.strip()
    if not final_ans:
        raise HTTPException(status_code=400, detail="Answer cannot be empty.")

    # --- Safety check ---
    safety = agents.SafetyAgent.check_safety(final_ans)
    if safety.get("safety_trigger", False):
        conn = database.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """UPDATE assessment_sessions
               SET status = 'completed', score = 0, severity = 'Crisis Distress',
                   risk_level = 'high', safety_escalated = 1, safety_notes = ?, completed_at = ?
               WHERE id = ?""",
            (
                f"Self-harm trigger word detected: '{final_ans}'",
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                state["session_id"],
            ),
        )
        conn.commit()
        conn.close()
        database.log_audit(user["id"], "safety_crisis_alert", "assessment_sessions", state["session_id"])
        state["active"] = False
        return {
            "crisis": True,
            "crisis_message": safety.get("recommended_response", ""),
            "safety_details": safety,
            "completed": True,
            "next_question": None,
        }

    # Log user reply to chat memory
    save_chat(state["session_id"], "user", final_ans)

    is_open_ended = (len(q["options"]) == 1 and q["options"][0] == "Open-ended response") or (not q["options"])

    if is_open_ended:
        val = 0
        opt_txt = final_ans
    else:
        mapping = agents.AssessmentAgent.map_user_response(final_ans, q["options"], q["scores"])
        m_idx = mapping.get("matched_index", -1)
        if m_idx == -1:
            # Check for "Other" option
            other_idx = -1
            for idx, opt in enumerate(q["options"]):
                if "other" in opt.lower() or "free text" in opt.lower():
                    other_idx = idx
                    break
            m_idx = other_idx if other_idx != -1 else 0

        if final_ans not in q["options"]:
            opt_txt = final_ans
        else:
            opt_txt = q["options"][m_idx]
        val = q["scores"][m_idx]

    # Save response to DB
    conn = database.get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO assessment_responses (session_id, question_id, response_value, response_text) VALUES (?, ?, ?, ?)",
        (state["session_id"], q["id"], val, opt_txt),
    )
    conn.commit()
    conn.close()

    ack = f"Acknowledged reflection: {opt_txt[:60]}..." if is_open_ended else f"Acknowledged response: {opt_txt}"
    save_chat(state["session_id"], "assistant", ack)

    state["answers"][q["id"]] = val
    state["q_idx"] += 1
    new_idx = state["q_idx"]

    # --- Check if assessment is now complete ---
    if new_idx >= len(questions):
        # Score
        score, severity, risk = agents.ScoringEngine.score_session(state["session_id"], state["scale_data"])
        conn = database.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """UPDATE assessment_sessions
               SET status = 'completed', score = ?, severity = ?, risk_level = ?, completed_at = ?
               WHERE id = ?""",
            (score, severity, risk, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), state["session_id"]),
        )
        conn.commit()
        conn.close()
        database.log_audit(user["id"], f"completed_{state['scale_key']}", "assessment_sessions", state["session_id"])

        # Clinical notes + DIRA analysis
        notes_result = agents.SessionSummarizerAgent.generate_clinical_notes(state["session_id"])
        summary_text, soap_notes, action_items = ("", "", "")
        if notes_result:
            summary_text, soap_notes, action_items = notes_result

        # Fetch DIRA transformational report if generated
        dira_report = None
        try:
            conn = database.get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM transformational_reports WHERE session_id = ?", (state["session_id"],))
            t_row = cursor.fetchone()
            conn.close()
            if t_row:
                dira_report = dict(t_row)
        except Exception:
            pass

        state["active"] = False

        return {
            "crisis": False,
            "completed": True,
            "next_question": None,
            "result": {
                "session_id": state["session_id"],
                "assessment_name": state["scale_data"]["name"],
                "score": score,
                "severity": severity,
                "risk_level": risk,
                "summary_text": summary_text,
                "soap_notes": soap_notes,
                "action_items": action_items,
                "dira_report": dira_report,
            },
        }

    # --- Return next question ---
    next_q = questions[new_idx]
    is_dira = next_q["id"].startswith("dira_")
    is_next_open = (len(next_q["options"]) == 1 and next_q["options"][0] == "Open-ended response") or (not next_q["options"])

    # Calculate question numbering
    if is_dira:
        dira_qids = [quest["id"] for quest in questions if quest["id"].startswith("dira_")]
        try:
            dira_num = dira_qids.index(next_q["id"]) + 1
        except ValueError:
            dira_num = new_idx + 1
        question_label = f"DIRA Question {dira_num} of {len(dira_qids)}"
    else:
        clinical_qids = [quest["id"] for quest in questions if not quest["id"].startswith("dira_")]
        question_label = f"Question {new_idx + 1} of {len(clinical_qids)}"

    return {
        "crisis": False,
        "completed": False,
        "acknowledged": ack,
        "next_question": {
            "index": new_idx,
            "id": next_q["id"],
            "text": next_q["text"],
            "options": next_q["options"],
            "is_dira": is_dira,
            "is_open_ended": is_next_open,
            "label": question_label,
        },
    }

# ---------------------------------------------------------------------------
# Assessments: History
# ---------------------------------------------------------------------------

@app.get("/api/assessments/history")
def assessment_history(user: dict = Depends(get_current_user)):
    profile = agents.SecurityManager.get_patient_profile(user["id"])
    if not profile or not profile.get("patient_id"):
        return {"sessions": []}
    conn = database.get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """SELECT id, assessment_name, score, severity, risk_level, started_at, completed_at, status, safety_escalated
           FROM assessment_sessions
           WHERE patient_id = ? AND status = 'completed'
           ORDER BY completed_at DESC""",
        (profile["patient_id"],),
    )
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return {"sessions": rows}

# ---------------------------------------------------------------------------
# Assessments: Clinical report for a session
# ---------------------------------------------------------------------------

@app.get("/api/assessments/report/{session_id}")
def assessment_report(session_id: int, user: dict = Depends(get_current_user)):
    # Verify session belongs to user
    profile = agents.SecurityManager.get_patient_profile(user["id"])
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    conn = database.get_connection()
    cursor = conn.cursor()

    # Session details
    cursor.execute(
        """SELECT s.*, sum.summary_text, sum.clinician_notes, sum.action_items
           FROM assessment_sessions s
           LEFT JOIN session_summaries sum ON s.id = sum.session_id
           WHERE s.id = ? AND s.patient_id = ?""",
        (session_id, profile["patient_id"]),
    )
    session_row = cursor.fetchone()
    if not session_row:
        conn.close()
        raise HTTPException(status_code=404, detail="Session not found or access denied")

    result = dict(session_row)

    # Responses
    cursor.execute(
        "SELECT question_id, response_text, response_value FROM assessment_responses WHERE session_id = ? ORDER BY id",
        (session_id,),
    )
    result["responses"] = [dict(r) for r in cursor.fetchall()]

    # Transformational report
    cursor.execute("SELECT * FROM transformational_reports WHERE session_id = ?", (session_id,))
    t_row = cursor.fetchone()
    result["dira_report"] = dict(t_row) if t_row else None

    conn.close()
    return result

# ---------------------------------------------------------------------------
# Coach Chat (post-assessment RAG wellness coach)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Compatibility session stores (user_id -> data)
# ---------------------------------------------------------------------------
intake_sessions_by_user: dict[int, dict] = {}
assessment_sessions_by_user: dict[int, dict] = {}
coach_sessions_by_user: dict[int, dict] = {}

# ---------------------------------------------------------------------------
# Coach Chat (post-assessment RAG wellness coach)
# ---------------------------------------------------------------------------

@app.post("/api/chat/coach")
def coach_chat(req: CoachChatRequest, authorization: Optional[str] = Header(None)):
    global rag_assistant_instance
    token = None
    if authorization:
        scheme, _, tok = authorization.partition(" ")
        if scheme.lower() == "bearer" and tok:
            token = tok
    if not token and req.token:
        token = req.token

    if not token or token not in auth_sessions:
        raise HTTPException(status_code=401, detail="Invalid, expired or missing token")

    user = auth_sessions[token]

    if not has_completed_any_assessment(user["id"]):
        raise HTTPException(
            status_code=403,
            detail="You must complete at least one assessment before using the wellness coach."
        )

    # Initialise coach session
    if token not in coach_sessions:
        coach_sessions[token] = {
            "chat_history": [
                {"role": "assistant", "content": "Hello! I am your CareMinds Wellness Coach. I have reviewed your latest assessment findings and am here to help you reflect, explore your growth roadmap, and discuss stress or CBT principles. What would you like to discuss today?"}
            ]
        }

    session = coach_sessions[token]
    session["chat_history"].append({"role": "user", "content": req.message})

    # Fetch latest completed session for personalised context
    profile = agents.SecurityManager.get_patient_profile(user["id"])
    latest_session = None
    if profile and profile.get("patient_id"):
        try:
            conn = database.get_connection()
            cursor = conn.cursor()
            cursor.execute(
                """SELECT s.id, s.assessment_name, s.score, s.severity, s.risk_level,
                          sum.clinician_notes as soap_notes,
                          t.clinical_risk_summary, t.deep_narrative_insight, t.blind_spot_detection,
                          t.strength_recognition, t.coaching_reflection, t.growth_roadmap
                   FROM assessment_sessions s
                   LEFT JOIN session_summaries sum ON s.id = sum.session_id
                   LEFT JOIN transformational_reports t ON s.id = t.session_id
                   WHERE s.patient_id = ? AND s.status = 'completed'
                   ORDER BY s.completed_at DESC LIMIT 1""",
                (profile["patient_id"],),
            )
            latest_session = cursor.fetchone()
            conn.close()
        except Exception as e:
            print(f"Error loading latest session for chat context: {e}")

    # RAG search
    query = req.message
    database.log_audit(user["id"], "knowledge_search", "faiss_index", 0)
    res = rag_assistant_instance.search(query, top_k=4) if rag_assistant_instance else []

    context = ""
    citations = []
    if res:
        for r in res:
            context += f"[Doc: {r['source']}, Page: {r['page']}]\n{r['text']}\n\n"
            citations.append(f"- **{r['source']} (Page {r['page']})**")

    # Personalised prefix
    personalized_prefix = ""
    if latest_session:
        ls = dict(latest_session)
        soap_snippet = (ls.get("soap_notes") or "")[:400]
        personalized_prefix = f"""You are CareMinds AI, the patient's personal Advanced Transformational Wellness Coach.
You have the following clinical and transformational report data for the patient's latest assessment:
- Assessment Type: {ls.get('assessment_name', 'N/A')} (Clinical Score: {ls.get('score', 'N/A')}, Severity: {ls.get('severity', 'N/A')}, Risk: {ls.get('risk_level', 'N/A')})
- Clinical SOAP Notes: {soap_snippet}...
- Clinical Risk Summary: {ls.get('clinical_risk_summary', 'N/A')}
- Deep Narrative Insight: {ls.get('deep_narrative_insight', 'N/A')}
- Blind Spot Detection: {ls.get('blind_spot_detection', 'N/A')}
- Strength Recognition: {ls.get('strength_recognition', 'N/A')}
- AI Coaching Reflection: {ls.get('coaching_reflection', 'N/A')}
- Growth Roadmap: {ls.get('growth_roadmap', 'N/A')}

Incorporate these details to make your coaching highly personalized and relevant to the patient's specific limiting beliefs, strengths, and goals.
"""

    prompt_template = agents.load_prompt_file("rag_prompt.txt", DEFAULT_RAG_PROMPT)
    full_context = context
    if personalized_prefix:
        full_context = f"{personalized_prefix}\n\nRetrieved Reference Materials Context:\n{context}"

    prompt = prompt_template.format(context=full_context, query=query)
    try:
        reply = agents.gemini_client.generate(prompt)
    except Exception as e:
        reply = "I'm processing what you said. Let's explore this together."
        print(f"Error generating coach response: {e}")

    if citations and "citations" not in reply.lower() and "document citations" not in reply.lower():
        reply += "\n\n**Document Citations:**\n" + "\n".join(set(citations))

    session["chat_history"].append({"role": "assistant", "content": reply})

    return {
        "reply": reply,
        "citations": list(set(citations)),
        "chat_history": session["chat_history"],
    }

# ---------------------------------------------------------------------------
# Compatibility / Authless Endpoints
# ---------------------------------------------------------------------------

@app.get("/api/auth/profile/{user_id}")
def get_auth_profile(user_id: int):
    profile = agents.SecurityManager.get_patient_profile(user_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    return profile

@app.post("/api/auth/profile/update")
def update_auth_profile(req: AuthProfileUpdateRequest):
    success, msg = agents.SecurityManager.update_patient_profile(
        req.user_id, req.dob, req.gender, req.phone, req.consent_given
    )
    if not success:
        raise HTTPException(status_code=400, detail=msg)
    return {"message": "Profile updated successfully"}

@app.post("/api/intake/message")
def auth_intake_chat(req: AuthIntakeChatRequest):
    user_id = req.user_id
    
    # Safety Check
    try:
        safety = agents.SafetyAgent.check_safety(req.message)
        if safety and safety.get("is_escalated"):
            return {
                "crisis": True,
                "response": safety.get("recommended_response", "Please contact support immediately."),
                "redirect": False
            }
    except Exception as e:
        print(f"Safety check error in compatibility intake: {e}")
        
    if user_id not in intake_sessions_by_user:
        intake_sessions_by_user[user_id] = {
            "chat_history": [
                {"role": "assistant", "content": "Hello! I am CareMinds AI. How can I support you today? Please say hello or type 'hi' to get started."}
            ],
            "redirected": False,
            "recommended_scale": None,
            "recommended_reason": None,
        }
        
    session = intake_sessions_by_user[user_id]
    if session["redirected"]:
        return {
            "crisis": False,
            "redirect": True,
            "scale": session["recommended_scale"],
            "reason": session["recommended_reason"],
            "response": None,
        }
        
    session["chat_history"].append({"role": "user", "content": req.message})
    
    user_msgs = [m for m in session["chat_history"] if m["role"] == "user"]
    user_msg_count = len(user_msgs)
    
    is_greeting = False
    if user_msg_count > 0:
        first = user_msgs[0]["content"].strip().lower().rstrip(".!?")
        if first in ["hi", "hello", "hey", "hi there", "hello there", "greetings"]:
            is_greeting = True
    threshold = 3 if is_greeting else 2
    
    if user_msg_count >= threshold:
        try:
            rec_scale, reason = agents.recommend_assessment_scale(session["chat_history"])
        except Exception as e:
            rec_scale, reason = "phq9", "We recommend completing a standard PHQ-9 screening."
            print(f"Error recommending scale: {e}")
        session["redirected"] = True
        session["recommended_scale"] = rec_scale
        session["recommended_reason"] = reason
        return {
            "crisis": False,
            "redirect": True,
            "scale": rec_scale,
            "reason": reason,
            "response": None,
        }
        
    user_clean = req.message.strip().lower().rstrip(".!?")
    if user_clean in ["hi", "hello", "hey", "hi there", "hello there", "greetings"]:
        reply = "Hi! What seems to be the problem or concern you are experiencing today? Please describe it."
    else:
        chat_str = ""
        for msg in session["chat_history"]:
            chat_str += f"{msg['role']}: {msg['content']}\n"
        prompt = f"""You are CareMinds AI, a psychological diagnostic chatbot.
A user is sharing their feelings with you. Ask a single, brief, empathetic follow-up question to help narrow down whether they are experiencing anxiety, depression, general stress, or low well-being.
Keep your response short (1-2 sentences max).

Chat History:
{chat_str}
Assistant:"""
        try:
            reply = agents.gemini_client.generate(prompt)
        except Exception as e:
            reply = "I understand. Could you tell me more about what you are feeling?"
            print(f"Error generating intake response: {e}")
            
    session["chat_history"].append({"role": "assistant", "content": reply})
    
    return {
        "crisis": False,
        "redirect": False,
        "scale": None,
        "reason": None,
        "response": reply
    }

@app.get("/api/assessments/list")
def list_assessments_compat():
    scales = []
    for key in VALID_SCALES:
        filepath = f"data/assessments/{key}.json"
        if os.path.exists(filepath):
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            scales.append({
                "key": key,
                "name": data.get("name", key),
                "description": data.get("description", ""),
            })
    return scales



@app.post("/api/assessments/submit")
def auth_submit_answer(req: AuthSubmitAnswerRequest):
    user_id = req.user_id
    session_id = req.session_id
    question_id = req.question_id
    ans_text = req.answer_text.strip()
    ans_val = req.answer_value
    
    try:
        safety = agents.SafetyAgent.check_safety(ans_text)
        if safety and safety.get("safety_trigger", False):
            conn = database.get_connection()
            cursor = conn.cursor()
            cursor.execute(
                """UPDATE assessment_sessions
                   SET status = 'completed', score = 0, severity = 'Crisis Distress',
                       risk_level = 'high', safety_escalated = 1, safety_notes = ?, completed_at = ?
                   WHERE id = ?""",
                (
                    f"Self-harm trigger word detected: '{ans_text}'",
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    session_id,
                ),
            )
            conn.commit()
            conn.close()
            database.log_audit(user_id, "safety_crisis_alert", "assessment_sessions", session_id)
            return {
                "crisis": True,
                "response": safety.get("recommended_response", "Please contact professional support immediately.")
            }
    except Exception as e:
        print(f"Safety check error on submit: {e}")
        
    conn = database.get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO assessment_responses (session_id, question_id, response_value, response_text) VALUES (?, ?, ?, ?)",
        (session_id, question_id, ans_val, ans_text),
    )
    conn.commit()
    conn.close()
    
    save_chat(session_id, "user", ans_text)
    ack = f"Acknowledged response: {ans_text}"
    save_chat(session_id, "assistant", ack)
    
    return {
        "crisis": False
    }

@app.post("/api/assessments/complete/{session_id}")
def auth_complete_assessment(session_id: int, user_id: int = Form(...)):
    conn = database.get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM assessment_sessions WHERE id = ?", (session_id,))
    session_row = cursor.fetchone()
    if not session_row:
        conn.close()
        raise HTTPException(status_code=404, detail="Session not found")
    
    session_data = dict(session_row)
    assessment_name = session_data["assessment_name"]
    
    scale_key = None
    for k in VALID_SCALES:
        data = load_scale_data(k)
        if data.get("name") == assessment_name:
            scale_key = k
            break
    if not scale_key:
        scale_key = "phq9"
        
    data = load_scale_data(scale_key)
    
    try:
        score, severity, risk = agents.ScoringEngine.score_session(session_id, data)
    except Exception as e:
        print(f"Scoring engine error: {e}")
        score, severity, risk = 0, "Mild Distress", "low"
        
    cursor.execute(
        """UPDATE assessment_sessions
           SET status = 'completed', score = ?, severity = ?, risk_level = ?, completed_at = ?
           WHERE id = ?""",
        (score, severity, risk, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), session_id),
    )
    conn.commit()
    
    database.log_audit(user_id, f"completed_{scale_key}", "assessment_sessions", session_id)
    
    try:
        agents.SessionSummarizerAgent.generate_clinical_notes(session_id)
    except Exception as e:
        print(f"Error generating clinical notes/DIRA: {e}")
        
    cursor.execute("SELECT * FROM transformational_reports WHERE session_id = ?", (session_id,))
    t_row = cursor.fetchone()
    conn.close()
    
    t_report = dict(t_row) if t_row else None
    
    return {
        "success": True,
        "score": score,
        "severity": severity,
        "risk_level": risk,
        "transformational_report": t_report
    }

@app.post("/api/rag/chat")
def auth_coach_chat(req: AuthCoachChatRequest):
    global rag_assistant_instance
    user_id = req.user_id
    query = req.message

    if not has_completed_any_assessment(user_id):
        raise HTTPException(
            status_code=403,
            detail="You must complete at least one assessment before using the wellness coach."
        )

    if user_id not in coach_sessions_by_user:
        coach_sessions_by_user[user_id] = {
            "chat_history": [
                {"role": "assistant", "content": "Hello! I am your CareMinds Wellness Coach. I have reviewed your latest assessment findings and am here to help you reflect, explore your growth roadmap, and discuss stress or CBT principles. What would you like to discuss today?"}
            ]
        }

    session = coach_sessions_by_user[user_id]
    session["chat_history"].append({"role": "user", "content": query})

    profile = agents.SecurityManager.get_patient_profile(user_id)
    latest_session = None
    if profile and profile.get("patient_id"):
        try:
            conn = database.get_connection()
            cursor = conn.cursor()
            cursor.execute(
                """SELECT s.id, s.assessment_name, s.score, s.severity, s.risk_level,
                          sum.clinician_notes as soap_notes,
                          t.clinical_risk_summary, t.deep_narrative_insight, t.blind_spot_detection,
                          t.strength_recognition, t.coaching_reflection, t.growth_roadmap
                   FROM assessment_sessions s
                   LEFT JOIN session_summaries sum ON s.id = sum.session_id
                   LEFT JOIN transformational_reports t ON s.id = t.session_id
                   WHERE s.patient_id = ? AND s.status = 'completed'
                   ORDER BY s.completed_at DESC LIMIT 1""",
                (profile["patient_id"],),
            )
            latest_session = cursor.fetchone()
            conn.close()
        except Exception as e:
            print(f"Error loading latest session for chat context: {e}")

    database.log_audit(user_id, "knowledge_search", "faiss_index", 0)
    res = rag_assistant_instance.search(query, top_k=4) if rag_assistant_instance else []

    context = ""
    citations = []
    if res:
        for r in res:
            context += f"[Doc: {r['source']}, Page: {r['page']}]\n{r['text']}\n\n"
            citations.append(f"- **{r['source']} (Page {r['page']})**")

    personalized_prefix = ""
    if latest_session:
        ls = dict(latest_session)
        soap_snippet = (ls.get("soap_notes") or "")[:400]
        personalized_prefix = f"""You are CareMinds AI, the patient's personal Advanced Transformational Wellness Coach.
You have the following clinical and transformational report data for the patient's latest assessment:
- Assessment Type: {ls.get('assessment_name', 'N/A')} (Clinical Score: {ls.get('score', 'N/A')}, Severity: {ls.get('severity', 'N/A')}, Risk: {ls.get('risk_level', 'N/A')})
- Clinical SOAP Notes: {soap_snippet}...
- Clinical Risk Summary: {ls.get('clinical_risk_summary', 'N/A')}
- Deep Narrative Insight: {ls.get('deep_narrative_insight', 'N/A')}
- Blind Spot Detection: {ls.get('blind_spot_detection', 'N/A')}
- Strength Recognition: {ls.get('strength_recognition', 'N/A')}
- AI Coaching Reflection: {ls.get('coaching_reflection', 'N/A')}
- Growth Roadmap: {ls.get('growth_roadmap', 'N/A')}

Incorporate these details to make your coaching highly personalized and relevant to the patient's specific limiting beliefs, strengths, and goals.
"""

    prompt_template = agents.load_prompt_file("rag_prompt.txt", DEFAULT_RAG_PROMPT)
    full_context = context
    if personalized_prefix:
        full_context = f"{personalized_prefix}\n\nRetrieved Reference Materials Context:\n{context}"

    prompt = prompt_template.format(context=full_context, query=query)
    try:
        reply = agents.gemini_client.generate(prompt)
    except Exception as e:
        reply = "I'm processing what you said. Let's explore this together."
        print(f"Error generating coach response: {e}")

    if citations and "citations" not in reply.lower() and "document citations" not in reply.lower():
        reply += "\n\n**Document Citations:**\n" + "\n".join(set(citations))

    session["chat_history"].append({"role": "assistant", "content": reply})

    return {
        "response": reply,
        "citations": list(set(citations)),
        "chat_history": session["chat_history"]
    }

# ---------------------------------------------------------------------------
# Clinician Endpoints
# ---------------------------------------------------------------------------

@app.get("/api/clinician/patients/{clinician_id}")
def get_clinician_patients(clinician_id: int):
    conn = database.get_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT p.id as patient_id, u.full_name, p.dob, p.age, p.gender, p.consent_given 
        FROM patients p 
        JOIN users u ON p.user_id = u.id
        WHERE p.assigned_psychologist_id = ?
    """, (clinician_id,))
    patients = [dict(r) for r in cursor.fetchall()]
    
    cursor.execute("""
        SELECT s.id, u.full_name, s.assessment_name, s.completed_at, s.safety_notes
        FROM assessment_sessions s
        JOIN patients p ON s.patient_id = p.id
        JOIN users u ON p.user_id = u.id
        WHERE p.assigned_psychologist_id = ? AND s.safety_escalated = 1
        ORDER BY s.completed_at DESC
    """, (clinician_id,))
    alerts = [dict(r) for r in cursor.fetchall()]
    
    cursor.execute("""
        SELECT s.risk_level, count(*) as count
        FROM assessment_sessions s
        JOIN patients p ON s.patient_id = p.id
        WHERE p.assigned_psychologist_id = ? AND s.status = 'completed'
        GROUP BY s.risk_level
    """, (clinician_id,))
    risk_distribution = [dict(r) for r in cursor.fetchall()]
    
    cursor.execute("""
        SELECT s.assessment_name, count(*) as count
        FROM assessment_sessions s
        JOIN patients p ON s.patient_id = p.id
        WHERE p.assigned_psychologist_id = ? AND s.status = 'completed'
        GROUP BY s.assessment_name
    """, (clinician_id,))
    volume_stats = [dict(r) for r in cursor.fetchall()]
    
    conn.close()
    
    return {
        "patients": patients,
        "alerts": alerts,
        "stats": {
            "risk_distribution": risk_distribution,
            "volume_stats": volume_stats
        }
    }

@app.get("/api/clinician/patient/{patient_id}/sessions")
def get_patient_sessions(patient_id: int):
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
def get_session_details(session_id: int):
    conn = database.get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM transformational_reports WHERE session_id = ?", (session_id,))
    t_row = cursor.fetchone()
    conn.close()
    return {
        "transformational_report": dict(t_row) if t_row else None
    }

@app.get("/api/reports/download/{session_id}")
def download_report(session_id: int):
    import report_generator
    os.makedirs("reports", exist_ok=True)
    pdf_path = f"reports/session_{session_id}_report.pdf"
    
    if not os.path.exists(pdf_path):
        try:
            report_generator.ReportGenerator.generate_assessment_report(session_id)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to generate PDF: {e}")
            
    if os.path.exists(pdf_path):
        return FileResponse(pdf_path, media_type="application/pdf", filename=f"report_{session_id}.pdf")
    raise HTTPException(status_code=404, detail="PDF report could not be found or generated")

# ---------------------------------------------------------------------------
# Admin Endpoints
# ---------------------------------------------------------------------------

@app.post("/api/admin/upload")
async def admin_upload(
    file: UploadFile = File(...),
    category: str = Form(...),
    user_id: int = Form(...)
):
    upload_dir = os.path.join("knowledge_base", category)
    os.makedirs(upload_dir, exist_ok=True)
    
    file_path = os.path.join(upload_dir, file.filename)
    
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
        
    if rag_assistant_instance:
        try:
            chunks = rag_assistant_instance.add_pdf(file_path)
            return {
                "success": True,
                "filename": file.filename,
                "chunks_indexed": chunks
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to index PDF: {e}")
    else:
        raise HTTPException(status_code=503, detail="RAG Assistant not initialized")

# ---------------------------------------------------------------------------
# Static files & SPA fallback
# ---------------------------------------------------------------------------

# Serve static directory if it exists
static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
if os.path.isdir(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

# Also check for templates directory (for the frontend builder)
templates_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")

@app.get("/")
def serve_root():
    # Prefer static/index.html, fall back to templates/index.html
    static_index = os.path.join(static_dir, "index.html")
    templates_index = os.path.join(templates_dir, "index.html")
    if os.path.exists(static_index):
        return FileResponse(static_index)
    elif os.path.exists(templates_index):
        return FileResponse(templates_index)
    return JSONResponse(
        {"message": "CareMinds AI API is running. Place index.html in static/ or templates/ to serve the frontend."},
        status_code=200,
    )

@app.get("/portal")
def serve_portal():
    portal_index = os.path.join(templates_dir, "portal.html")
    if os.path.exists(portal_index):
        return FileResponse(portal_index)
    raise HTTPException(status_code=404, detail="Portal page not found")

@app.get("/clinician")
def serve_clinician():
    clinician_index = os.path.join(templates_dir, "clinician.html")
    if os.path.exists(clinician_index):
        return FileResponse(clinician_index)
    raise HTTPException(status_code=404, detail="Clinician page not found")

@app.get("/admin")
def serve_admin():
    admin_index = os.path.join(templates_dir, "admin.html")
    if os.path.exists(admin_index):
        return FileResponse(admin_index)
    raise HTTPException(status_code=404, detail="Admin page not found")

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)
