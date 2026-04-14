from pathlib import Path

from backend.main import resolve_frontend_dist_dir


def test_resolve_frontend_dist_dir_prefers_existing_env_path(tmp_path, monkeypatch):
    env_frontend = tmp_path / "env-frontend"
    env_frontend.mkdir()

    monkeypatch.setenv("FRONTEND_DIST_DIR", str(env_frontend))

    resolved = resolve_frontend_dist_dir(module_file=str(tmp_path / "backend" / "main.py"))

    assert resolved == str(env_frontend)


def test_resolve_frontend_dist_dir_falls_back_to_resources_sibling(tmp_path, monkeypatch):
    resources_dir = tmp_path / "resources"
    backend_dir = resources_dir / "backend"
    frontend_dir = resources_dir / "frontend-dist"
    backend_dir.mkdir(parents=True)
    frontend_dir.mkdir()

    monkeypatch.delenv("FRONTEND_DIST_DIR", raising=False)

    resolved = resolve_frontend_dist_dir(module_file=str(backend_dir / "main.py"))

    assert resolved == str(frontend_dir)
