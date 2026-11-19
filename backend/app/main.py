"""Z CLIPS — application entrypoint.

Auth routes (landing, login, signup, owner bootstrap, plans, logout,
health) live here. All feature routes live in app/routers/* and are
mounted below. The one-time owner bootstrap is the ONLY way an owner
account is created; public signup always creates a customer.
"""
import re
from pathlib import Path

from fastapi import FastAPI, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import select, func

from .config import APP_ENV, SESSION_SECRET, OWNER_SETUP_TOKEN
from .database import Base, engine, get_db
from .deps import (
    check_rate_limit,
    csrf_token_for,
    current_user,
    validate_csrf,
)
from .models import User, AuditLog
from .plans import PLANS, promotion_is_active
from .render import render
from .security import hash_password, verify_password
from .services.billing import get_or_create_promotion

BASE_DIR = Path(__file__).resolve().parent
app = FastAPI(title="Z CLIPS", version="0.1.0")
app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    same_site="lax",
    https_only=(APP_ENV == "production"),
    session_cookie="zclips_session",
    max_age=60 * 60 * 24 * 7,
)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

# Feature routers (each owns its own routes; see docs/ROUTE_CONTEXT_SPEC.md).
from .routers import api, customer, owner, payments, support, yt  # noqa: E402

app.include_router(customer.router)
app.include_router(owner.router)
app.include_router(payments.router)
app.include_router(support.router)
app.include_router(yt.router)
app.include_router(api.router)


@app.on_event("startup")
def startup():
    Base.metadata.create_all(bind=engine)


@app.get("/", response_class=HTMLResponse)
def home(request: Request, db: Session = Depends(get_db)):
    user = current_user(request, db)
    if user:
        return RedirectResponse("/owner" if user.role == "owner" else "/dashboard", status_code=303)
    return render(request, "landing.html", {"plans": PLANS})


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return render(request, "auth.html", {"mode": "login", "error": None, "csrf_token": csrf_token_for(request)})


@app.post("/login")
def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    csrf_token: str = Form(""),
    db: Session = Depends(get_db),
):
    check_rate_limit(request, "login")
    validate_csrf(request, csrf_token)
    user = db.scalar(select(User).where(func.lower(User.username) == username.lower().strip()))
    if not user or not user.is_active or not verify_password(password, user.password_hash):
        return render(
            request,
            "auth.html",
            {"mode": "login", "error": "Username ya password sahi nahi hai.", "csrf_token": csrf_token_for(request)},
            400,
        )
    request.session.clear()
    request.session["user_id"] = user.id
    return RedirectResponse("/owner" if user.role == "owner" else "/dashboard", status_code=303)


@app.get("/signup", response_class=HTMLResponse)
def signup_page(request: Request):
    return render(request, "auth.html", {"mode": "signup", "error": None, "csrf_token": csrf_token_for(request)})


@app.post("/signup")
def signup(
    request: Request,
    username: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    csrf_token: str = Form(""),
    db: Session = Depends(get_db),
):
    check_rate_limit(request, "signup")
    validate_csrf(request, csrf_token)
    username = username.strip()
    email = email.strip().lower()

    def fail(msg: str):
        return render(
            request,
            "auth.html",
            {"mode": "signup", "error": msg, "csrf_token": csrf_token_for(request)},
            400,
        )

    if not re.fullmatch(r"[A-Za-z0-9_]{3,40}", username):
        return fail("Username 3–40 characters ka ho; letters, numbers aur underscore use karein.")
    if len(password) < 12:
        return fail("Password kam az kam 12 characters ka hona chahiye.")
    existing = db.scalar(
        select(User).where((func.lower(User.username) == username.lower()) | (func.lower(User.email) == email))
    )
    if existing:
        return fail("Ye username ya email pehle se registered hai.")
    # Public signup ALWAYS creates a customer. Role and plan are never accepted from the browser.
    user = User(username=username, email=email, password_hash=hash_password(password), role="customer", plan="free")
    db.add(user)
    db.commit()
    db.refresh(user)
    request.session.clear()
    request.session["user_id"] = user.id
    return RedirectResponse("/dashboard", status_code=303)


@app.get("/owner-setup", response_class=HTMLResponse)
def owner_setup_page(request: Request, db: Session = Depends(get_db)):
    owner_exists = db.scalar(select(User).where(User.role == "owner")) is not None
    if owner_exists:
        raise HTTPException(status_code=404, detail="Owner setup is already closed.")
    return render(request, "owner_setup.html", {"error": None, "csrf_token": csrf_token_for(request)})


@app.post("/owner-setup")
def owner_setup(
    request: Request,
    setup_token: str = Form(...),
    password: str = Form(...),
    confirm_password: str = Form(...),
    csrf_token: str = Form(""),
    db: Session = Depends(get_db),
):
    check_rate_limit(request, "owner_setup")
    validate_csrf(request, csrf_token)
    owner_exists = db.scalar(select(User).where(User.role == "owner")) is not None
    if owner_exists:
        raise HTTPException(status_code=404, detail="Owner setup is already closed.")

    def fail(msg: str, status_code: int = 400):
        return render(
            request,
            "owner_setup.html",
            {"error": msg, "csrf_token": csrf_token_for(request)},
            status_code,
        )

    if not OWNER_SETUP_TOKEN or setup_token != OWNER_SETUP_TOKEN:
        return fail("Setup token incorrect hai.", 403)
    if len(password) < 16:
        return fail("Owner password kam az kam 16 characters ka hona chahiye.")
    if password != confirm_password:
        return fail("Passwords match nahi karte.")
    # Fixed owner username. Public signup cannot create or promote an owner.
    user = User(
        username="zclips_owner",
        email="owner@zclips.local",
        password_hash=hash_password(password),
        role="owner",
        plan="pro",
        clips_used=0,
        is_active=True,
    )
    db.add(user)
    db.flush()
    db.add(AuditLog(actor_user_id=user.id, action="owner_initialized", detail="Initial owner account created"))
    db.commit()
    request.session.clear()
    request.session["user_id"] = user.id
    return RedirectResponse("/owner", status_code=303)


@app.get("/plans", response_class=HTMLResponse)
def plans_page(request: Request, db: Session = Depends(get_db)):
    promotion = get_or_create_promotion(db)
    db.commit()
    return render(
        request,
        "plans.html",
        {
            "plans": PLANS,
            "promotion": promotion,
            "promo_active": promotion_is_active(promotion),
        },
    )


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/", status_code=303)


@app.get("/health")
def health():
    return {"status": "ok", "service": "z-clips", "stage": "full"}
