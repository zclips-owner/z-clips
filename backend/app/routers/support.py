"""Support routes: customer help & support threads.

Customers see only their own threads. The owner inbox lives at
``/owner/support`` — an owner visiting ``/support`` is redirected there.
"""
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..activity import log_activity
from ..database import get_db
from ..deps import base_context, check_rate_limit, require_user, validate_csrf
from ..models import SupportMessage, SupportThread, utcnow
from ..render import render

router = APIRouter()


def _get_thread(db: Session, thread_id: int, user):
    """Return the thread or raise 404 / 403 (customer: own threads only)."""
    thread = db.get(SupportThread, thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail="Support thread not found.")
    if user.role != "owner" and thread.user_id != user.id:
        raise HTTPException(status_code=403, detail="You can only view your own support threads.")
    return thread


def _thread_messages(db: Session, thread_id: int):
    return db.scalars(
        select(SupportMessage)
        .where(SupportMessage.thread_id == thread_id)
        .order_by(SupportMessage.id.asc())
    ).all()


@router.get("/support")
def support_list(
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    if user.role == "owner":
        return RedirectResponse("/owner/support", status_code=303)
    threads = db.scalars(
        select(SupportThread)
        .where(SupportThread.user_id == user.id)
        .order_by(SupportThread.updated_at.desc(), SupportThread.id.desc())
    ).all()
    return render(
        request,
        "support.html",
        {**base_context(request, user), "threads": threads},
    )


@router.get("/support/new")
def support_new_page(
    request: Request,
    user=Depends(require_user),
):
    return render(
        request,
        "support_new.html",
        {**base_context(request, user), "error": None},
    )


@router.post("/support/new")
def support_new_submit(
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(require_user),
    subject: str = Form(...),
    message: str = Form(...),
    csrf_token: str = Form(...),
):
    validate_csrf(request, csrf_token)
    check_rate_limit(request, "generic_post", key=str(user.id))

    subject = (subject or "").strip()
    message = (message or "").strip()
    error = None
    if not subject:
        error = "Enter a subject for your request."
    elif len(subject) > 200:
        error = "Subject must be 200 characters or fewer."
    elif not message:
        error = "Enter a message describing your issue."
    elif len(message) > 5000:
        error = "Message must be 5000 characters or fewer."
    if error:
        return render(
            request, "support_new.html",
            {**base_context(request, user), "error": error}, status_code=400,
        )

    thread = SupportThread(user_id=user.id, subject=subject, status="open")
    db.add(thread)
    db.flush()
    db.add(SupportMessage(
        thread_id=thread.id,
        author_id=user.id,
        author_role="owner" if user.role == "owner" else "customer",
        body=message,
    ))
    log_activity(db, user.id, "support_opened", f"Support request opened: {subject[:200]}")
    db.commit()
    return RedirectResponse(f"/support/{thread.id}", status_code=303)


@router.get("/support/{thread_id}")
def support_thread_page(
    request: Request,
    thread_id: int,
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    thread = _get_thread(db, thread_id, user)
    return render(
        request,
        "support_thread.html",
        {
            **base_context(request, user),
            "thread": thread,
            "messages": _thread_messages(db, thread.id),
        },
    )


@router.post("/support/{thread_id}/reply")
def support_reply(
    request: Request,
    thread_id: int,
    db: Session = Depends(get_db),
    user=Depends(require_user),
    message: str = Form(...),
    csrf_token: str = Form(...),
):
    validate_csrf(request, csrf_token)
    check_rate_limit(request, "generic_post", key=str(user.id))

    thread = _get_thread(db, thread_id, user)
    message = (message or "").strip()
    if not message:
        return render(
            request,
            "support_thread.html",
            {
                **base_context(request, user),
                "thread": thread,
                "messages": _thread_messages(db, thread.id),
                "error": "Enter a reply message.",
            },
            status_code=400,
        )
    if len(message) > 5000:
        return render(
            request,
            "support_thread.html",
            {
                **base_context(request, user),
                "thread": thread,
                "messages": _thread_messages(db, thread.id),
                "error": "Message must be 5000 characters or fewer.",
            },
            status_code=400,
        )

    db.add(SupportMessage(
        thread_id=thread.id,
        author_id=user.id,
        author_role="owner" if user.role == "owner" else "customer",
        body=message,
    ))
    # A customer reply reopens a closed thread.
    if thread.status == "closed" and user.role != "owner":
        thread.status = "open"
    thread.updated_at = utcnow()
    log_activity(db, user.id, "support_replied", f"Replied to support thread #{thread.id}.")
    db.commit()
    return RedirectResponse(f"/support/{thread.id}", status_code=303)
