"""Integration tests: support threads and YouTube monitoring."""
from app.models import SupportMessage, SupportThread, YTChannel
from conftest import get_csrf, login_as, make_owner, make_user

PASSWORD = "correct-horse-battery-staple"
OWNER_PASSWORD = "owner-password-that-is-long-enough"


def _db(session_factory):
    return session_factory()


def _thread(session_factory, thread_id):
    db = _db(session_factory)
    try:
        return db.get(SupportThread, thread_id)
    finally:
        db.close()


def _messages(session_factory, thread_id):
    db = _db(session_factory)
    try:
        return db.query(SupportMessage).filter(
            SupportMessage.thread_id == thread_id).order_by(SupportMessage.id).all()
    finally:
        db.close()


def _channel(session_factory, channel_id):
    db = _db(session_factory)
    try:
        return db.get(YTChannel, channel_id)
    finally:
        db.close()


def create_thread(client, subject="Help with my clip", message="It is stuck."):
    token = get_csrf(client, "/support/new")
    resp = client.post(
        "/support/new",
        data={"subject": subject, "message": message, "csrf_token": token},
    )
    assert resp.status_code == 303, resp.text[:500]
    return int(resp.headers["location"].rsplit("/", 1)[-1])


def add_channel(client, url="https://www.youtube.com/@testchannel"):
    token = get_csrf(client, "/yt")
    resp = client.post("/yt/channels",
                       data={"channel_url": url, "csrf_token": token})
    assert resp.status_code == 303, resp.text[:500]
    return resp


def _channel_id(session_factory, user_id):
    db = _db(session_factory)
    try:
        row = db.query(YTChannel).filter(YTChannel.user_id == user_id).one()
        return row.id
    finally:
        db.close()


# ------------------------------------------------------------------ support
def test_support_create_thread_redirects_to_thread(client, tmp_db):
    alice = make_user(tmp_db, username="alice", password=PASSWORD)
    login_as(client, tmp_db, "alice", PASSWORD)
    thread_id = create_thread(client)
    thread = _thread(tmp_db, thread_id)
    assert thread is not None
    assert thread.user_id == alice
    assert thread.status == "open"
    assert thread.subject == "Help with my clip"
    msgs = _messages(tmp_db, thread_id)
    assert len(msgs) == 1
    assert msgs[0].author_role == "customer"
    assert msgs[0].body == "It is stuck."


def test_support_reply_works(client, tmp_db):
    make_user(tmp_db, username="alice", password=PASSWORD)
    login_as(client, tmp_db, "alice", PASSWORD)
    thread_id = create_thread(client)
    token = get_csrf(client, f"/support/{thread_id}")
    resp = client.post(f"/support/{thread_id}/reply",
                       data={"message": "Any update?", "csrf_token": token})
    assert resp.status_code == 303
    assert resp.headers["location"] == f"/support/{thread_id}"
    msgs = _messages(tmp_db, thread_id)
    assert len(msgs) == 2
    assert msgs[1].body == "Any update?"
    page = client.get(f"/support/{thread_id}")
    assert "Any update?" in page.text


def test_support_thread_forbidden_for_other_customer(client, tmp_db):
    make_user(tmp_db, username="alice", password=PASSWORD)
    make_user(tmp_db, username="bob", email="bob@example.com", password=PASSWORD)
    login_as(client, tmp_db, "alice", PASSWORD)
    thread_id = create_thread(client, subject="Alice private issue")
    login_as(client, tmp_db, "bob", PASSWORD)
    assert client.get(f"/support/{thread_id}").status_code == 403


def test_support_list_hides_other_customers_threads(client, tmp_db):
    make_user(tmp_db, username="alice", password=PASSWORD)
    make_user(tmp_db, username="bob", email="bob@example.com", password=PASSWORD)
    login_as(client, tmp_db, "alice", PASSWORD)
    create_thread(client, subject="Alice private issue")
    login_as(client, tmp_db, "bob", PASSWORD)
    resp = client.get("/support")
    assert resp.status_code == 200
    assert "Alice private issue" not in resp.text


def test_owner_sees_all_threads_and_can_reply(client, tmp_db):
    make_user(tmp_db, username="alice", password=PASSWORD)
    login_as(client, tmp_db, "alice", PASSWORD)
    thread_id = create_thread(client, subject="Alice private issue")
    make_owner(tmp_db)
    login_as(client, tmp_db, "zclips_owner", OWNER_PASSWORD)
    inbox = client.get("/owner/support")
    assert inbox.status_code == 200
    assert "Alice private issue" in inbox.text
    token = get_csrf(client, f"/owner/support/{thread_id}")
    resp = client.post(f"/owner/support/{thread_id}/reply",
                       data={"message": "We are looking into it.",
                             "csrf_token": token})
    assert resp.status_code == 303
    msgs = _messages(tmp_db, thread_id)
    assert msgs[-1].author_role == "owner"
    assert msgs[-1].body == "We are looking into it."


def test_customer_reply_reopens_closed_thread(client, tmp_db):
    make_user(tmp_db, username="alice", password=PASSWORD)
    login_as(client, tmp_db, "alice", PASSWORD)
    thread_id = create_thread(client)
    make_owner(tmp_db)
    login_as(client, tmp_db, "zclips_owner", OWNER_PASSWORD)
    token = get_csrf(client, f"/owner/support/{thread_id}")
    resp = client.post(f"/owner/support/{thread_id}/close",
                       data={"csrf_token": token})
    assert resp.status_code == 303
    assert _thread(tmp_db, thread_id).status == "closed"
    login_as(client, tmp_db, "alice", PASSWORD)
    token = get_csrf(client, f"/support/{thread_id}")
    resp = client.post(f"/support/{thread_id}/reply",
                       data={"message": "Still broken, please help.",
                             "csrf_token": token})
    assert resp.status_code == 303
    assert _thread(tmp_db, thread_id).status == "open"


# ------------------------------------------------------------------ YouTube
def test_free_user_yt_page_shows_locked_state(client, tmp_db):
    make_user(tmp_db, username="alice", password=PASSWORD)
    login_as(client, tmp_db, "alice", PASSWORD)
    resp = client.get("/yt")
    assert resp.status_code == 200
    assert "YT Monitoring is locked" in resp.text
    assert "Upgrade to Pro" in resp.text


def test_free_user_add_channel_returns_403(client, tmp_db):
    make_user(tmp_db, username="alice", password=PASSWORD)
    login_as(client, tmp_db, "alice", PASSWORD)
    # The locked /yt page renders no add-channel form for a Free user (and
    # /dashboard has no form at all), so the CSRF token is fetched from
    # the login page (tokens are per-session, not per-page).
    token = get_csrf(client, "/login")
    resp = client.post("/yt/channels",
                       data={"channel_url": "https://www.youtube.com/@someone",
                             "csrf_token": token})
    assert resp.status_code == 403


def test_pro_user_adds_channel_waiting_authorized(client, tmp_db):
    pro = make_user(tmp_db, username="prouser", email="pro@example.com",
                    password=PASSWORD, plan="pro", credits=70)
    login_as(client, tmp_db, "prouser", PASSWORD)
    resp = add_channel(client)
    assert resp.headers["location"] == "/yt"
    channel_id = _channel_id(tmp_db, pro)
    channel = _channel(tmp_db, channel_id)
    assert channel.ingestion_state == "waiting_authorized"
    assert channel.channel_handle == "@testchannel"
    assert channel.channel_url == "https://www.youtube.com/@testchannel"


def test_pro_user_add_channel_invalid_input_returns_400(client, tmp_db):
    make_user(tmp_db, username="prouser", email="pro@example.com",
              password=PASSWORD, plan="pro", credits=70)
    login_as(client, tmp_db, "prouser", PASSWORD)
    token = get_csrf(client, "/yt")
    resp = client.post("/yt/channels",
                       data={"channel_url": "not-a-channel", "csrf_token": token})
    assert resp.status_code == 400
    assert "valid YouTube channel URL" in resp.text
    token = get_csrf(client, "/yt")
    resp = client.post("/yt/channels",
                       data={"channel_url": "https://example.com/someone",
                             "csrf_token": token})
    assert resp.status_code == 400


def test_pro_user_check_without_api_key_reports_not_configured(client, tmp_db):
    pro = make_user(tmp_db, username="prouser", email="pro@example.com",
                    password=PASSWORD, plan="pro", credits=70)
    login_as(client, tmp_db, "prouser", PASSWORD)
    add_channel(client)
    channel_id = _channel_id(tmp_db, pro)
    token = get_csrf(client, "/yt")
    resp = client.post(f"/yt/channels/{channel_id}/check",
                       data={"csrf_token": token})
    assert resp.status_code == 200
    assert "not configured" in resp.text.lower()
    assert _channel(tmp_db, channel_id).last_status == "not_configured"


def test_pro_user_authorize_channel(client, tmp_db):
    pro = make_user(tmp_db, username="prouser", email="pro@example.com",
                    password=PASSWORD, plan="pro", credits=70)
    login_as(client, tmp_db, "prouser", PASSWORD)
    add_channel(client)
    channel_id = _channel_id(tmp_db, pro)
    token = get_csrf(client, "/yt")
    resp = client.post(
        f"/yt/channels/{channel_id}/authorize",
        data={"note": "my own channel, VOD file uploaded by me",
              "csrf_token": token},
    )
    assert resp.status_code == 303
    channel = _channel(tmp_db, channel_id)
    assert channel.ingestion_state == "authorized"
    assert channel.authorized_source_note == "my own channel, VOD file uploaded by me"


def test_pro_user_deletes_own_channel(client, tmp_db):
    pro = make_user(tmp_db, username="prouser", email="pro@example.com",
                    password=PASSWORD, plan="pro", credits=70)
    login_as(client, tmp_db, "prouser", PASSWORD)
    add_channel(client)
    channel_id = _channel_id(tmp_db, pro)
    token = get_csrf(client, "/yt")
    resp = client.post(f"/yt/channels/{channel_id}/delete",
                       data={"csrf_token": token})
    assert resp.status_code == 303
    assert _channel(tmp_db, channel_id) is None


def test_delete_another_users_channel_returns_403(client, tmp_db):
    alice = make_user(tmp_db, username="alice", password=PASSWORD,
                      plan="pro", credits=70)
    make_user(tmp_db, username="bob", email="bob@example.com",
              password=PASSWORD, plan="pro", credits=70)
    login_as(client, tmp_db, "alice", PASSWORD)
    add_channel(client)
    channel_id = _channel_id(tmp_db, alice)
    login_as(client, tmp_db, "bob", PASSWORD)
    token = get_csrf(client, "/yt")
    resp = client.post(f"/yt/channels/{channel_id}/delete",
                       data={"csrf_token": token})
    assert resp.status_code == 403
    assert _channel(tmp_db, channel_id) is not None
