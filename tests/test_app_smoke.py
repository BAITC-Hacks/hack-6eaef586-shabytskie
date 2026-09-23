from pathlib import Path
from streamlit.testing.v1 import AppTest

def test_streamlit_app_renders_without_providers():
    app = AppTest.from_file(Path(__file__).resolve().parents[1] / "app.py").run()
    assert not app.exception
    assert any(title.value == "StockPilot" for title in app.title)

