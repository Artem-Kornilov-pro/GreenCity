"""Сквозной сценарий аккаунта: регистрация -> проект со сценой -> GreenPlan ->
сохранение результата -> повторное открытие -> обновление токена -> выход ->
удаление. MongoDB и Redis -- in-memory (fake_mongo/fake_redis)."""


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_register_save_greenplan_result_reopen_and_logout(client, e2e):
    r = client.post("/api/auth/register", json={"username": "designer", "password": "secret-pass-1"})
    assert r.status_code == 200, r.text
    tokens = r.json()
    access, refresh = tokens["access_token"], tokens["refresh_token"]

    assert client.get("/api/auth/me", headers=_auth(access)).json()["username"] == "designer"

    scene = e2e.upload_dxf(client, e2e.location_dxf("25_classical_building_ring_primer"))
    r = client.post("/api/projects", json={"name": "Двор у ратуши", "scene": scene}, headers=_auth(access))
    assert r.status_code == 200, r.text
    project_id = r.json()["id"]

    planned = client.post("/api/greenplan/generate", json=scene).json()["scene"]
    r = client.put(f"/api/projects/{project_id}", json={"scene": planned}, headers=_auth(access))
    assert r.status_code == 200

    reopened = client.get(f"/api/projects/{project_id}", headers=_auth(access)).json()
    assert len(reopened["scene"]["objects"]) == len(planned["objects"])
    assert sum(1 for o in reopened["scene"]["objects"] if o["metadata"].get("generated")) > 0

    listing = client.get("/api/projects", headers=_auth(access)).json()
    assert [p["name"] for p in listing] == ["Двор у ратуши"]

    # Новый access-токен по refresh; после выхода refresh больше не работает.
    r = client.post("/api/auth/refresh", json={"refresh_token": refresh})
    assert r.status_code == 200
    access = r.json()["access_token"]
    assert client.post("/api/auth/logout", json={"refresh_token": refresh}).status_code == 200
    assert client.post("/api/auth/refresh", json={"refresh_token": refresh}).status_code == 401

    assert client.delete(f"/api/projects/{project_id}", headers=_auth(access)).status_code == 200
    assert client.get("/api/projects", headers=_auth(access)).json() == []


def test_projects_are_private_and_limited(client, e2e):
    scene = e2e.upload_dxf(client, e2e.location_dxf("01_single_building"))
    alice = client.post("/api/auth/register", json={"username": "alice", "password": "alice-pass-1"}).json()["access_token"]
    bob = client.post("/api/auth/register", json={"username": "bob", "password": "bob-pass-12"}).json()["access_token"]

    ids = []
    for i in range(3):
        r = client.post("/api/projects", json={"name": f"Проект {i}", "scene": scene}, headers=_auth(alice))
        assert r.status_code == 200
        ids.append(r.json()["id"])
    # Лимит -- три проекта на пользователя.
    assert client.post("/api/projects", json={"name": "лишний", "scene": scene}, headers=_auth(alice)).status_code == 400

    # Чужой проект не виден и не удаляется.
    assert client.get(f"/api/projects/{ids[0]}", headers=_auth(bob)).status_code == 404
    assert client.delete(f"/api/projects/{ids[0]}", headers=_auth(bob)).status_code == 404
    assert client.get("/api/projects", headers=_auth(bob)).json() == []

    # Без токена -- 401, редактор при этом работает без аккаунта.
    assert client.get("/api/projects").status_code == 401
    assert client.post("/api/greenplan/generate", json=scene).status_code == 200
