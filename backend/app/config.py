from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./app.db"

    # Groq (understand stage)
    groq_api_key: str = "REPLACE_WITH_GROQ_API_KEY"
    groq_model: str = "llama-3.3-70b-versatile"

    # Gemini Flash Image (design flow - generate stage)
    gemini_api_key: str = "REPLACE_WITH_GEMINI_API_KEY"
    gemini_image_model: str = "gemini-2.5-flash-image"

    # Gemini text model (mock paper flow - blueprint, questions, answers)
    gemini_text_model: str = "gemini-2.5-flash"

    # Firebase Auth: set to the Firebase project id to require student login.
    # Only the project id is needed to verify ID tokens (no service account).
    firebase_project_id: str = ""

    # Firebase Storage (needs a service account file)
    firebase_enabled: bool = False
    firebase_credentials_path: str = "REPLACE_WITH_PATH_TO_serviceAccount.json"
    firebase_storage_bucket: str = "REPLACE_WITH_PROJECT_ID.appspot.com"

    # Moderation
    moderation_api_key: str = "REPLACE_WITH_MODERATION_API_KEY"

    # Local storage fallback (always used unless Firebase is enabled)
    storage_dir: str = "./generated"
    upload_max_mb: int = 15

    # Comma-separated list of allowed frontend origins (CORS)
    frontend_origin: str = "http://localhost:3000,http://localhost:8080,http://localhost:5173"

    # ---- Print queue ----
    # Pages (pages x copies) a student may print per day, and per single job.
    print_daily_page_limit: int = 30
    print_max_pages_per_job: int = 20
    print_max_copies: int = 5

    # Students always print to their OWN printer through the browser's print
    # dialog (any Wi-Fi / USB / Bluetooth / AirPrint printer their device knows).
    # Optionally, a shared (e.g. school library) printer can be offered too:
    #   none   - no shared printer (default)
    #   mock   - simulated shared printer, PDFs copied to STORAGE_DIR/printed
    #   cups   - this server prints directly with `lp` (printer attached to this machine)
    #   agent  - a print agent on the school network pulls jobs (see print_agent/)
    printer_backend: str = "none"
    printer_name: str = ""  # CUPS queue name; blank = system default printer
    print_agent_token: str = "REPLACE_WITH_PRINT_AGENT_TOKEN"

    def is_placeholder(self, value: str) -> bool:
        return not value or value.startswith("REPLACE_WITH_")

    @property
    def auth_required(self) -> bool:
        return bool(self.firebase_project_id)

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.frontend_origin.split(",") if o.strip()]


settings = Settings()
