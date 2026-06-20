"""
Fixture condivise per l'intera suite di test.
"""
import pytest
import core.cds_manual as manual_module
import fidal_cds_tool as app_module


@pytest.fixture
def isolated_manual(tmp_path, monkeypatch):
    """Reindirizza MANUAL_FILE su un file temporaneo per isolare ogni test."""
    path = str(tmp_path / "manual_entries.json")
    monkeypatch.setattr(manual_module, "MANUAL_FILE", path)
    return path


@pytest.fixture
def flask_client(isolated_manual):
    """Flask test client con storage manuale isolato."""
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c
