import re

with open("docker-compose.yml", "r") as f:
    content = f.read()

# Add postgres service
postgres_service = """  postgres:
    image: postgres:16-alpine
    container_name: omniagent_postgres
    restart: unless-stopped
    environment:
      POSTGRES_DB: omniagent
      POSTGRES_USER: omniagent
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-omniagent_secret}
    volumes:
      - omniagent_pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U omniagent"]
      interval: 10s
      timeout: 5s
      retries: 5
    logging:
      driver: json-file
      options:
        max-size: "5m"
        max-file: "3"

  omniagent:"""

content = content.replace("  omniagent:", postgres_service)

depends_on = """    depends_on:
      postgres:
        condition: service_healthy

    env_file:"""

content = content.replace("    env_file:", depends_on)

env_var = """      - DATABASE_URL=postgresql+asyncpg://omniagent:${POSTGRES_PASSWORD:-omniagent_secret}@postgres:5432/omniagent
      - OLLAMA_BASE_URL"""

content = content.replace("      - OLLAMA_BASE_URL", env_var)

volumes = """volumes:
  omniagent_pgdata:
    driver: local
  omniagent_venv:"""

content = content.replace("volumes:\n  omniagent_venv:", volumes)

with open("docker-compose.yml", "w") as f:
    f.write(content)
