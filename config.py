import os
DB_TYPE = "sqlite"
SQLITE_PATH = "data/mental_health_platform.db"
SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://your-project.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "your-supabase-anon-key")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
ROLE_PATIENT = "patient"
ROLE_PSYCHOLOGIST = "psychologist"
ROLE_ADMIN = "admin"
PROMPTS_DIR = "prompts"
