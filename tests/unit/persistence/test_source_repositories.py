import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import func, inspect, select, text
from sqlalchemy.exc import IntegrityError

from research_workbench.persistence import (
    Database,
    EntityNotFoundError,
    ProjectRepository,
    QuestionRepository,
    QuestionSourceRepository,
    SourceRepository,
)
from research_workbench.persistence import source_repositories as module
from research_workbench.persistence.source_models import QuestionSourceRow, SourceRow


def question(projects, questions):
    return questions.create(projects.create("Project").id, "Question")


def test_reference_is_unique_by_scoped_identity(sources):
    personal = sources.get_or_create_reference("FAKE0001")
    assert sources.get_or_create_reference("FAKE0001") == personal
    assert sources.get(personal.id) == personal
    group = sources.get_or_create_reference("FAKE0001", scope="group:42")
    assert group.id != personal.id
    assert group.external_key == personal.external_key
    assert sources.get(9999) is None


def test_many_to_many_and_duplicate_attach(projects, questions, sources, links):
    project = projects.create("Project")
    a, b = questions.create(project.id, "A"), questions.create(project.id, "B")
    one, two = (
        sources.get_or_create_reference("FAKE0001"),
        sources.get_or_create_reference("FAKE0002"),
    )
    first = links.attach(a.id, one.id)
    assert links.attach(a.id, one.id) == first
    second = links.attach(a.id, two.id)
    reused = links.attach(b.id, one.id)
    assert links.list_for_question(a.id) == (first, second)
    assert links.list_for_question(b.id) == (reused,)
    assert links.list_for_question(a.id, limit=1, offset=1) == (second,)


def test_attach_reference_is_atomic_idempotent_and_detach_preserves_source(
    projects, questions, sources, links
):
    q = question(projects, questions)
    link = links.attach_reference(q.id, "FAKE0001")
    assert links.attach_reference(q.id, "FAKE0001") == link
    assert links.detach(q.id, link.source_id) is True
    assert links.detach(q.id, link.source_id) is False
    assert links.list_for_question(q.id) == ()
    assert sources.get(link.source_id) == link.source


def test_detach_does_not_affect_other_question(projects, questions, links):
    project = projects.create("Project")
    a, b = questions.create(project.id, "A"), questions.create(project.id, "B")
    one = links.attach_reference(a.id, "FAKE0001")
    other = links.attach_reference(b.id, "FAKE0001")
    links.detach(a.id, one.source_id)
    assert links.list_for_question(b.id) == (other,)


def test_failure_after_reference_insert_rolls_back_reference_and_link(
    database, projects, questions, links, monkeypatch
):
    q = question(projects, questions)

    def fail(*args):
        raise RuntimeError("Simulated association failure")

    monkeypatch.setattr(module, "_attach", fail)
    with pytest.raises(RuntimeError, match="Simulated"):
        links.attach_reference(q.id, "FAKE0001")
    with database._session() as session:
        assert session.scalar(select(func.count()).select_from(SourceRow)) == 0
        assert session.scalar(select(func.count()).select_from(QuestionSourceRow)) == 0


def test_missing_question_does_not_leave_source(database, links):
    with pytest.raises(EntityNotFoundError):
        links.attach_reference(9999, "FAKE0001")
    with database._session() as session:
        assert session.scalar(select(func.count()).select_from(SourceRow)) == 0


def test_missing_source_or_question_has_clear_error(projects, questions, links):
    q = question(projects, questions)
    with pytest.raises(EntityNotFoundError):
        links.attach(q.id, 9999)
    with pytest.raises(EntityNotFoundError):
        links.list_for_question(9999)
    with pytest.raises(EntityNotFoundError):
        links.detach(9999, 1)


@pytest.mark.parametrize(
    "key,kwargs",
    [
        ("bad", {}),
        ("fake0001", {}),
        ("FAKE0001", {"provider": "blog"}),
        ("FAKE0001", {"scope": " "}),
        ("FAKE0001", {"scope": "x" * 129}),
    ],
)
def test_invalid_reference_identity_is_rejected(sources, key, kwargs):
    with pytest.raises(ValueError):
        sources.get_or_create_reference(key, **kwargs)


def test_source_identity_unique_constraint_is_enforced(database, sources):
    sources.get_or_create_reference("FAKE0001")
    with pytest.raises(IntegrityError), database._engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO sources (provider, scope, external_key) "
                "VALUES ('zotero','personal','FAKE0001')"
            )
        )


def test_association_unique_constraint_is_enforced(database, projects, questions, links):
    q = question(projects, questions)
    link = links.attach_reference(q.id, "FAKE0001")
    with pytest.raises(IntegrityError), database._engine.begin() as connection:
        connection.execute(
            text("INSERT INTO question_sources (question_id,source_id) VALUES (:q,:s)"),
            {"q": q.id, "s": link.source_id},
        )


@pytest.mark.parametrize("missing", ["question", "source"])
def test_association_foreign_keys_are_enforced(database, projects, questions, sources, missing):
    q = question(projects, questions)
    ref = sources.get_or_create_reference("FAKE0001")
    params = {
        "q": 9999 if missing == "question" else q.id,
        "s": 9999 if missing == "source" else ref.id,
    }
    with pytest.raises(IntegrityError), database._engine.begin() as connection:
        connection.execute(
            text("INSERT INTO question_sources (question_id,source_id) VALUES (:q,:s)"), params
        )


def test_references_and_links_survive_reopen(database, projects, questions, links):
    q = question(projects, questions)
    link = links.attach_reference(q.id, "FAKE0001")
    database.close()
    with Database(database.path) as reopened:
        assert SourceRepository(reopened).get(link.source_id) == link.source
        assert QuestionSourceRepository(reopened).list_for_question(q.id) == (link,)


def test_sources_schema_cannot_store_metadata(database):
    inspector = inspect(database._engine)
    assert {c["name"] for c in inspector.get_columns("sources")} == {
        "id",
        "provider",
        "scope",
        "external_key",
        "created_at",
    }
    assert {c["name"] for c in inspector.get_columns("question_sources")} == {
        "question_id",
        "source_id",
        "created_at",
    }
    assert set(inspector.get_pk_constraint("question_sources")["constrained_columns"]) == {
        "question_id",
        "source_id",
    }
    assert any(
        i["column_names"] == ["source_id"] for i in inspector.get_indexes("question_sources")
    )


def test_upgrade_from_0001_preserves_existing_research_data(tmp_path, monkeypatch):
    path = tmp_path / "existing.db"
    monkeypatch.setenv("WORKBENCH_DATABASE_PATH", str(path))
    config = Config("alembic.ini")
    command.upgrade(config, "0001")
    with Database(path) as db:
        assert set(inspect(db._engine).get_table_names()) == {
            "alembic_version",
            "projects",
            "questions",
        }
        p = ProjectRepository(db).create("Existing project")
        q = QuestionRepository(db).create(p.id, "Existing question")
        db.upgrade()
        link = QuestionSourceRepository(db).attach_reference(q.id, "FAKE0001")
        db.upgrade()
        assert ProjectRepository(db).get(p.id) == p
        assert QuestionRepository(db).get(q.id) == q
        assert QuestionSourceRepository(db).list_for_question(q.id) == (link,)
