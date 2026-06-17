import json
import re
import os
import sqlite3
from groq import Groq
import config
import database

def load_prompt_file(filename, fallback):
    path = os.path.join("prompts", filename)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    return fallback

def parse_gemini_json(text):
    text = text.strip()
    match = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL)
    if match:
        json_str = match.group(1)
    else:
        match = re.search(r"```\s*(.*?)\s*```", text, re.DOTALL)
        if match:
            json_str = match.group(1)
        else:
            json_str = text
    try:
        return json.loads(json_str)
    except Exception:
        try:
            match = re.search(r"(\{.*\}|\[.*\])", json_str, re.DOTALL)
            if match:
                return json.loads(match.group(1))
        except:
            pass
        return {}

class GroqClient:
    def __init__(self):
        self.api_key = config.GROQ_API_KEY
        if not self.api_key:
            self.api_key = os.environ.get("GROQ_API_KEY", "")
        self.client = None
        self.model_name = "llama-3.3-70b-versatile"
        if self.api_key:
            try:
                self.client = Groq(api_key=self.api_key)
            except Exception as e:
                print(f"Failed to initialize Groq Client: {e}")

    def generate(self, prompt, system_instruction=None):
        if not self.client:
            self.api_key = config.GROQ_API_KEY or os.environ.get("GROQ_API_KEY", "")
            if self.api_key:
                try:
                    self.client = Groq(api_key=self.api_key)
                except Exception as e:
                    return f"Groq API Key initialization error: {str(e)}"
            else:
                return "Groq API Key is not set. Running in local fallback mode."
        
        try:
            messages = []
            if system_instruction:
                messages.append({"role": "system", "content": system_instruction})
            messages.append({"role": "user", "content": prompt})
            
            chat_completion = self.client.chat.completions.create(
                messages=messages,
                model=self.model_name,
            )
            return chat_completion.choices[0].message.content
        except Exception as e:
            return f"Groq Error: {str(e)}"

gemini_client = GroqClient()

class SecurityManager:
    @staticmethod
    def hash_password(password):
        import hashlib
        return hashlib.sha256(password.encode()).hexdigest()

    @staticmethod
    def verify_password(password, hashed):
        return SecurityManager.hash_password(password) == hashed

    @staticmethod
    def register_user(username, password, role, full_name, email):
        conn = database.get_connection()
        cursor = conn.cursor()
        password_hash = SecurityManager.hash_password(password)
        try:
            cursor.execute("""
            INSERT INTO users (username, password_hash, role, full_name, email)
            VALUES (?, ?, ?, ?, ?)
            """, (username, password_hash, role, full_name, email))
            user_id = cursor.lastrowid
            if role == config.ROLE_PATIENT:
                cursor.execute("""
                INSERT INTO patients (user_id, dob, age, gender, phone, consent_given, assigned_psychologist_id)
                VALUES (?, '', 30, '', '', 0, 2)
                """, (user_id,))
            conn.commit()
            database.log_audit(user_id, "user_registration", "users", user_id)
            return user_id, None
        except sqlite3.IntegrityError:
            return None, "Username already exists."
        finally:
            conn.close()

    @staticmethod
    def authenticate_user(username, password):
        conn = database.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE username = ?", (username,))
        user = cursor.fetchone()
        conn.close()
        if user and SecurityManager.verify_password(password, user['password_hash']):
            database.log_audit(user['id'], "user_login", "users", user['id'])
            return dict(user)
        return None

    @staticmethod
    def update_patient_profile(user_id, dob, gender, phone, consent_given):
        from datetime import datetime
        conn = database.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM users WHERE id = ?", (user_id,))
        if not cursor.fetchone():
            conn.close()
            return False, "User not found."
        cursor.execute("SELECT id FROM patients WHERE user_id = ?", (user_id,))
        patient = cursor.fetchone()
        consent_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S") if consent_given else None
        
        # Calculate approximate age
        age = 30
        try:
            birth_year = int(dob.split("-")[0])
            age = datetime.now().year - birth_year
        except:
            pass
            
        if patient:
            cursor.execute("""
            UPDATE patients
            SET dob = ?, age = ?, gender = ?, phone = ?, consent_given = ?, consent_date = ?
            WHERE user_id = ?
            """, (dob, age, gender, phone, int(consent_given), consent_date, user_id))
            patient_id = patient['id']
        else:
            cursor.execute("""
            INSERT INTO patients (user_id, dob, age, gender, phone, consent_given, consent_date, assigned_psychologist_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, 2)
            """, (user_id, dob, age, gender, phone, int(consent_given), consent_date))
            patient_id = cursor.lastrowid
        conn.commit()
        database.log_audit(user_id, "update_patient_profile", "patients", patient_id)
        conn.close()
        return True, "Profile saved."

    @staticmethod
    def get_patient_profile(user_id):
        conn = database.get_connection()
        cursor = conn.cursor()
        cursor.execute("""
        SELECT u.id as user_id, u.username, u.full_name, u.email, u.role,
               p.id as patient_id, p.dob, p.age, p.gender, p.phone, p.consent_given, p.consent_date, p.assigned_psychologist_id
        FROM users u
        LEFT JOIN patients p ON u.id = p.user_id
        WHERE u.id = ?
        """, (user_id,))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

class SafetyAgent:
    @staticmethod
    def check_safety(patient_input):
        fallback = """Analyze for suicide or self harm risk. Return strictly JSON with safety_trigger key."""
        instructions = load_prompt_file("safety_prompt.txt", fallback)
        prompt = instructions.format(patient_input=patient_input)
        raw_output = gemini_client.generate(prompt, "Check crisis flags. Output JSON.")
        
        if not gemini_client.model_name or "Gemini API Key" in raw_output:
            triggers = ["suicide", "kill myself", "want to die", "harm myself", "end my life", "cut myself", "overdose", "hang myself"]
            triggered = any(t in patient_input.lower() for t in triggers)
            return {
                "safety_trigger": triggered,
                "trigger_category": "suicidal_ideation" if triggered else "none",
                "risk_level": "high" if triggered else "low",
                "action_required": triggered,
                "recommended_response": "WARNING: Safety concern identified. Please consult a qualified mental health professional or immediately contact the Suicide & Crisis Lifeline by dialing 988. You do not have to carry this alone."
            }
        return parse_gemini_json(raw_output)

class ScoringEngine:
    @staticmethod
    def score_session(session_id, scale_data):
        conn = database.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT question_id, response_value FROM assessment_responses WHERE session_id = ?;", (session_id,))
        rows = cursor.fetchall()
        conn.close()
        responses = {r["question_id"]: r["response_value"] for r in rows}
        scoring_type = scale_data["scoring"]["type"]
        total_score = 0
        if scoring_type == "sum":
            total_score = sum(responses.values())
        elif scoring_type == "percentage_who5":
            total_score = sum(responses.values()) * 4
            
        severity = "Unknown"
        risk_level = "low"
        for b in scale_data["scoring"]["brackets"]:
            if b["min"] <= total_score <= b["max"]:
                severity = b["severity"]
                risk_level = b["risk"]
                break
        risk_trigger = scale_data["scoring"].get("risk_trigger")
        if risk_trigger:
            trigger_qid = risk_trigger["question_id"]
            if trigger_qid in responses and responses[trigger_qid] >= risk_trigger["threshold"]:
                risk_level = risk_trigger["trigger_severity"]
        return total_score, severity, risk_level

class AssessmentAgent:
    @staticmethod
    def map_user_response(user_text, options, scores):
        # 1. Local matching first (handles direct button clicks & exact option typing immediately)
        user_clean = re.sub(r'[^\w\s]', '', user_text).strip().lower()
        for idx, opt in enumerate(options):
            opt_clean = re.sub(r'[^\w\s]', '', opt).strip().lower()
            if user_clean == opt_clean:
                return {"matched_index": idx}
        for idx in range(len(options)):
            if user_clean == str(idx):
                return {"matched_index": idx}

        # 2. Call Gemini for conversational mapping
        options_str = "\n".join([f"- {idx}: {opt}" for idx, opt in enumerate(options)])
        fallback = """Map user response to option index. Return JSON: {\"matched_index\": int}"""
        instructions = load_prompt_file("assessment_prompt.txt", fallback)
        prompt = f'{instructions}\nMap User Response: "{user_text}" to options:\n{options_str}'
        raw_output = gemini_client.generate(prompt, "Map choices. Output JSON.")
        
        # If client is not set or Gemini returned an error, fallback to local search
        if not gemini_client.model_name or "Gemini API Key" in raw_output or "Gemini Error" in raw_output:
            for idx, opt in enumerate(options):
                opt_clean = re.sub(r'[^\w\s]', '', opt).strip().lower()
                if opt_clean in user_clean:
                    return {"matched_index": idx}
            for i in range(len(options)):
                if str(i) in user_clean:
                    return {"matched_index": i}
            return {"matched_index": -1}
            
        parsed = parse_gemini_json(raw_output)
        if "matched_index" in parsed and isinstance(parsed["matched_index"], int) and 0 <= parsed["matched_index"] < len(options):
            return parsed
            
        # Fallback if parsed json is invalid or missing matched_index
        for idx, opt in enumerate(options):
            opt_clean = re.sub(r'[^\w\s]', '', opt).strip().lower()
            if opt_clean in user_clean:
                return {"matched_index": idx}
        for i in range(len(options)):
            if str(i) in user_clean:
                return {"matched_index": i}
        return {"matched_index": -1}

class SessionSummarizerAgent:
    @staticmethod
    def generate_clinical_notes(session_id):
        conn = database.get_connection()
        cursor = conn.cursor()
        cursor.execute("""
        SELECT s.assessment_name, s.score, s.severity, s.risk_level,
               p.dob, p.gender, u.full_name as patient_name
        FROM assessment_sessions s
        JOIN patients p ON s.patient_id = p.id
        JOIN users u ON p.user_id = u.id
        WHERE s.id = ?
        """, (session_id,))
        session = cursor.fetchone()
        if not session:
            conn.close()
            return
        cursor.execute("SELECT question_id, response_text, response_value FROM assessment_responses WHERE session_id = ?;", (session_id,))
        responses = cursor.fetchall()
        conn.close()
        resp_table = ""
        for r in responses:
            resp_table += f"- Q {r['question_id']}: {r['response_text']} (Weight: {r['response_value']})\n"
        
        fallback = """Write SOAP notes. Format with headers Subjective, Objective, Assessment, Plan."""
        instructions = load_prompt_file("report_prompt.txt", fallback)
        prompt = instructions.format(
            assessment_name=session["assessment_name"],
            patient_name=session["patient_name"],
            dob=session["dob"],
            gender=session["gender"],
            score=session["score"],
            severity=session["severity"],
            risk_level=session["risk_level"],
            responses_table=resp_table
        )
        soap_notes = gemini_client.generate(prompt, "Draft SOAP clinical notes.")
        summary_text = f"Completed assessment {session['assessment_name']}. Score: {session['score']}."
        
        action_items = "Follow clinical recommendations."
        action_split = soap_notes.lower().split("plan**")
        if len(action_split) > 1:
            action_items = action_split[1].replace("**", "").strip()
            
        conn = database.get_connection()
        cursor = conn.cursor()
        cursor.execute("""
        INSERT OR REPLACE INTO session_summaries (session_id, summary_text, clinician_notes, action_items)
        VALUES (?, ?, ?, ?)
        """, (session_id, summary_text, soap_notes, action_items))
        conn.commit()
        conn.close()
        return summary_text, soap_notes, action_items
