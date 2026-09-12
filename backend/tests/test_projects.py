"""projects.py -- регистрация/вход и CRUD проектов поверх (поддельных)
MongoDB + Redis. Полностью то, что реально исполняется в проде -- fake_mongo/
fake_redis подменяют только транспорт, вся бизнес-логика (лимит проектов,
владение чужими проектами, дублирующиеся имена) настоящая."""

import projects as projects_module
import pytest


@pytest.fixture
def scene():
    from schemas import Scene, SceneMeta

    return Scene(
        boundary=None,
        restrictions=[],
        objects=[],
        meta=SceneMeta(scale=1.0, insunits=6, origin={"x": 0.0, "z": 0.0}, buildingCount=0, pointObjectCount=0),
    )


# --- Регистрация/вход ---------------------------------------------------------


async def test_register_returns_tokens_and_creates_user(fake_mongo, fake_redis):
    result = await projects_module.register("alice", "password123")
    assert result.username == "alice"
    assert result.access_token
    assert result.refresh_token
    stored = await fake_mongo["users"].find_one({"username": "alice"})
    assert stored is not None
    assert stored["password_hash"] != "password123"  # захэширован, не в открытом виде


async def test_register_rejects_duplicate_username(fake_mongo, fake_redis):
    await projects_module.register("alice", "password123")
    with pytest.raises(projects_module.ProjectsError):
        await projects_module.register("alice", "another-password")


async def test_register_rejects_invalid_username(fake_mongo, fake_redis):
    with pytest.raises(projects_module.ProjectsError):
        await projects_module.register("a", "password123")


async def test_register_rejects_short_password(fake_mongo, fake_redis):
    with pytest.raises(projects_module.ProjectsError):
        await projects_module.register("alice", "123")


async def test_login_succeeds_with_correct_credentials(fake_mongo, fake_redis):
    await projects_module.register("alice", "password123")
    result = await projects_module.login("alice", "password123")
    assert result.username == "alice"


async def test_login_rejects_wrong_password(fake_mongo, fake_redis):
    await projects_module.register("alice", "password123")
    with pytest.raises(projects_module.ProjectsError):
        await projects_module.login("alice", "wrong-password")


async def test_login_rejects_unknown_username(fake_mongo, fake_redis):
    with pytest.raises(projects_module.ProjectsError):
        await projects_module.login("no-such-user", "password123")


async def test_register_and_login_issue_independently_revocable_sessions(fake_mongo, fake_redis):
    """Несколько входов подряд -- независимые refresh-сессии, отзыв одной не
    должен трогать остальные (ТЗ: несколько параллельных сессий на аккаунт)."""
    import cache

    first = await projects_module.register("alice", "password123")
    second = await projects_module.login("alice", "password123")
    assert first.refresh_token != second.refresh_token

    from auth import decode_token

    sid_first = decode_token(first.refresh_token)["sid"]
    sid_second = decode_token(second.refresh_token)["sid"]
    await cache.revoke_session(sid_first)

    assert await cache.session_is_active(sid_first, decode_token(first.refresh_token)["sub"]) is False
    assert await cache.session_is_active(sid_second, decode_token(second.refresh_token)["sub"]) is True


# --- Проекты: CRUD и лимиты ----------------------------------------------------


async def test_create_and_get_project(fake_mongo, fake_redis, scene):
    auth_result = await projects_module.register("alice", "password123")
    from auth import decode_token

    user_id = decode_token(auth_result.access_token)["sub"]

    created = await projects_module.create_project(user_id, "Мой двор", scene)
    fetched = await projects_module.get_project(user_id, created.id)
    assert fetched.name == "Мой двор"
    assert fetched.id == created.id


async def test_create_project_enforces_max_projects_per_user(fake_mongo, fake_redis, scene):
    auth_result = await projects_module.register("alice", "password123")
    from auth import decode_token

    user_id = decode_token(auth_result.access_token)["sub"]

    for i in range(projects_module.MAX_PROJECTS_PER_USER):
        await projects_module.create_project(user_id, f"Проект {i}", scene)

    with pytest.raises(projects_module.ProjectsError):
        await projects_module.create_project(user_id, "Лишний проект", scene)


async def test_list_projects_returns_only_own_projects_newest_first(fake_mongo, fake_redis, scene):
    from auth import decode_token

    alice = await projects_module.register("alice", "password123")
    bob = await projects_module.register("bob", "password456")
    alice_id = decode_token(alice.access_token)["sub"]
    bob_id = decode_token(bob.access_token)["sub"]

    await projects_module.create_project(alice_id, "Двор Алисы", scene)
    await projects_module.create_project(bob_id, "Двор Боба", scene)

    alice_projects = await projects_module.list_projects(alice_id)
    assert [p.name for p in alice_projects] == ["Двор Алисы"]


async def test_get_project_owned_by_another_user_raises_not_found(fake_mongo, fake_redis, scene):
    from auth import decode_token

    alice = await projects_module.register("alice", "password123")
    bob = await projects_module.register("bob", "password456")
    alice_id = decode_token(alice.access_token)["sub"]
    bob_id = decode_token(bob.access_token)["sub"]

    project = await projects_module.create_project(alice_id, "Двор Алисы", scene)
    with pytest.raises(projects_module.NotFoundError):
        await projects_module.get_project(bob_id, project.id)


async def test_get_project_with_malformed_id_raises_not_found(fake_mongo, fake_redis):
    with pytest.raises(projects_module.NotFoundError):
        await projects_module.get_project("user-1", "not-a-valid-object-id")


async def test_update_project_can_rename_without_touching_scene(fake_mongo, fake_redis, scene):
    from auth import decode_token

    auth_result = await projects_module.register("alice", "password123")
    user_id = decode_token(auth_result.access_token)["sub"]
    project = await projects_module.create_project(user_id, "Старое имя", scene)

    updated = await projects_module.update_project(user_id, project.id, "Новое имя", None)
    assert updated.name == "Новое имя"
    assert updated.scene == scene


async def test_update_project_can_replace_scene(fake_mongo, fake_redis, scene):
    from auth import decode_token
    from schemas import SceneMeta

    auth_result = await projects_module.register("alice", "password123")
    user_id = decode_token(auth_result.access_token)["sub"]
    project = await projects_module.create_project(user_id, "Двор", scene)

    from schemas import Scene as SceneModel

    new_scene = SceneModel(
        boundary=None,
        restrictions=[],
        objects=[],
        meta=SceneMeta(scale=2.0, insunits=6, origin={"x": 1.0, "z": 1.0}, buildingCount=1, pointObjectCount=0),
    )
    updated = await projects_module.update_project(user_id, project.id, None, new_scene)
    assert updated.name == "Двор"  # имя не тронуто
    assert updated.scene.meta.scale == 2.0


async def test_update_project_owned_by_another_user_raises_not_found(fake_mongo, fake_redis, scene):
    from auth import decode_token

    alice = await projects_module.register("alice", "password123")
    bob = await projects_module.register("bob", "password456")
    alice_id = decode_token(alice.access_token)["sub"]
    bob_id = decode_token(bob.access_token)["sub"]
    project = await projects_module.create_project(alice_id, "Двор Алисы", scene)

    with pytest.raises(projects_module.NotFoundError):
        await projects_module.update_project(bob_id, project.id, "Захват", None)


async def test_delete_project_removes_it(fake_mongo, fake_redis, scene):
    from auth import decode_token

    auth_result = await projects_module.register("alice", "password123")
    user_id = decode_token(auth_result.access_token)["sub"]
    project = await projects_module.create_project(user_id, "Временный", scene)

    await projects_module.delete_project(user_id, project.id)
    with pytest.raises(projects_module.NotFoundError):
        await projects_module.get_project(user_id, project.id)


async def test_delete_project_owned_by_another_user_raises_not_found(fake_mongo, fake_redis, scene):
    from auth import decode_token

    alice = await projects_module.register("alice", "password123")
    bob = await projects_module.register("bob", "password456")
    alice_id = decode_token(alice.access_token)["sub"]
    bob_id = decode_token(bob.access_token)["sub"]
    project = await projects_module.create_project(alice_id, "Двор Алисы", scene)

    with pytest.raises(projects_module.NotFoundError):
        await projects_module.delete_project(bob_id, project.id)


async def test_delete_nonexistent_project_raises_not_found(fake_mongo, fake_redis):
    with pytest.raises(projects_module.NotFoundError):
        await projects_module.delete_project("user-1", "not-a-valid-object-id")


async def test_create_project_after_deleting_one_is_allowed(fake_mongo, fake_redis, scene):
    """Лимит -- не больше MAX_PROJECTS_PER_USER одновременно, не всего за всё
    время: после удаления одного место освобождается."""
    from auth import decode_token

    auth_result = await projects_module.register("alice", "password123")
    user_id = decode_token(auth_result.access_token)["sub"]

    ids = []
    for i in range(projects_module.MAX_PROJECTS_PER_USER):
        project = await projects_module.create_project(user_id, f"Проект {i}", scene)
        ids.append(project.id)

    await projects_module.delete_project(user_id, ids[0])
    await projects_module.create_project(user_id, "Новый проект", scene)  # не должно поднять исключение
