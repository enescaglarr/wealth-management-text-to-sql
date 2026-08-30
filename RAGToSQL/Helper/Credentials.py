import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")


class Credentials:
    """Configuration for the whole project, read from .env."""

    # --- database (SQL Server)
    server = os.getenv("DB_SERVER", "localhost")
    port = os.getenv("DB_PORT", "1433")
    database = os.getenv("DB_NAME", "wealth_management")
    user = os.getenv("DB_USER", "sa")
    password = os.getenv("DB_PASSWORD", "")

    # --- LLM (Groq)
    llm_api_key = os.getenv("GROQ_API_KEY", "")
    llm_base_url = os.getenv("LLM_BASE_URL", "https://api.groq.com/openai/v1")
    model = os.getenv("LLM_MODEL", "openai/gpt-oss-120b")

    @staticmethod
    def connection_string() -> str:
        c = Credentials
        return ("Driver={ODBC Driver 18 for SQL Server};"
                f"Server={c.server},{c.port};Database={c.database};UID={c.user};PWD={c.password};"
                "Encrypt=Yes;TrustServerCertificate=Yes")

    @staticmethod
    def reasoning_effort() -> str | None:
        """Reasoning models must be told to think briefly or they burn the whole completion budget
        before writing any SQL. The accepted values differ per family: gpt-oss takes low/medium/high,
        qwen only none/default. LLM_REASONING_EFFORT overrides."""
        override = os.getenv("LLM_REASONING_EFFORT")
        if override:
            return None if override.lower() == "off" else override
        model = os.getenv("LLM_MODEL", "openai/gpt-oss-120b").lower()
        if "gpt-oss" in model:
            return "low"
        if "qwen" in model:
            return "none"
        return None

    @staticmethod
    def extra_body() -> dict:
        effort = Credentials.reasoning_effort()
        return {"reasoning_effort": effort} if effort else {}
