import re
with open('adapters/admin_api.py', 'r') as f:
    content = f.read()

# Add query token support for SSE
replacement = """
async def verify_token(request: Request, credentials: HTTPAuthorizationCredentials = Depends(security)) -> str:
    # Check if query param exists for SSE
    token = request.query_params.get("token")
    if not token and credentials:
        token = credentials.credentials
    if not settings.admin_api_secret:
        raise HTTPException(status_code=403, detail="Admin API is disabled. Set ADMIN_API_SECRET.")
    if token != settings.admin_api_secret:
        raise HTTPException(status_code=401, detail="Invalid token")
    return token
"""
# Need to adjust FastAPI security usage since Depends(security) is required and will fail if header is missing.
# Let's make it optional.
replacement = """
from fastapi.security import APIKeyHeader, APIKeyQuery

api_key_header = APIKeyHeader(name="Authorization", auto_error=False)
api_key_query = APIKeyQuery(name="token", auto_error=False)

async def verify_token(header_key: str = Depends(api_key_header), query_key: str = Depends(api_key_query)) -> str:
    if not settings.admin_api_secret:
        raise HTTPException(status_code=403, detail="Admin API disabled")
    
    token = None
    if header_key:
        token = header_key.replace("Bearer ", "")
    elif query_key:
        token = query_key
        
    if token != settings.admin_api_secret:
        raise HTTPException(status_code=401, detail="Invalid token")
    return token
"""

content = re.sub(r"security = HTTPBearer\(\).*?return credentials\.credentials", replacement.strip(), content, flags=re.DOTALL)

with open('adapters/admin_api.py', 'w') as f:
    f.write(content)
