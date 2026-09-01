with open("config.py", "r") as f:
    content = f.read()

new_db = """    # ── Database (PostgreSQL — primary for production) ──────────────────────────
    database_url: Optional[str] = Field(
        default=None,
        description="PostgreSQL connection URL. If set, overrides SQLite for UnifiedMemory and LangGraph checkpoints."
    )
    # SQLite is kept as a local fallback when DATABASE_URL is not set
    db_path: str = Field(default="data/memory.sqlite")"""

content = content.replace('    db_path: str = Field(default="data/memory.sqlite")', new_db)

new_prop = """
    @property
    def openrouter_free_models_list(self) -> list[str]:
        return [m.strip() for m in self.openrouter_free_models.split(",") if m.strip()]

    @property
    def use_postgres(self) -> bool:
        \"\"\"True when a PostgreSQL DATABASE_URL has been configured.\"\"\"
        return bool(self.database_url)"""

content = content.replace("""
    @property
    def openrouter_free_models_list(self) -> list[str]:
        return [m.strip() for m in self.openrouter_free_models.split(",") if m.strip()]""", new_prop)

with open("config.py", "w") as f:
    f.write(content)
