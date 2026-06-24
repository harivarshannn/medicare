import os
DB_TYPE = os.environ.get("DB_TYPE", "postgresql")
SQLITE_PATH = "data/mental_health_platform.db"
SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://your-project.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "your-supabase-anon-key")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
ROLE_PATIENT = "patient"
ROLE_PSYCHOLOGIST = "psychologist"
ROLE_ADMIN = "admin"
PROMPTS_DIR = "prompts"

# PostgreSQL connection parameters
PG_HOST = os.environ.get("PG_HOST", "localhost")
PG_PORT = os.environ.get("PG_PORT", "5432")
PG_DB = os.environ.get("PG_DB", "careminds_db")
PG_USER = os.environ.get("PG_USER", "postgres")
PG_PASSWORD = os.environ.get("PG_PASSWORD", "hari")

