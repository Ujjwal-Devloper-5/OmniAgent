import re
with open('pyproject.toml', 'r') as f:
    content = f.read()

deps_injection = """
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.30.0",
"""
content = re.sub(r'(dependencies = \[)', r'\1\n' + deps_injection, content)

with open('pyproject.toml', 'w') as f:
    f.write(content)
