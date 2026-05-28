from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel

from app import auth

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    password: str


@router.get("/status")
async def status_endpoint(request: Request) -> dict:
    enabled = auth.auth_enabled()
    return {
        "enabled": enabled,
        "authenticated": (not enabled) or auth.is_authenticated(request),
    }


@router.post("/login")
async def login(body: LoginRequest, response: Response) -> dict:
    if not auth.auth_enabled():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Auth is not enabled")
    if not auth.verify_password(body.password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid password")
    auth.issue_session(response)
    return {"success": True}


@router.post("/logout")
async def logout(request: Request, response: Response) -> dict:
    auth.revoke_session(request, response)
    return {"success": True}
