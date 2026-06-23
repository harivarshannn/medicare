import streamlit as st
import os
import json
from datetime import datetime
import plotly.graph_objects as go
import plotly.express as px

import config
import database
import agents
import report_generator
from rag_assistant import RAGAssistant

st.set_page_config(page_title="CareMinds AI Platform", page_icon="🧠", layout="wide")

# Custom CSS styling matching Risen PsyLabs Design System
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

    html, body, [data-testid="stAppViewContainer"] {
        font-family: 'Inter', sans-serif !important;
        background-color: #F3F4F6 !important;
        color: #111827 !important;
    }
    
    header[data-testid="stHeader"] {
        display: none !important;
    }
    
    .main .block-container {
        padding-top: 5.5rem !important;
        padding-bottom: 2rem !important;
        max-width: 1280px !important;
        margin: 0 auto !important;
    }
    
    /* Sidebar styling */
    [data-testid="stSidebar"] {
        background-color: #FFFFFF !important;
        border-right: 1px solid #E5E7EB !important;
        padding-top: 3.5rem !important;
    }
    
    [data-testid="stSidebar"] * {
        color: #111827 !important;
    }
    
    /* Card design */
    .metric-card {
        background-color: #FFFFFF !important;
        border-radius: 8px !important;
        padding: 1.5rem !important;
        margin-bottom: 1rem !important;
        border: 1px solid #E5E7EB !important;
        box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05), 0 2px 4px -1px rgba(0, 0, 0, 0.03) !important;
        color: #111827 !important;
    }
    
    .metric-card h1, .metric-card h2, .metric-card h3, .metric-card h4, .metric-card p, .metric-card span {
        color: #111827 !important;
    }
    
    /* Crisis Alert card */
    .crisis-card {
        background-color: #FEF2F2 !important;
        border: 1.5px solid #EF4444 !important;
        border-radius: 8px !important;
        padding: 1.5rem !important;
        color: #991B1B !important;
        margin-bottom: 1.5rem !important;
        box-shadow: 0 4px 6px -1px rgba(239, 68, 68, 0.1) !important;
    }
    
    .alert-header {
        background-color: #EF4444 !important;
        color: #FFFFFF !important;
        padding: 12px !important;
        border-radius: 6px !important;
        font-weight: bold !important;
        margin-bottom: 15px !important;
    }
    
    /* Buttons matching Secondary/CTA Accent Blue */
    div.stButton > button {
        background-color: #3B82F6 !important;
        color: #FFFFFF !important;
        border: none !important;
        border-radius: 6px !important;
        padding: 0.5rem 1.25rem !important;
        font-weight: 600 !important;
        transition: all 0.2s ease-in-out !important;
    }
    
    div.stButton > button:hover {
        background-color: #2563EB !important;
        box-shadow: 0 4px 6px -1px rgba(59, 130, 246, 0.3) !important;
        color: #FFFFFF !important;
    }
    
    div.stButton > button:active {
        transform: scale(0.98) !important;
    }
    
    /* Input Fields */
    div[data-baseweb="input"], div[data-baseweb="textarea"], select {
        background-color: #FFFFFF !important;
        border-radius: 6px !important;
        border: 1px solid #D1D5DB !important;
        color: #111827 !important;
    }
    
    /* Typography customizations */
    h1, h2, h3, h4, h5, h6 {
        color: #111827 !important;
        font-family: 'Inter', sans-serif !important;
    }
    
    p, span, label {
        color: #4B5563 !important;
        font-family: 'Inter', sans-serif !important;
    }
</style>
""", unsafe_allow_html=True)

# Fixed Header styling matching Primary Purple (#3A41B4)
st.markdown("""
<div style="position: fixed; top: 0; left: 0; right: 0; height: 60px; background-color: #3A41B4; color: white; display: flex; align-items: center; justify-content: space-between; padding: 0 24px; z-index: 999999; box-shadow: 0 2px 4px rgba(0,0,0,0.1); font-family: 'Inter', sans-serif;">
    <div style="display: flex; align-items: center; gap: 8px; font-weight: 700; font-size: 1.25rem;">
        <span style="font-size: 1.5rem; color: #FFFFFF;">🧠</span>
        <span style="color: #FFFFFF !important; font-weight: 700;">CareMinds AI</span>
    </div>
    <div style="display: flex; align-items: center; gap: 20px; font-weight: 500; font-size: 0.9rem;">
        <span style="color: rgba(255,255,255,0.85) !important;">Risen PsyLabs Diagnostics Suite</span>
    </div>
</div>
""", unsafe_allow_html=True)

if "rag" not in st.session_state:
    st.session_state.rag = RAGAssistant()
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
if "user" not in st.session_state:
    st.session_state.user = None
if "assessment_state" not in st.session_state:
    st.session_state.assessment_state = {
        "active": False, "scale_key": None, "q_idx": 0, "session_id": None, "answers": {}, "questions": [], "scale_data": None
    }

# Session Memory helper
def save_chat(session_id, sender, msg):
    try:
        conn = database.get_connection()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO chat_messages (session_id, sender, message) VALUES (?, ?, ?)",
                       (session_id, sender, msg))
        conn.commit()
        conn.close()
    except:
        pass

    # Voice support features removed
def render_auth():
    st.title("🧠 CareMinds AI Platform")
    st.subheader("Mental Health Assessment & Clinical Psychometric Portal")
    auth_mode = st.radio("Session Select", ["Login", "Sign Up"], horizontal=True)
    username = st.text_input("Username")
    password = st.text_input("Password", type="password")
    if auth_mode == "Login":
        if st.button("Access Platform"):
            user = agents.SecurityManager.authenticate_user(username, password)
            if user:
                st.session_state.logged_in = True
                st.session_state.user = user
                st.rerun()
            else:
                st.error("Incorrect credentials.")
    else:
        full_name = st.text_input("Full Name")
        email = st.text_input("Email")
        role = st.selectbox("Role", [config.ROLE_PATIENT, config.ROLE_PSYCHOLOGIST])
        if st.button("Create User Profile"):
            uid, err = agents.SecurityManager.register_user(username, password, role, full_name, email)
            if err: st.error(err)
            else: st.success("Registered. Please login.")

def has_completed_any_assessment(user_id):
    try:
        profile = agents.SecurityManager.get_patient_profile(user_id)
        if not profile:
            return False
        conn = database.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT count(*) FROM assessment_sessions WHERE patient_id = ? AND status = 'completed'", (profile["patient_id"],))
        count = cursor.fetchone()[0]
        conn.close()
        return count > 0
    except:
        return False

def render_sidebar():
    st.sidebar.title("CareMinds Workspace")
    st.sidebar.write(f"User: **{st.session_state.user['full_name']}**")
    st.sidebar.write(f"Role: `{st.session_state.user['role'].upper()}`")
    if st.sidebar.button("Log Out"):
        st.session_state.logged_in = False
        st.session_state.user = None
        st.session_state.assessment_state["active"] = False
        if "menu_choice" in st.session_state:
            del st.session_state.menu_choice
        st.rerun()
    st.sidebar.divider()
    role = st.session_state.user["role"]
    
    # Initialize menu choice if needed
    if "menu_choice" not in st.session_state:
        st.session_state.menu_choice = "Intake & Consent" if role == config.ROLE_PATIENT else "Patient Dashboard"
        
    if role == config.ROLE_PATIENT:
        has_completed = has_completed_any_assessment(st.session_state.user["id"])
        has_active = st.session_state.get("assessment_state", {}).get("active", False)
        
        options = ["Intake & Consent", "Wellness Chatbot"]
        if has_active or has_completed:
            options.append("Assessment Center")
        if has_completed:
            options.append("My Progress")
        
        if st.session_state.menu_choice not in options:
            st.session_state.menu_choice = options[0]
            
        choice = st.sidebar.radio("Menu", options, index=options.index(st.session_state.menu_choice))
        st.session_state.menu_choice = choice
        return choice
        
    elif role == config.ROLE_PSYCHOLOGIST:
        options = ["Patient Dashboard", "Wellness Chatbot", "Resource Administrator"]
        if st.session_state.menu_choice not in options:
            st.session_state.menu_choice = options[0]
        choice = st.sidebar.radio("Menu", options, index=options.index(st.session_state.menu_choice))
        st.session_state.menu_choice = choice
        return choice
        
    options = ["Database Auditor", "Resource Administrator"]
    if st.session_state.menu_choice not in options:
        st.session_state.menu_choice = options[0]
    choice = st.sidebar.radio("Menu", options, index=options.index(st.session_state.menu_choice))
    st.session_state.menu_choice = choice
    return choice

def render_intake():
    st.title("📋 Intake Information & Evaluation Consent")
    uid = st.session_state.user["id"]
    profile = agents.SecurityManager.get_patient_profile(uid)
    dob = st.text_input("Date of Birth (YYYY-MM-DD)", value=profile.get("dob", ""))
    gender = st.selectbox("Gender", ["Male", "Female", "Non-binary", "Undisclosed"], index=0)
    phone = st.text_input("Phone Number", value=profile.get("phone", ""))
    st.subheader("Psychometric Informed Consent Agreement")
    st.caption("Your answers are evaluated deterministically to assess mood severity metrics. This AI is not a definitive diagnosis. High risk responses trigger crisis resource notifications.")
    consent = st.checkbox("I grant explicit consent for storing and scoring my responses.", value=bool(profile.get("consent_given", 0)))
    if st.button("Update Patient Profile"):
        success, msg = agents.SecurityManager.update_patient_profile(uid, dob, gender, phone, consent)
        if success: st.success("Profile successfully updated.")
        else: st.error(msg)

def render_diagnostic_chat():
    st.title("🩺 CareMinds Wellness Diagnostic Chat")
    st.caption("Chat with our diagnostic bot to receive an assessment recommendation based on your current concerns.")
    
    # Initialize diagnostic chat history
    if "diagnostic_chat_history" not in st.session_state:
        st.session_state.diagnostic_chat_history = [
            {"role": "assistant", "content": "Hello! I am CareMinds AI. How can I support you today? Please say hello or type 'hi' to get started."}
        ]
        
    # Count how many responses the user has submitted
    user_msgs = [msg for msg in st.session_state.diagnostic_chat_history if msg["role"] == "user"]
    user_msg_count = len(user_msgs)
    
    # Determine if user started with a short greeting
    is_greeting = False
    if user_msg_count > 0:
        first_msg = user_msgs[0]["content"].strip().lower().rstrip(".!?")
        if first_msg in ["hi", "hello", "hey", "hi there", "hello there", "greetings"]:
            is_greeting = True
            
    # Threshold is 3 if they started with a greeting (greeting + concern + details), 2 if they started with concern directly
    threshold = 3 if is_greeting else 2
    
    # If user has submitted enough responses, we analyze and redirect them automatically!
    if user_msg_count >= threshold:
        with st.spinner("Analyzing your responses and redirecting you to the recommended assessment..."):
            rec_scale, reason = agents.recommend_assessment_scale(st.session_state.diagnostic_chat_history)
            
            # Setup active assessment state and redirect
            st.session_state.assessment_state["active"] = False
            start_assessment(rec_scale)
            st.session_state.menu_choice = "Assessment Center"
            
            # Clear any diagnostic recommendation state
            if "diagnostic_recommendation" in st.session_state:
                del st.session_state.diagnostic_recommendation
                
            st.rerun()
            return
            
    # Display message history
    for msg in st.session_state.diagnostic_chat_history:
        with st.chat_message(msg["role"]):
            st.write(msg["content"])
            
    # Chat input
    user_input = st.chat_input("Describe your thoughts, feelings, or concerns here...")
    if user_input:
        # Display user input immediately
        with st.chat_message("user"):
            st.write(user_input)
        st.session_state.diagnostic_chat_history.append({"role": "user", "content": user_input})
        
        # Recalculate message count after user message is added
        temp_user_msgs = [msg for msg in st.session_state.diagnostic_chat_history if msg["role"] == "user"]
        temp_user_msg_count = len(temp_user_msgs)
        
        # Check if first message was a greeting
        temp_is_greeting = False
        if temp_user_msg_count > 0:
            temp_first_msg = temp_user_msgs[0]["content"].strip().lower().rstrip(".!?")
            if temp_first_msg in ["hi", "hello", "hey", "hi there", "hello there", "greetings"]:
                temp_is_greeting = True
        temp_threshold = 3 if temp_is_greeting else 2
        
        if temp_user_msg_count >= temp_threshold:
            # We reached the threshold! Trigger rerun immediately so the redirection block at the top executes.
            st.rerun()
        else:
            # Generate response
            with st.spinner("Reflecting..."):
                user_clean = user_input.strip().lower().rstrip(".!?")
                # If user says greeting (like 'hi'), reply with "whats ur problem like that"
                if user_clean in ["hi", "hello", "hey", "hi there", "hello there", "greetings"]:
                    ans = "Hi! What seems to be the problem or concern you are experiencing today? Please describe it."
                else:
                    chat_str = ""
                    for msg in st.session_state.diagnostic_chat_history:
                        chat_str += f"{msg['role']}: {msg['content']}\n"
                    prompt = f"""
You are CareMinds AI, a psychological diagnostic chatbot.
A user is sharing their feelings with you. Ask a single, brief, empathetic follow-up question to help narrow down whether they are experiencing anxiety, depression, general stress, or low well-being.
Keep your response short (1-2 sentences max).

Chat History:
{chat_str}
Assistant:"""
                    ans = agents.gemini_client.generate(prompt)
                    
            st.session_state.diagnostic_chat_history.append({"role": "assistant", "content": ans})
            st.rerun()

def render_assessments():
    st.title("📋 Conversational Screening Center")
    if st.session_state.get("crisis_detected", False):
        st.markdown(f'<div class="crisis-card"><h3>⚠️ Crisis Resource Active</h3><p>{st.session_state.crisis_msg}</p></div>', unsafe_allow_html=True)
        if st.button("I acknowledge this warning"):
            st.session_state.crisis_detected = False
            st.session_state.assessment_state["active"] = False
            st.rerun()
        return
    
    state = st.session_state.assessment_state
    if not state["active"]:
        st.subheader("Choose clinical scale to start:")
        cols = st.columns(2)
        with cols[0]:
            if st.button("Start PHQ-9 Depression evaluation"):
                start_assessment("phq9")
            if st.button("Start WHO-5 Well-being evaluation"):
                start_assessment("who5")
        with cols[1]:
            if st.button("Start GAD-7 Anxiety evaluation"):
                start_assessment("gad7")
            if st.button("Start PSS-10 Stress evaluation"):
                start_assessment("pss10")
    else:
        run_active_assessment()

def start_assessment(key):
    profile = agents.SecurityManager.get_patient_profile(st.session_state.user["id"])
    if not profile or not profile.get("consent_given", 0):
        st.error("You must fill in details and grant consent in 'Intake & Consent' tab first.")
        return
    filepath = f"data/assessments/{key}.json"
    with open(filepath, "r", encoding="utf-8") as f: data = json.load(f)
    
    conn = database.get_connection()
    cursor = conn.cursor()
    cursor.execute("""
    INSERT INTO assessment_sessions (patient_id, assessment_name, status, started_at)
    VALUES (?, ?, 'started', ?)
    """, (profile["patient_id"], data["name"], datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    sid = cursor.lastrowid
    conn.commit()
    conn.close()
    
    # Save pre-assessment intake/diagnostic chat messages to the database
    if "diagnostic_chat_history" in st.session_state:
        for msg in st.session_state.diagnostic_chat_history:
            save_chat(sid, msg["role"], msg["content"])
            
    st.session_state.assessment_state = {
        "active": True, "scale_key": key, "q_idx": 0, "session_id": sid, "answers": {}, "questions": data["questions"], "scale_data": data
    }
    database.log_audit(st.session_state.user["id"], f"started_assessment_{key}", "assessment_sessions", sid)
    st.rerun()

def run_active_assessment():
    state = st.session_state.assessment_state
    questions = state["questions"]
    q_idx = state["q_idx"]
    
    if q_idx >= len(questions):
        score, severity, risk = agents.ScoringEngine.score_session(state["session_id"], state["scale_data"])
        conn = database.get_connection()
        cursor = conn.cursor()
        cursor.execute("""
        UPDATE assessment_sessions
        SET status = 'completed', score = ?, severity = ?, risk_level = ?, completed_at = ?
        WHERE id = ?
        """, (score, severity, risk, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), state["session_id"]))
        conn.commit()
        conn.close()
        
        database.log_audit(st.session_state.user["id"], f"completed_{state['scale_key']}", "assessment_sessions", state["session_id"])
        
        with st.spinner("Creating clinical documentation SOAP file..."):
            summary_text, soap_notes, action_items = agents.SessionSummarizerAgent.generate_clinical_notes(state["session_id"])
            report_generator.ReportGenerator.generate_assessment_report(state["session_id"])
            
        st.success("Screening completed!")
        st.metric("Calculated Instrument Score", score)
        st.write(f"Classification Severity: **{severity}**")
        st.write(f"Safety Flag Risk: **{risk.upper()}**")
        
        # Check if DIRA transformational insights are available for this session
        conn = database.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM transformational_reports WHERE session_id = ?", (state["session_id"],))
        t_report = cursor.fetchone()
        conn.close()
        
        if t_report:
            st.divider()
            st.subheader("🌱 Layer 2: Deep Self-Awareness & Coaching Insights")
            
            # Show scores in metric cards
            dims = ["Resilience", "Self-Awareness", "Agency", "Flexibility", "Growth Mindset", "Relationships", "Purpose", "Optimism"]
            vals = [
                t_report["emotional_resilience"], t_report["self_awareness"], t_report["personal_agency"],
                t_report["cognitive_flexibility"], t_report["growth_mindset"], t_report["relationship_health"],
                t_report["purpose_alignment"], t_report["future_optimism"]
            ]
            
            cols = st.columns(4)
            for idx, (dim, val) in enumerate(zip(dims, vals)):
                cols[idx % 4].metric(dim, f"{val}/100")
                
            # Create Plotly bar chart
            fig = go.Figure(go.Bar(
                x=vals,
                y=dims,
                orientation='h',
                marker=dict(
                    color='#3B82F6',
                    line=dict(color='#2563EB', width=1)
                )
            ))
            fig.update_layout(
                title="<b>Transformational Dimension Analysis</b>",
                xaxis=dict(
                    title="Score", 
                    range=[0, 100],
                    showgrid=True,
                    gridcolor="#E5E7EB",
                    linecolor="#D1D5DB",
                    tickfont=dict(color="#4B5563")
                ),
                yaxis=dict(
                    autorange="reversed",
                    showgrid=False,
                    linecolor="#D1D5DB",
                    tickfont=dict(color="#4B5563")
                ),
                plot_bgcolor="#FFFFFF",
                paper_bgcolor="#FFFFFF",
                font=dict(family="Inter, sans-serif", color="#111827"),
                height=320,
                margin=dict(l=150, r=20, t=50, b=45)
            )
            st.plotly_chart(fig, use_container_width=True)
            
            # Display narrative insights
            st.markdown(f"### 🔍 Clinical Risk Summary\n{t_report['clinical_risk_summary']}")
            st.markdown(f"### 🧠 Deep Narrative Insight\n{t_report['deep_narrative_insight']}")
            st.markdown(f"### 🕶️ Blind Spot Detection\n{t_report['blind_spot_detection']}")
            st.markdown(f"### 💪 Strength Recognition\n{t_report['strength_recognition']}")
            st.markdown(f"### 💡 AI Coaching Reflection\n{t_report['coaching_reflection']}")
            st.markdown(f"### 🗺️ Personalized Growth Roadmap\n{t_report['growth_roadmap']}")
        
        # PDF Report Button
        pdf_file = f"reports/session_{state['session_id']}_report.pdf"
        if os.path.exists(pdf_file):
            with open(pdf_file, "rb") as f:
                st.download_button("📥 Download Clinical Report PDF", f, file_name=f"report_{state['session_id']}.pdf", mime="application/pdf")
                
        # JSON Report Button
        profile = agents.SecurityManager.get_patient_profile(st.session_state.user["id"])
        json_report = {
            "session_id": state["session_id"],
            "patient_name": profile["full_name"],
            "dob": profile["dob"],
            "age": profile["age"],
            "gender": profile["gender"],
            "assessment": state["scale_data"]["name"],
            "score": score,
            "severity": severity,
            "risk_level": risk,
            "completed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "SOAP_clinical_note": soap_notes,
            "action_items": action_items
        }
        st.download_button("📥 Export JSON Clinical Data", json.dumps(json_report, indent=4), file_name=f"report_{state['session_id']}.json", mime="application/json")
        
        if st.button("Finish Session"):
            state["active"] = False
            st.rerun()
        return
    
    q = questions[q_idx]
    is_dira = q["id"].startswith("dira_")
    if is_dira:
        st.markdown('<span style="color:#10B981; font-weight:bold; font-size:1.1rem;">🌱 Layer 2: Deep Self-Awareness & Meaning-Making (DIRA)</span>', unsafe_allow_html=True)
        # Determine DIRA question index
        dira_qids = [quest["id"] for quest in questions if quest["id"].startswith("dira_")]
        try:
            dira_idx = dira_qids.index(q["id"]) + 1
        except:
            dira_idx = q_idx + 1
        st.subheader(f"Question {dira_idx} of {len(dira_qids)}:")
    else:
        st.markdown(f'<span style="color:#3B82F6; font-weight:bold; font-size:1.1rem;">📋 Layer 1: Standardized Clinical Assessment ({state["scale_data"]["name"]})</span>', unsafe_allow_html=True)
        clinical_qids = [quest["id"] for quest in questions if not quest["id"].startswith("dira_")]
        st.subheader(f"Question {q_idx+1} of {len(clinical_qids)}:")
        
    st.info(q["text"])
    
    # Fetch chat memory messages for this session
    conn = database.get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT sender, message FROM chat_messages WHERE session_id = ? ORDER BY id ASC", (state["session_id"],))
    messages = cursor.fetchall()
    conn.close()
    
    for m in messages:
        with st.chat_message("user" if m[0] == "user" else "assistant"):
            st.write(m[1])
            
    is_open_ended = (len(q["options"]) == 1 and q["options"][0] == "Open-ended response") or (not q["options"])
    
    if is_open_ended:
        final_ans = st.text_area("Your response:", key=f"ans_{q_idx}", height=120)
        submitted = st.button("Submit Response", key=f"sub_{q_idx}")
    else:
        # Check if option count is large (e.g. 1-10 scale in dira_q14)
        if len(q["options"]) > 5:
            clicked = st.selectbox("Select option:", [""] + q["options"], key=f"sel_{q_idx}")
            if clicked == "":
                clicked = None
            text_ans = ""
        else:
            text_ans = st.text_input("Your reply: (or select an option below)", key=f"ans_{q_idx}")
            opt_cols = st.columns(len(q["options"]))
            clicked = None
            for i, opt in enumerate(q["options"]):
                if opt_cols[i].button(opt, key=f"btn_{q_idx}_{i}"):
                    clicked = opt
                    
        final_ans = clicked if clicked else text_ans
        submitted = st.button("Submit Response", key=f"sub_{q_idx}") or clicked
        
    if submitted:
        if not final_ans:
            st.warning("Please write or select an answer.")
        else:
            # Session Memory log user reply
            save_chat(state["session_id"], "user", final_ans)
            
            safety = agents.SafetyAgent.check_safety(final_ans)
            if safety.get("safety_trigger", False):
                st.session_state.crisis_detected = True
                st.session_state.crisis_msg = safety.get("recommended_response")
                
                conn = database.get_connection()
                cursor = conn.cursor()
                cursor.execute("""
                UPDATE assessment_sessions 
                SET status = 'completed', score = 0, severity = 'Crisis Distress', risk_level = 'high', safety_escalated = 1, safety_notes = ?, completed_at = ? 
                WHERE id = ?
                """, (f"Self-harm trigger word detected: '{final_ans}'", datetime.now().strftime("%Y-%m-%d %H:%M:%S"), state["session_id"]))
                conn.commit()
                conn.close()
                
                database.log_audit(st.session_state.user["id"], "safety_crisis_alert", "assessment_sessions", state["session_id"])
                st.rerun()
                return
                
            if is_open_ended:
                val = 0
                opt_txt = final_ans
                conn = database.get_connection()
                cursor = conn.cursor()
                cursor.execute("""
                INSERT INTO assessment_responses (session_id, question_id, response_value, response_text)
                VALUES (?, ?, ?, ?)
                """, (state["session_id"], q["id"], val, opt_txt))
                conn.commit()
                conn.close()
                
                save_chat(state["session_id"], "assistant", f"Acknowledged reflection: {opt_txt[:60]}...")
                state["answers"][q["id"]] = val
                state["q_idx"] += 1
                st.rerun()
            else:
                mapping = agents.AssessmentAgent.map_user_response(final_ans, q["options"], q["scores"])
                m_idx = mapping.get("matched_index", -1)
                
                # If mapping failed, check if there is an "Other" option to capture custom text
                if m_idx == -1:
                    other_idx = -1
                    for idx, opt in enumerate(q["options"]):
                        if "other" in opt.lower() or "free text" in opt.lower():
                            other_idx = idx
                            break
                    if other_idx != -1:
                        m_idx = other_idx
                    else:
                        # Safe non-blocking fallback to option 0
                        m_idx = 0
                
                # If they typed a custom thought (not exact option text), save their custom text
                if final_ans not in q["options"]:
                    opt_txt = final_ans
                else:
                    opt_txt = q["options"][m_idx]

                val = q["scores"][m_idx]
                conn = database.get_connection()
                cursor = conn.cursor()
                cursor.execute("""
                INSERT INTO assessment_responses (session_id, question_id, response_value, response_text)
                VALUES (?, ?, ?, ?)
                """, (state["session_id"], q["id"], val, opt_txt))
                conn.commit()
                conn.close()
                
                save_chat(state["session_id"], "assistant", f"Acknowledged response: {opt_txt}")
                state["answers"][q["id"]] = val
                state["q_idx"] += 1
                st.rerun()

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

def render_patient_rag_view():
    st.title("💬 CareMinds Wellness Coach Chat")
    st.caption("Talk to your AI Coach. Your coach is fully personalized based on your latest clinical and coaching assessment answers!")
    
    # Query latest completed session for this patient to customize the chatbot context
    profile = agents.SecurityManager.get_patient_profile(st.session_state.user["id"])
    latest_session = None
    if profile:
        try:
            conn = database.get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT s.id, s.assessment_name, s.score, s.severity, s.risk_level, 
                       sum.clinician_notes as soap_notes,
                       t.clinical_risk_summary, t.deep_narrative_insight, t.blind_spot_detection, 
                       t.strength_recognition, t.coaching_reflection, t.growth_roadmap
                FROM assessment_sessions s
                LEFT JOIN session_summaries sum ON s.id = sum.session_id
                LEFT JOIN transformational_reports t ON s.id = t.session_id
                WHERE s.patient_id = ? AND s.status = 'completed'
                ORDER BY s.completed_at DESC LIMIT 1
            """, (profile["patient_id"],))
            latest_session = cursor.fetchone()
            conn.close()
        except Exception as e:
            print(f"Error loading latest session for chat context: {e}")
            
    # Initialize coach chat history
    if "coach_chat_history" not in st.session_state:
        st.session_state.coach_chat_history = [
            {"role": "assistant", "content": "Hello! I am your CareMinds Wellness Coach. I have reviewed your latest assessment findings and am here to help you reflect, explore your growth roadmap, and discuss stress or cbt principles. What would you like to discuss today?"}
        ]
        
    # Show conversational message history
    for msg in st.session_state.coach_chat_history:
        with st.chat_message(msg["role"]):
            st.write(msg["content"])
            
    # Chat Input
    query = st.chat_input("Ask a question about cbt, psychology, stress, or self-awareness...")
    if query:
        # Display user input immediately
        with st.chat_message("user"):
            st.write(query)
        st.session_state.coach_chat_history.append({"role": "user", "content": query})
        
        # Run search query
        database.log_audit(st.session_state.user["id"], "knowledge_search", "faiss_index", 0)
        with st.spinner("Analyzing preloaded knowledge libraries..."):
            res = st.session_state.rag.search(query, top_k=4)
            
            context = ""
            citations = []
            if res:
                for r in res:
                    context += f"[Doc: {r['source']}, Page: {r['page']}]\n{r['text']}\n\n"
                    citations.append(f"- **{r['source']} (Page {r['page']})**")
            
            # Construct personalized context prefix
            personalized_prefix = ""
            if latest_session:
                personalized_prefix = f"""
You are CareMinds AI, the patient's personal Advanced Transformational Wellness Coach.
You have the following clinical and transformational report data for the patient's latest assessment:
- Assessment Type: {latest_session['assessment_name']} (Clinical Score: {latest_session['score']}, Severity: {latest_session['severity']}, Risk: {latest_session['risk_level']})
- Clinical SOAP Notes: {latest_session['soap_notes'][:400]}...
- Clinical Risk Summary: {latest_session['clinical_risk_summary']}
- Deep Narrative Insight: {latest_session['deep_narrative_insight']}
- Blind Spot Detection: {latest_session['blind_spot_detection']}
- Strength Recognition: {latest_session['strength_recognition']}
- AI Coaching Reflection: {latest_session['coaching_reflection']}
- Growth Roadmap: {latest_session['growth_roadmap']}

Incorporate these details to make your coaching highly personalized and relevant to the patient's specific limiting beliefs, strengths, and goals.
"""
            
            # Fetch RAG prompt template
            prompt_template = agents.load_prompt_file("rag_prompt.txt", DEFAULT_RAG_PROMPT)
            
            # Mix in the personalized coach prefix in user query or context
            full_context = context
            if personalized_prefix:
                full_context = f"{personalized_prefix}\n\nRetrieved Reference Materials Context:\n{context}"
                
            prompt = prompt_template.format(context=full_context, query=query)
            ans = agents.gemini_client.generate(prompt)
            
            # Append citations to answer if they are not already in it and we have citations
            if citations and "citations" not in ans.lower() and "document citations" not in ans.lower():
                ans += "\n\n**Document Citations:**\n" + "\n".join(set(citations))
                    
        with st.chat_message("assistant"):
            st.write(ans)
        st.session_state.coach_chat_history.append({"role": "assistant", "content": ans})
        st.rerun()

def render_patient_progress_view():
    st.title("📈 Evaluation Progress History")
    profile = agents.SecurityManager.get_patient_profile(st.session_state.user["id"])
    conn = database.get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, assessment_name, score, severity, risk_level, completed_at FROM assessment_sessions WHERE patient_id = ? AND status = 'completed' ORDER BY completed_at ASC;", (profile["patient_id"],))
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    if not rows: st.info("No completed assessments found.")
    else:
        scales = list(set([r["assessment_name"] for r in rows]))
        selected = st.selectbox("Select scale to graph", scales)
        filtered = [r for r in rows if r["assessment_name"] == selected]
        dates = [f["completed_at"][:10] for f in filtered]
        scores = [f["score"] for f in filtered]
        
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=dates, 
            y=scores, 
            mode="lines+markers", 
            name="Score", 
            line=dict(color="#3A41B4", width=3),
            marker=dict(size=8, color="#3B82F6", line=dict(width=2, color="#FFFFFF"))
        ))
        fig.update_layout(
            title=f"<b>Progress Trend - {selected}</b>", 
            xaxis=dict(
                title="Date",
                showgrid=True,
                gridcolor="#E5E7EB",
                linecolor="#D1D5DB",
                tickfont=dict(color="#4B5563")
            ),
            yaxis=dict(
                title="Score",
                showgrid=True,
                gridcolor="#E5E7EB",
                linecolor="#D1D5DB",
                tickfont=dict(color="#4B5563")
            ),
            plot_bgcolor="#FFFFFF",
            paper_bgcolor="#FFFFFF",
            font=dict(family="Inter, sans-serif", color="#111827"),
            margin=dict(l=40, r=40, t=55, b=45)
        )
        st.plotly_chart(fig, use_container_width=True)
        
        st.subheader("Logs Table")
        for r in reversed(filtered):
            with st.expander(f"{r['completed_at']} | Score: {r['score']} ({r['severity']})"):
                pdf_path = f"reports/session_{r['id']}_report.pdf"
                if os.path.exists(pdf_path):
                    with open(pdf_path, "rb") as f:
                        st.download_button("📥 Download report", f, file_name=f"report_{r['id']}.pdf", mime="application/pdf", key=f"progress_pdf_{r['id']}")

def render_clinician_dashboard():
    st.title("👩‍⚕️ Psychologist Patient Records")
    clinician_id = st.session_state.user["id"]
    
    # Load clinician alerts (safety escalated)
    conn = database.get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT s.id, u.full_name, s.assessment_name, s.completed_at, s.safety_notes
        FROM assessment_sessions s
        JOIN patients p ON s.patient_id = p.id
        JOIN users u ON p.user_id = u.id
        WHERE p.assigned_psychologist_id = ? AND s.safety_escalated = 1
        ORDER BY s.completed_at DESC
    """, (clinician_id,))
    alerts = cursor.fetchall()
    conn.close()
    
    if alerts:
        st.markdown('<div class="alert-header">⚠️ HIGH-RISK SAFETY CRITICAL EVENTS TRIGGERED:</div>', unsafe_allow_html=True)
        for alert in alerts:
            st.warning(f"Patient: **{alert['full_name']}** | Screening: **{alert['assessment_name']}** on {alert['completed_at']} | Message: `{alert['safety_notes']}`")
            
    # Patient dropdown list assigned to this clinician
    conn = database.get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT p.id as patient_id, u.full_name, p.dob, p.age, p.gender, p.consent_given 
        FROM patients p 
        JOIN users u ON p.user_id = u.id
        WHERE p.assigned_psychologist_id = ?
    """, (clinician_id,))
    patients = [dict(r) for r in cursor.fetchall()]
    conn.close()
    
    if not patients: 
        st.info("No patients assigned to your clinician account.")
        return
        
    p_map = {p["patient_id"]: f"{p['full_name']} (Age: {p['age']} | DOB: {p['dob']})" for p in patients}
    selected_p = st.selectbox("Select patient profile", list(p_map.keys()), format_func=lambda x: p_map[x])
    
    # Render patient progress visuals and logs
    selected_p_details = next(p for p in patients if p["patient_id"] == selected_p)
    st.markdown(f"""
    <div class="metric-card">
        <h4>Patient Case Details</h4>
        <p><b>Name:</b> {selected_p_details['full_name']} | <b>Gender:</b> {selected_p_details['gender']} | <b>Consent:</b> {'Granted' if selected_p_details['consent_given'] else 'Not Provided'}</p>
    </div>
    """, unsafe_allow_html=True)
    
    # Visual dashboards
    conn = database.get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT s.id, s.assessment_name, s.score, s.severity, s.risk_level, s.completed_at, sum.summary_text, sum.clinician_notes, sum.action_items 
        FROM assessment_sessions s 
        LEFT JOIN session_summaries sum ON s.id = sum.session_id 
        WHERE s.patient_id = ? AND s.status = 'completed'
        ORDER BY s.completed_at DESC
    """, (selected_p,))
    sessions = [dict(r) for r in cursor.fetchall()]
    conn.close()
    
    if not sessions:
        st.info("No completed assessments for this patient.")
    else:
        # Chart 1: Line Chart of Scores
        st.subheader("Psychometric Scale Score Progress")
        graph_scales = list(set([s["assessment_name"] for s in sessions]))
        sel_graph_scale = st.selectbox("Choose screening scale", graph_scales)
        
        scale_sessions = [s for s in sessions if s["assessment_name"] == sel_graph_scale]
        dates = [s["completed_at"][:10] for s in scale_sessions]
        scores = [s["score"] for s in scale_sessions]
        
        fig_line = px.line(
            x=dates, 
            y=scores, 
            markers=True, 
            title=f"<b>Symptom Trend - {sel_graph_scale}</b>",
            labels={"x": "Date Completed", "y": "Assessment Score"}
        )
        fig_line.update_traces(
            line_color="#3A41B4", 
            line_width=3,
            marker=dict(size=8, color="#3B82F6", line=dict(width=2, color="#FFFFFF"))
        )
        fig_line.update_layout(
            plot_bgcolor="#FFFFFF",
            paper_bgcolor="#FFFFFF",
            font=dict(family="Inter, sans-serif", color="#111827"),
            xaxis=dict(
                showgrid=True, 
                gridcolor="#E5E7EB", 
                linecolor="#D1D5DB", 
                tickfont=dict(color="#4B5563")
            ),
            yaxis=dict(
                showgrid=True, 
                gridcolor="#E5E7EB", 
                linecolor="#D1D5DB", 
                tickfont=dict(color="#4B5563")
            ),
            margin=dict(l=40, r=40, t=55, b=45)
        )
        st.plotly_chart(fig_line, use_container_width=True)
        
        # Chart 2: Aggregated Clinician Statistics (assigned patients summary)
        st.subheader("Overall assigned cases dashboard statistics")
        col_stat1, col_stat2 = st.columns(2)
        
        # Load global clinician stats
        conn = database.get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT s.risk_level, count(*) 
            FROM assessment_sessions s 
            JOIN patients p ON s.patient_id = p.id 
            WHERE p.assigned_psychologist_id = ? AND s.status = 'completed' 
            GROUP BY s.risk_level
        """, (clinician_id,))
        risk_stats = cursor.fetchall()
        
        cursor.execute("""
            SELECT s.assessment_name, count(*) 
            FROM assessment_sessions s 
            JOIN patients p ON s.patient_id = p.id 
            WHERE p.assigned_psychologist_id = ? AND s.status = 'completed' 
            GROUP BY s.assessment_name
        """, (clinician_id,))
        scale_stats = cursor.fetchall()
        conn.close()
        
        with col_stat1:
            if risk_stats:
                risk_names = [r[0] for r in risk_stats]
                color_map = {
                    "low": "#10B981",
                    "moderate": "#F59E0B",
                    "high": "#EF4444",
                    "critical": "#991B1B"
                }
                pie_colors = [color_map.get(name.lower(), "#6B7280") for name in risk_names]
                
                fig_pie = px.pie(
                    names=risk_names, 
                    values=[r[1] for r in risk_stats], 
                    title="<b>Case Risk Levels Distribution</b>"
                )
                fig_pie.update_traces(
                    marker=dict(colors=pie_colors, line=dict(color='#FFFFFF', width=2)),
                    textinfo='percent+label',
                    textposition='inside'
                )
                fig_pie.update_layout(
                    paper_bgcolor="#FFFFFF",
                    font=dict(family="Inter, sans-serif", color="#111827"),
                    title=dict(font=dict(size=14, color="#111827")),
                    legend=dict(font=dict(color="#4B5563")),
                    margin=dict(l=20, r=20, t=55, b=20)
                )
                st.plotly_chart(fig_pie, use_container_width=True)
            else:
                st.info("No risk data logged.")
        with col_stat2:
            if scale_stats:
                fig_bar = px.bar(
                    x=[r[0] for r in scale_stats], 
                    y=[r[1] for r in scale_stats], 
                    title="<b>Sessions Volume per Psychometric Scale</b>",
                    labels={"x": "Screening Scale", "y": "Completed Sessions"}
                )
                fig_bar.update_traces(
                    marker_color="#3B82F6",
                    marker_line=dict(width=1.5, color="#2563EB")
                )
                fig_bar.update_layout(
                    plot_bgcolor="#FFFFFF",
                    paper_bgcolor="#FFFFFF",
                    font=dict(family="Inter, sans-serif", color="#111827"),
                    xaxis=dict(
                        showgrid=False, 
                        linecolor="#D1D5DB", 
                        tickfont=dict(color="#4B5563")
                    ),
                    yaxis=dict(
                        showgrid=True, 
                        gridcolor="#E5E7EB", 
                        linecolor="#D1D5DB", 
                        tickfont=dict(color="#4B5563")
                    ),
                    margin=dict(l=20, r=20, t=55, b=20)
                )
                st.plotly_chart(fig_bar, use_container_width=True)
            else:
                st.info("No sessions volumes logged.")
                
        st.subheader("Logs")
        for s in sessions:
            with st.expander(f"{s['assessment_name']} on {s['completed_at']} | Score: {s['score']} ({s['severity']}) Risk: {s['risk_level'].upper()}"):
                st.write(f"**Summary:** {s['summary_text']}")
                st.markdown(s["clinician_notes"] or "SOAP details not calculated.")
                
                # Fetch Layer 2 DIRA transformational insights if available
                conn = database.get_connection()
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM transformational_reports WHERE session_id = ?", (s["id"],))
                t_report = cursor.fetchone()
                conn.close()
                
                if t_report:
                    st.markdown("---")
                    st.markdown("##### 🌱 Layer 2: Transformational Coaching Insights")
                    
                    # Display metrics in cols
                    tdims = ["Resilience", "Self-Awareness", "Agency", "Flexibility", "Growth Mindset", "Relationships", "Purpose", "Optimism"]
                    tvals = [
                        t_report["emotional_resilience"], t_report["self_awareness"], t_report["personal_agency"],
                        t_report["cognitive_flexibility"], t_report["growth_mindset"], t_report["relationship_health"],
                        t_report["purpose_alignment"], t_report["future_optimism"]
                    ]
                    tcols = st.columns(4)
                    for t_idx, (tdim, tval) in enumerate(zip(tdims, tvals)):
                        tcols[t_idx % 4].metric(tdim, f"{tval}/100")
                        
                    st.markdown(f"**Clinical Risk Summary:** {t_report['clinical_risk_summary']}")
                    st.markdown(f"**Deep Narrative Insight:** {t_report['deep_narrative_insight']}")
                    st.markdown(f"**Blind Spot Detection:** {t_report['blind_spot_detection']}")
                    st.markdown(f"**Strength Recognition:** {t_report['strength_recognition']}")
                    st.markdown(f"**AI Coaching Reflection:** {t_report['coaching_reflection']}")
                    st.markdown(f"**Personalized Growth Roadmap:** {t_report['growth_roadmap']}")
                
                # Exporters
                pdf_path = f"reports/session_{s['id']}_report.pdf"
                if os.path.exists(pdf_path):
                    with open(pdf_path, "rb") as f:
                        st.download_button("Download PDF Record", f, file_name=f"report_{s['id']}.pdf", mime="application/pdf", key=f"clin_pdf_{s['id']}")
                else:
                    if st.button("Generate PDF Report Record", key=f"c_pdf_gen_{s['id']}"):
                        report_generator.ReportGenerator.generate_assessment_report(s["id"])
                        st.success("Report generated. Reload page.")
                        
                # JSON exporter
                json_data = {
                    "session_id": s["id"],
                    "assessment": s["assessment_name"],
                    "score": s["score"],
                    "severity": s["severity"],
                    "risk_level": s["risk_level"],
                    "date": s["completed_at"],
                    "SOAP": s["clinician_notes"]
                }
                st.download_button("Export JSON Record", json.dumps(json_data, indent=4), file_name=f"report_{s['id']}.json", mime="application/json", key=f"clin_json_{s['id']}")

def render_admin_pdfs():
    st.title("🔑 Admin RAG reference library uploads")
    st.subheader("Incremental RAG PDF Indexer (MD5 hashing duplicate checks enabled)")
    
    cat = st.selectbox("Select Knowledge Folder Category:", ["psychology", "cbt", "guidelines", "assessments"])
    uploaded = st.file_uploader("Choose a Clinical Reference PDF file to upload", type="pdf")
    if uploaded is not None:
        dest_folder = f"knowledge_base/{cat}"
        dest = os.path.join(dest_folder, uploaded.name)
        with open(dest, "wb") as f: 
            f.write(uploaded.getbuffer())
        st.success(f"Saved file to {dest}")
        
        if st.button("Reindex RAG database with this PDF"):
            database.log_audit(st.session_state.user["id"], "document_upload", "faiss_index", 0)
            with st.spinner("Generating sentence transformer embeddings and updating FAISS index..."):
                chunks = st.session_state.rag.add_pdf(dest)
            if chunks > 0: st.success(f"Successfully indexed {chunks} new text chunks.")
            else: st.info("File is already indexed and unchanged (Skipped to prevent duplicate re-embedding).")

def render_wellness_chatbot():
    # If the user has completed any assessment, render the Personalized Coach Chat.
    # Otherwise, render the intake/diagnostic chat recommendation flow.
    has_completed = has_completed_any_assessment(st.session_state.user["id"])
    if has_completed:
        render_patient_rag_view()
    else:
        render_diagnostic_chat()

def render_footer():
    st.markdown("""
    <style>
        .footer-container {
            background-color: #1F2937;
            color: #FFFFFF !important;
            padding: 40px 24px 20px 24px;
            margin-top: 80px;
            border-radius: 8px;
            font-family: 'Inter', sans-serif;
        }
        .footer-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 32px;
            margin-bottom: 40px;
        }
        .footer-col h4 {
            color: #FFFFFF !important;
            font-size: 1.1rem;
            font-weight: 600;
            margin-bottom: 16px;
            margin-top: 0;
        }
        .footer-col ul {
            list-style: none;
            padding: 0;
            margin: 0;
        }
        .footer-col ul li {
            margin-bottom: 8px;
        }
        .footer-col ul li a {
            color: #9CA3AF !important;
            text-decoration: none;
            transition: color 0.15s ease-in-out;
        }
        .footer-col ul li a:hover {
            color: #3B82F6 !important;
        }
        .footer-bottom {
            border-top: 1px solid #374151;
            padding-top: 20px;
            display: flex;
            flex-wrap: wrap;
            justify-content: space-between;
            align-items: center;
            color: #9CA3AF !important;
            font-size: 0.875rem;
        }
    </style>
    <div class="footer-container">
        <div class="footer-grid">
            <div class="footer-col">
                <h4>Solutions</h4>
                <ul>
                    <li><a href="#">Psychometric Assessments</a></li>
                    <li><a href="#">Capability Diagnostics</a></li>
                    <li><a href="#">Talent Analytics</a></li>
                    <li><a href="#">SOAP Notes Integration</a></li>
                </ul>
            </div>
            <div class="footer-col">
                <h4>Platform</h4>
                <ul>
                    <li><a href="#">Assessment Center</a></li>
                    <li><a href="#">Wellness Chatbot</a></li>
                    <li><a href="#">Psychologist Dashboard</a></li>
                    <li><a href="#">Security Auditor</a></li>
                </ul>
            </div>
            <div class="footer-col">
                <h4>Resources</h4>
                <ul>
                    <li><a href="#">CBT Practice Guides</a></li>
                    <li><a href="#">Clinical Reference PDF Library</a></li>
                    <li><a href="#">Security Audit Logs</a></li>
                    <li><a href="#">Documentation</a></li>
                </ul>
            </div>
            <div class="footer-col">
                <h4>Risen PsyLabs</h4>
                <p style="color: #9CA3AF !important; font-size: 0.9rem; line-height: 1.5; margin-bottom: 12px; margin-top: 0;">
                    Behavioral science–led consulting firm specializing in psychometric assessments and capability diagnostics.
                </p>
                <p style="color: #9CA3AF !important; font-size: 0.85rem; line-height: 1.4; margin: 0;">
                    100 Science Park, Suite 400<br>
                    Boston, MA 02110<br>
                    Email: info@risenpsylabs.com<br>
                    Phone: (555) 123-4567
                </p>
            </div>
        </div>
        <div class="footer-bottom">
            <div>&copy; 2026 CareMinds AI / Risen PsyLabs. All rights reserved.</div>
            <div style="display: flex; gap: 16px;">
                <a href="#" style="color: #9CA3AF !important; text-decoration: none;">Privacy Policy</a>
                <a href="#" style="color: #9CA3AF !important; text-decoration: none;">Terms of Service</a>
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)

def main():
    if not st.session_state.logged_in:
        render_auth()
    else:
        nav = render_sidebar()
        if nav == "Intake & Consent": render_intake()
        elif nav == "Wellness Chatbot": render_wellness_chatbot()
        elif nav == "Assessment Center": render_assessments()
        elif nav == "My Progress": render_patient_progress_view()
        elif nav == "Patient Dashboard": render_clinician_dashboard()
        elif nav == "Resource Administrator": render_admin_pdfs()
        elif nav == "Database Auditor":
            st.title("🔑 Security audit logs")
            conn = database.get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT a.id, u.username, a.action, a.target_table, a.timestamp FROM audit_logs a LEFT JOIN users u ON a.user_id = u.id ORDER BY a.id DESC LIMIT 100;")
            logs = cursor.fetchall()
            conn.close()
            st.dataframe([dict(l) for l in logs], use_container_width=True)
        
        # Render the custom footer
        render_footer()

if __name__ == '__main__': main()
