"""Local .env must configure plugin settings just as Compose env_file does."""
from subitoo.config import Settings


def test_plugin_settings_read_local_dotenv(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path/'.env').write_text('NTFY_TOPIC=fixture-only-topic\nDEFAULT_CHANNEL=ntfy\n')
    monkeypatch.delenv('NTFY_TOPIC', raising=False)
    assert Settings().for_plugin('ntfy')['topic'] == 'fixture-only-topic'


def test_environment_overrides_dotenv(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path/'.env').write_text('NTFY_TOPIC=file-topic\n')
    monkeypatch.setenv('NTFY_TOPIC', 'environment-topic')
    assert Settings().for_plugin('ntfy')['topic'] == 'environment-topic'
