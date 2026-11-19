"""Integration tests: the NayaPay QR image route."""
from pathlib import Path

from app.config import NAYAPAY_QR_PATH
from conftest import login_as, make_user

PASSWORD = "correct-horse-battery-staple"

PNG_1X1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
)


def test_pay_qr_returns_404_when_no_qr_file(client, tmp_db):
    qr = Path(NAYAPAY_QR_PATH)
    if qr.is_file():
        qr.unlink()
    make_user(tmp_db, username="alice", password=PASSWORD)
    login_as(client, tmp_db, "alice", PASSWORD)
    resp = client.get("/pay/qr")
    assert resp.status_code == 404


def test_pay_qr_returns_image_when_file_present(client, tmp_db):
    qr = Path(NAYAPAY_QR_PATH)
    try:
        qr.parent.mkdir(parents=True, exist_ok=True)
        qr.write_bytes(PNG_1X1)
        make_user(tmp_db, username="alice", password=PASSWORD)
        login_as(client, tmp_db, "alice", PASSWORD)
        resp = client.get("/pay/qr")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("image/")
        assert resp.content == PNG_1X1
    finally:
        qr.unlink(missing_ok=True)
