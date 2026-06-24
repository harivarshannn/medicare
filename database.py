import sqlite3
import os
import psycopg2
import psycopg2.extras
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
from datetime import datetime
import config

class PostgreSqlCursorWrapper:
    def __init__(self, real_cursor):
        self.real_cursor = real_cursor
        self._lastrowid = None

    def execute(self, query, vars=None):
        # Convert sqlite placeholders '?' to postgres '%s'
        query = query.replace('?', '%s')
        
        # Replace SQLite specific syntax
        if "PRAGMA" in query.upper():
            query = "SELECT 1;"
            
        if "CREATE TABLE" in query.upper():
            query = query.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
            query = query.replace("AUTOINCREMENT", "")
            query = query.replace("TEXT DEFAULT CURRENT_TIMESTAMP", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
            query = query.replace("timestamp TEXT DEFAULT CURRENT_TIMESTAMP", "timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
            
        self.real_cursor.execute(query, vars)
        
        # Capture last inserted ID if it's an INSERT
        if query.strip().upper().startswith("INSERT"):
            try:
                conn = self.real_cursor.connection
                with conn.cursor() as temp_cur:
                    temp_cur.execute("SELECT lastval()")
                    self._lastrowid = temp_cur.fetchone()[0]
            except Exception:
                pass
        return self

    def executemany(self, query, vars_list):
        query = query.replace('?', '%s')
        self.real_cursor.executemany(query, vars_list)
        return self

    def fetchone(self):
        row = self.real_cursor.fetchone()
        if row is None:
            return None
        return row

    def fetchall(self):
        return self.real_cursor.fetchall()

    def fetchmany(self, size=None):
        if size is None:
            return self.real_cursor.fetchmany()
        return self.real_cursor.fetchmany(size)

    @property
    def lastrowid(self):
        return self._lastrowid

    def __iter__(self):
        return iter(self.real_cursor)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.real_cursor.close()

    def close(self):
        self.real_cursor.close()

    @property
    def rowcount(self):
        return self.real_cursor.rowcount

class PostgreSqlConnectionWrapper:
    def __init__(self, real_conn):
        self.real_conn = real_conn

    def cursor(self, *args, **kwargs):
        if 'cursor_factory' not in kwargs:
            kwargs['cursor_factory'] = psycopg2.extras.DictCursor
        real_cursor = self.real_conn.cursor(*args, **kwargs)
        return PostgreSqlCursorWrapper(real_cursor)

    def commit(self):
        self.real_conn.commit()

    def rollback(self):
        self.real_conn.rollback()

    def close(self):
        self.real_conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.real_conn.close()

def create_database_if_not_exists():
    try:
        # Connect to system postgres database first to check/create the target database
        conn = psycopg2.connect(
            host=config.PG_HOST,
            port=config.PG_PORT,
            user=config.PG_USER,
            password=config.PG_PASSWORD,
            database="postgres"
        )
        conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        cursor = conn.cursor()
        
        # Check if DB exists
        cursor.execute("SELECT 1 FROM pg_catalog.pg_database WHERE datname = %s", (config.PG_DB,))
        exists = cursor.fetchone()
        
        if not exists:
            print(f"[database] Creating database '{config.PG_DB}'...")
            cursor.execute(f'CREATE DATABASE "{config.PG_DB}"')
            print("[database] Database created successfully.")
        
        cursor.close()
        conn.close()
    except Exception as e:
        print(f"[database] Warning: Could not verify/create database '{config.PG_DB}': {e}")

def get_connection():
    if config.DB_TYPE == "sqlite":
        conn = sqlite3.connect(config.SQLITE_PATH)
        conn.row_factory = sqlite3.Row
        return conn
    elif config.DB_TYPE == "postgresql":
        create_database_if_not_exists()
        real_conn = psycopg2.connect(
            host=config.PG_HOST,
            port=config.PG_PORT,
            user=config.PG_USER,
            password=config.PG_PASSWORD,
            database=config.PG_DB
        )
        return PostgreSqlConnectionWrapper(real_conn)
    else:
        raise NotImplementedError(f"Database type '{config.DB_TYPE}' is not supported.")

def init_db():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("PRAGMA foreign_keys = ON;")
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id SERIAL PRIMARY KEY,
        username VARCHAR(100) UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        role VARCHAR(50) NOT NULL,
        full_name VARCHAR(150) NOT NULL,
        email VARCHAR(150) NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS patients (
        id SERIAL PRIMARY KEY,
        user_id INTEGER UNIQUE NOT NULL,
        dob VARCHAR(50) NOT NULL DEFAULT '',
        age INTEGER DEFAULT 30,
        gender VARCHAR(50) NOT NULL DEFAULT '',
        phone VARCHAR(50) NOT NULL DEFAULT '',
        consent_given INTEGER NOT NULL DEFAULT 0,
        consent_date VARCHAR(50),
        assigned_psychologist_id INTEGER,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
        FOREIGN KEY (assigned_psychologist_id) REFERENCES users(id)
    );
    """)
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS assessment_sessions (
        id SERIAL PRIMARY KEY,
        patient_id INTEGER NOT NULL,
        assessment_name VARCHAR(100) NOT NULL,
        status VARCHAR(50) NOT NULL CHECK (status IN ('started', 'completed', 'abandoned')),
        score INTEGER,
        severity VARCHAR(100),
        risk_level VARCHAR(50) DEFAULT 'low',
        safety_escalated INTEGER DEFAULT 0,
        safety_notes TEXT DEFAULT '',
        started_at VARCHAR(100) NOT NULL,
        completed_at VARCHAR(100),
        FOREIGN KEY (patient_id) REFERENCES patients(id) ON DELETE CASCADE
    );
    """)
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS assessment_responses (
        id SERIAL PRIMARY KEY,
        session_id INTEGER NOT NULL,
        question_id VARCHAR(100) NOT NULL,
        response_value INTEGER NOT NULL,
        response_text TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (session_id) REFERENCES assessment_sessions(id) ON DELETE CASCADE
    );
    """)
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS session_summaries (
        id SERIAL PRIMARY KEY,
        session_id INTEGER UNIQUE NOT NULL,
        summary_text TEXT NOT NULL,
        clinician_notes TEXT NOT NULL,
        action_items TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (session_id) REFERENCES assessment_sessions(id) ON DELETE CASCADE
    );
    """)
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS chat_messages (
        id SERIAL PRIMARY KEY,
        session_id INTEGER NOT NULL,
        sender VARCHAR(50) NOT NULL,
        message TEXT NOT NULL,
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (session_id) REFERENCES assessment_sessions(id) ON DELETE CASCADE
    );
    """)
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS audit_logs (
        id SERIAL PRIMARY KEY,
        user_id INTEGER,
        action VARCHAR(100) NOT NULL,
        target_table VARCHAR(100),
        target_id INTEGER,
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS transformational_reports (
        id SERIAL PRIMARY KEY,
        session_id INTEGER UNIQUE NOT NULL,
        emotional_resilience INTEGER,
        self_awareness INTEGER,
        personal_agency INTEGER,
        cognitive_flexibility INTEGER,
        growth_mindset INTEGER,
        relationship_health INTEGER,
        purpose_alignment INTEGER,
        future_optimism INTEGER,
        clinical_risk_summary TEXT,
        deep_narrative_insight TEXT,
        blind_spot_detection TEXT,
        strength_recognition TEXT,
        coaching_reflection TEXT,
        growth_roadmap TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (session_id) REFERENCES assessment_sessions(id) ON DELETE CASCADE
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
