import sqlite3
import os
from datetime import datetime
import config

def get_connection():
    if config.DB_TYPE == "sqlite":
        conn = sqlite3.connect(config.SQLITE_PATH)
        conn.row_factory = sqlite3.Row
        return conn
    else:
        raise NotImplementedError("Supabase connection logic not configured.")

def init_db():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("PRAGMA foreign_keys = ON;")
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL,
        full_name TEXT NOT NULL,
        email TEXT NOT NULL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    """)
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS patients (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER UNIQUE NOT NULL,
        dob TEXT NOT NULL DEFAULT '',
        age INTEGER DEFAULT 30,
        gender TEXT NOT NULL DEFAULT '',
        phone TEXT NOT NULL DEFAULT '',
        consent_given INTEGER NOT NULL DEFAULT 0,
        consent_date TEXT,
        assigned_psychologist_id INTEGER,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
        FOREIGN KEY (assigned_psychologist_id) REFERENCES users(id)
    );
    """)
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS assessment_sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        patient_id INTEGER NOT NULL,
        assessment_name TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('started', 'completed', 'abandoned')),
        score INTEGER,
        severity TEXT,
        risk_level TEXT DEFAULT 'low',
        safety_escalated INTEGER DEFAULT 0,
        safety_notes TEXT DEFAULT '',
        started_at TEXT NOT NULL,
        completed_at TEXT,
        FOREIGN KEY (patient_id) REFERENCES patients(id) ON DELETE CASCADE
    );
    """)
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS assessment_responses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id INTEGER NOT NULL,
        question_id TEXT NOT NULL,
        response_value INTEGER NOT NULL,
        response_text TEXT NOT NULL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (session_id) REFERENCES assessment_sessions(id) ON DELETE CASCADE
    );
    """)
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS session_summaries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id INTEGER UNIQUE NOT NULL,
        summary_text TEXT NOT NULL,
        clinician_notes TEXT NOT NULL,
        action_items TEXT NOT NULL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (session_id) REFERENCES assessment_sessions(id) ON DELETE CASCADE
    );
    """)
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS chat_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id INTEGER NOT NULL,
        sender TEXT NOT NULL,
        message TEXT NOT NULL,
        timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (session_id) REFERENCES assessment_sessions(id) ON DELETE CASCADE
    );
    """)
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS audit_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        action TEXT NOT NULL,
        target_table TEXT,
        target_id INTEGER,
        timestamp TEXT DEFAULT CURRENT_TIMESTAMP
    );
    """)
    
    cursor.execute("SELECT count(*) FROM users;")
    if cursor.fetchone()[0] == 0:
        users_to_seed = [
            ('admin', '240be518fabd2724ddb6f04eeb1da5967448d7e831c08c8fa822809f74c720a9', 'admin', 'System Admin', 'admin@careminds.ai'),
            ('dr_smith', 'c108533d4511b3e7263207ace80f6b4be9dd983898514f1dbc075c9b6a83bae1', 'psychologist', 'Dr. Smith', 'smith@careminds.ai'),
            ('john_doe', 'd4587ea9ead060c13fd994f21ecfa7926272a78854a2c20136b10a3c9e53e71e', 'patient', 'John Doe', 'johndoe@email.com')
        ]
        for username, password_hash, role, full_name, email in users_to_seed:
            cursor.execute("""
            INSERT INTO users (username, password_hash, role, full_name, email)
            VALUES (?, ?, ?, ?, ?);
            """, (username, password_hash, role, full_name, email))
            u_id = cursor.lastrowid
            if role == 'patient':
                cursor.execute("""
                INSERT INTO patients (user_id, dob, age, gender, phone, consent_given, consent_date, assigned_psychologist_id)
                VALUES (?, '1992-08-20', 33, 'Male', '+15550199', 1, '2026-06-17 12:00:00', 2);
                """, (u_id,))
        print("Database seeded with default credentials.")
        
    conn.commit()
    conn.close()

def log_audit(user_id, action, target_table=None, target_id=None):
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
        INSERT INTO audit_logs (user_id, action, target_table, target_id)
        VALUES (?, ?, ?, ?);
        """, (user_id, action, target_table, target_id))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Audit log failed: {e}")
