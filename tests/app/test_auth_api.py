import pytest
from fastapi.testclient import TestClient


def test_server_mode_needs_setup_then_login(make_app):
    c = TestClient(make_app("server"))
    s = c.get("/api/auth/session").json()
    assert s == {
        "authenticated": False,
        "mode": "server",
        "needs_setup": True,
        "user": None,
        "csrf_token": None,
    }
    assert c.get("/api/settings").status_code == 401

    r = c.post("/api/auth/setup", json={"username": "alex", "password": "correct-horse-battery"})
    assert r.status_code == 200 and r.json()["authenticated"] is True
    assert "tuppence_session" in r.cookies
    set_cookie = r.headers["set-cookie"].lower()
    assert "httponly" in set_cookie and "samesite=strict" in set_cookie
    csrf = r.json()["csrf_token"]
    assert c.get("/api/settings").status_code == 200

    again = c.post(
        "/api/auth/setup",
        json={"username": "x", "password": "another-long-one"},
        headers={"X-CSRF-Token": csrf},
    )
    assert again.status_code == 409


def test_setup_rejects_weak_password(make_app):
    c = TestClient(make_app("server"))
    r = c.post("/api/auth/setup", json={"username": "alex", "password": "short"})
    assert r.status_code == 422 and "10 characters" in r.json()["detail"]


def test_csrf_required_for_unsafe_methods(client):
    ok = client.patch(
        "/api/settings/privacy.local_only", json={"value": True, "expected_version": 0}
    )
    assert ok.status_code == 200
    client.headers.pop("X-CSRF-Token")
    bad = client.patch(
        "/api/settings/privacy.local_only", json={"value": False, "expected_version": 1}
    )
    assert bad.status_code == 403 and bad.json()["detail"] == "Missing or invalid CSRF token."


def test_login_logout_and_wrong_password(make_app):
    c = TestClient(make_app("server"))
    c.post("/api/auth/setup", json={"username": "alex", "password": "correct-horse-battery"})
    csrf = c.get("/api/auth/session").json()["csrf_token"]
    assert c.post("/api/auth/logout", headers={"X-CSRF-Token": csrf}).status_code == 204
    assert c.get("/api/auth/session").json()["authenticated"] is False
    bad = c.post("/api/auth/login", json={"username": "alex", "password": "wrong-password-1"})
    assert bad.status_code == 401 and bad.json()["detail"] == "Wrong username or password."
    good = c.post("/api/auth/login", json={"username": "ALEX", "password": "correct-horse-battery"})
    assert good.status_code == 200 and good.json()["user"]["username"] == "alex"


def test_login_lockout(make_app):
    c = TestClient(make_app("server"))
    c.post("/api/auth/setup", json={"username": "alex", "password": "correct-horse-battery"})
    c.cookies.clear()
    for _ in range(5):
        r = c.post("/api/auth/login", json={"username": "alex", "password": "nope-nope-nope"})
        assert r.status_code == 401
    locked = c.post(
        "/api/auth/login", json={"username": "alex", "password": "correct-horse-battery"}
    )
    assert locked.status_code == 429 and int(locked.headers["retry-after"]) > 0
    assert "Too many attempts" in locked.json()["detail"]


def test_cross_origin_login_rejected(make_app):
    c = TestClient(make_app("server"))
    r = c.post(
        "/api/auth/setup",
        json={"username": "alex", "password": "correct-horse-battery"},
        headers={"Origin": "https://evil.example"},
    )
    assert r.status_code == 403


def test_launch_token_flow(make_app):
    app = make_app("desktop", launch_token="T" * 43)
    c = TestClient(app, follow_redirects=False)
    assert c.get("/api/settings").status_code == 401
    bad = c.get("/auth/launch", params={"token": "nope"})
    assert bad.status_code == 403 and "This link has expired" in bad.text
    r = c.get("/auth/launch", params={"token": "T" * 43})
    assert r.status_code == 303 and r.headers["location"] == "/"
    s = c.get("/api/auth/session").json()
    assert s["authenticated"] is True and s["mode"] == "desktop" and s["user"] is None
    assert c.get("/api/settings").status_code == 200


def test_desktop_launch_token_is_single_use(make_app):
    c = TestClient(make_app("desktop", launch_token="T" * 43), follow_redirects=False)
    assert c.get("/auth/launch", params={"token": "T" * 43}).status_code == 303
    again = TestClient(c.app, follow_redirects=False).get(
        "/auth/launch", params={"token": "T" * 43}
    )
    assert again.status_code == 403 and "This link has expired" in again.text
    assert "set-cookie" not in again.headers
    # the session from the first exchange keeps working
    assert c.get("/api/settings").status_code == 200


def test_local_launch_token_is_reusable_until_restart(make_app):
    app = make_app("local", launch_token="L" * 43)
    for _ in range(2):
        c = TestClient(app, follow_redirects=False)
        assert c.get("/auth/launch", params={"token": "L" * 43}).status_code == 303
        assert c.get("/api/settings").status_code == 200
    restarted = TestClient(make_app("local", launch_token="M" * 43), follow_redirects=False)
    assert restarted.get("/auth/launch", params={"token": "L" * 43}).status_code == 403


def test_launch_route_absent_in_server_mode(make_app):
    c = TestClient(make_app("server"), follow_redirects=False)
    assert c.get("/auth/launch", params={"token": "x"}).status_code == 404


def test_old_launch_sessions_purged_on_restart(make_app):
    c1 = TestClient(make_app("desktop", launch_token="A" * 43), follow_redirects=False)
    c1.get("/auth/launch", params={"token": "A" * 43})
    cookie = c1.cookies.get("tuppence_session")
    c2 = TestClient(make_app("desktop", launch_token="B" * 43))
    c2.cookies.set("tuppence_session", cookie)
    assert c2.get("/api/settings").status_code == 401


def test_setup_race_over_http_makes_one_admin(make_app):
    import threading

    app = make_app("server")
    barrier = threading.Barrier(8)
    codes = []

    def go(i):
        c = TestClient(app)
        barrier.wait()
        r = c.post(
            "/api/auth/setup", json={"username": f"a{i}", "password": "correct-horse-battery"}
        )
        codes.append(r.status_code)

    threads = [threading.Thread(target=go, args=(i,)) for i in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(codes) == [200] + [409] * 7
    assert app.state.services.users.count() == 1


def test_concurrent_wrong_guesses_are_throttled(make_app):
    import threading

    app = make_app("server")
    TestClient(app).post(
        "/api/auth/setup", json={"username": "alex", "password": "correct-horse-battery"}
    )
    barrier = threading.Barrier(30)
    codes = []

    def go():
        c = TestClient(app)
        barrier.wait()
        codes.append(
            c.post(
                "/api/auth/login", json={"username": "alex", "password": "nope-nope-nope"}
            ).status_code
        )

    threads = [threading.Thread(target=go) for _ in range(30)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert codes.count(401) <= 5 and codes.count(429) >= 25


def test_launch_session_rejected_in_server_mode(tmp_path, make_app):
    local = make_app("local", launch_token="L" * 43)
    c = TestClient(local, follow_redirects=False)
    c.get("/auth/launch", params={"token": "L" * 43})
    cookie = c.cookies.get("tuppence_session")
    # Same data dir, now started in server mode: startup purges, and the guard rejects regardless.
    server = TestClient(make_app("server"))
    server.cookies.set("tuppence_session", cookie)
    assert server.get("/api/settings").status_code == 401
    # Even if a launch row is injected after startup, server mode refuses it.
    token, _ = server.app.state.services.sessions.create("launch")
    server.cookies.set("tuppence_session", token)
    assert server.get("/api/settings").status_code == 401


@pytest.mark.parametrize("name", ["", "   ", "x" * 65])
def test_setup_rejects_bad_username_with_422(make_app, name):
    c = TestClient(make_app("server"))
    r = c.post("/api/auth/setup", json={"username": name, "password": "correct-horse-battery"})
    assert r.status_code == 422 and "between 1 and 64" in r.json()["detail"]


def test_session_cookie_slides_when_renewed(make_app):
    c = TestClient(make_app("server"))
    c.post("/api/auth/setup", json={"username": "alex", "password": "correct-horse-battery"})
    assert "set-cookie" not in c.get("/api/settings").headers
    with c.app.state.services.db.transaction() as conn:
        conn.execute("UPDATE session SET last_seen_at = '2020-01-01T00:00:00Z'")
    r = c.get("/api/settings")
    sc = r.headers["set-cookie"].lower()
    assert "tuppence_session=" in sc and "max-age=2592000" in sc and "httponly" in sc


def test_logout_deletes_session_server_side(make_app):
    c = TestClient(make_app("server"))
    c.post("/api/auth/setup", json={"username": "alex", "password": "correct-horse-battery"})
    old = c.cookies.get("tuppence_session")
    csrf = c.get("/api/auth/session").json()["csrf_token"]
    assert c.post("/api/auth/logout", headers={"X-CSRF-Token": csrf}).status_code == 204
    c.cookies.set("tuppence_session", old)
    assert c.get("/api/settings").status_code == 401


def test_secure_cookie_flag(make_app):
    c = TestClient(make_app("server", secure_cookies=True), base_url="https://testserver")
    r = c.post("/api/auth/setup", json={"username": "alex", "password": "correct-horse-battery"})
    assert "secure" in r.headers["set-cookie"].lower().split("; ")


@pytest.mark.parametrize("mode", ["local", "desktop"])
def test_setup_and_login_absent_outside_server_mode(make_app, mode):
    c = TestClient(make_app(mode))
    body = {"username": "alex", "password": "correct-horse-battery"}
    assert c.post("/api/auth/setup", json=body).status_code == 404
    assert c.post("/api/auth/login", json=body).status_code == 404


def test_cross_origin_login_rejected_but_same_origin_ok(make_app):
    c = TestClient(make_app("server"))
    c.post("/api/auth/setup", json={"username": "alex", "password": "correct-horse-battery"})
    r = c.post(
        "/api/auth/login",
        json={"username": "alex", "password": "correct-horse-battery"},
        headers={"Origin": "https://evil.example"},
    )
    assert r.status_code == 403
    ok = c.post(
        "/api/auth/login",
        json={"username": "alex", "password": "correct-horse-battery"},
        headers={"Origin": "http://testserver"},
    )
    assert ok.status_code == 200
