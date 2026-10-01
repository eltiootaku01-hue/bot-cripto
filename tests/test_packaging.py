from importlib.metadata import version

def test_editable_distribution_metadata_is_available():
    assert version("bot-obrero") == "0.1.0"
