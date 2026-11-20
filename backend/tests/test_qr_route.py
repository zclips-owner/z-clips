"""Integration tests: the NayaPay QR image route.

NOTE: the tests must never touch the real shipped QR file
(storage/nayapay-qr.png). They point the routers at a temporary path
instead, so running the suite cannot delete the owner's QR.
"""
from pathlib import Path

import app.routers.api as api_router
import app.routers.owner as owner_router
import app.routers.payments as payments_router
from conftest import login_as, make_user

PASSWORD = "correct-horse-battery-staple"

PNG_1X1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
)


def _point_qr_at(monkeypatch, tmp_path):
    """Point every module that reads the QR at a temp file (routers import
    NAYAPAY_QR_PATH by value, so each module namespace needs patching)."""
    qr = tmp_path / "nayapay-qr.png"
    monkeypatch.setattr(payments_router, "NAYAPAY_QR_PATH", str(qr))
    monkeypatch.setattr(api_router, "NAYAPAY_QR_PATH", str(qr))
    monkeypatch.setattr(owner_router, "NAYAPAY_QR_PATH", str(qr))
    return qr


def test_pay_qr_returns_404_when_no_qr_file(client, tmp_db, monkeypatch, tmp_path):
    _point_qr_at(monkeypatch, tmp_path)
    make_user(tmp_db, username="alice", password=PASSWORD)
    login_as(client, tmp_db, "alice", PASSWORD)
    resp = client.get("/pay/qr")
    assert resp.status_code == 404


def test_pay_qr_returns_image_when_file_present(client, tmp_db, monkeypatch, tmp_path):
    qr = _point_qr_at(monkeypatch, tmp_path)
    qr.write_bytes(PNG_1X1)
    make_user(tmp_db, username="alice", password=PASSWORD)
    login_as(client, tmp_db, "alice", PASSWORD)
    resp = client.get("/pay/qr")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("image/")
    assert resp.content == PNG_1X1
