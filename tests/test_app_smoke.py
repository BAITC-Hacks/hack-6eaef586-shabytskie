from pathlib import Path
from streamlit.testing.v1 import AppTest

def test_streamlit_app_renders_without_providers():
    app = AppTest.from_file(Path(__file__).resolve().parents[1] / "app.py").run()
    assert not app.exception
    assert any(title.value == "StockPilot" for title in app.title)


def test_demo_flow_reaches_recommendations_dashboard():
    app = AppTest.from_file(Path(__file__).resolve().parents[1] / "app.py").run()
    app.button[0].click().run()
    app.button[0].click().run(timeout=30)
    assert not app.exception
    assert len(app.metric) == 5
    assert len(app.tabs) == 5
    assert app.metric[0].value == "40"

