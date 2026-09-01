with open('docker-compose.yml', 'r') as f:
    content = f.read()

injection = """
    ports:
      - "8080:8080"  # Admin API + Dashboard
    volumes:
"""
content = content.replace("    volumes:", injection.strip("\n"))

with open('docker-compose.yml', 'w') as f:
    f.write(content)
