"""Guards the add-on packaging.

These constraints are easy to get wrong and the failure mode is always the same:
the add-on does not appear, or it appears and refuses to start, with a message
from Supervisor that does not say which line is at fault. Most of them changed
recently enough that copying an older add-on gets them wrong.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from ha_smartmeter import __version__, suppliers
from ha_smartmeter.web.server import DEFAULT_PORT

ROOT = Path(__file__).resolve().parents[1]
ADDON = ROOT / "smartmeter"


@pytest.fixture(scope="module")
def config() -> dict:
    return yaml.safe_load((ADDON / "config.yaml").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def dockerfile() -> str:
    return (ADDON / "Dockerfile").read_text(encoding="utf-8")


class TestRepository:
    def test_the_repository_has_its_metadata_at_the_root(self):
        data = yaml.safe_load((ROOT / "repository.yaml").read_text(encoding="utf-8"))
        assert data["name"]
        assert data["url"].startswith("https://github.com/")

    def test_build_yaml_is_not_used(self):
        # Since Supervisor 2026.04.0 build.yaml is gone and BUILD_FROM is not
        # supplied. A build.yaml here would be silently ignored.
        assert not list(ROOT.rglob("build.yaml"))

    def test_the_config_is_yaml_not_json(self):
        assert (ADDON / "config.yaml").is_file()
        assert not (ADDON / "config.json").exists()


class TestConfigYaml:
    def test_init_is_false_for_the_s6_base_image(self, config):
        # Without this the s6-overlay init never runs and the add-on does not
        # start at all.
        assert config["init"] is False

    def test_both_architectures_are_targeted(self, config):
        assert sorted(config["arch"]) == ["aarch64", "amd64"]

    def test_serial_devices_are_mapped_in(self, config):
        assert config["uart"] is True

    def test_the_broker_comes_from_supervisor(self, config):
        assert config["services"] == ["mqtt:need"]

    def test_captures_have_somewhere_to_go(self, config):
        assert "addon_config:rw" in config["map"]

    def test_the_device_option_renders_as_a_dropdown(self, config):
        assert config["schema"]["port"].startswith("device(subsystem=tty)")

    def test_the_key_is_validated_before_the_add_on_starts(self, config):
        assert config["schema"]["key"] == "match(^[0-9A-Fa-f]{32}$)"

    def test_the_key_is_required_and_has_no_default(self, config):
        assert not config["schema"]["key"].endswith("?")
        assert "key" not in config["options"]

    def test_ingress_points_at_the_status_page(self, config):
        assert config["ingress"] is True
        assert config["ingress_port"] == DEFAULT_PORT

    def test_the_stage_is_honest(self, config):
        # Nothing has been confirmed against a physical meter yet.
        assert config["stage"] == "experimental"

    def test_every_supplier_profile_is_offered_in_the_dropdown(self, config):
        offered = set(
            re.fullmatch(r"list\((.*)\)", config["schema"]["supplier"].strip())[1].split("|")
        )
        assert offered == set(suppliers.load_all())

    def test_the_default_supplier_exists(self, config):
        assert config["options"]["supplier"] in suppliers.load_all()

    def test_the_default_publishes_every_telegram(self, config):
        assert config["options"]["min_publish_interval"] == 0

    def test_capture_is_off_by_default(self, config):
        assert config["options"]["capture_raw"] is False

    def test_every_default_passes_its_own_schema(self, config):
        # A default that the schema rejects makes the add-on unstartable out of
        # the box, and the message Supervisor gives says nothing useful.
        for key in config["options"]:
            assert key in config["schema"], f"{key} has a default but no schema"


class TestVersions:
    def test_the_version_is_the_same_everywhere(self, config):
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        assert config["version"] == __version__
        assert f'version = "{__version__}"' in pyproject

    def test_the_changelog_mentions_the_current_version(self, config):
        changelog = (ADDON / "CHANGELOG.md").read_text(encoding="utf-8")
        assert f"## {config['version']}" in changelog


class TestDockerfile:
    def test_the_base_image_is_explicit_and_pinned(self, dockerfile):
        match = re.search(r"^FROM (\S+)", dockerfile, re.MULTILINE)
        assert match, "no FROM line"
        image = match[1]
        assert "$" not in image, "BUILD_FROM is no longer provided by Supervisor"
        assert not image.endswith(":latest")
        assert re.search(r":\d+\.\d+-alpine\d+\.\d+-\d{4}\.\d+\.\d+$", image), image

    def test_the_label_says_app_not_addon(self, dockerfile):
        assert 'io.hass.type="app"' in dockerfile
        assert 'io.hass.type="addon"' not in dockerfile

    def test_the_application_and_the_profiles_are_copied_in(self, dockerfile):
        assert "COPY app/" in dockerfile
        assert "COPY suppliers/" in dockerfile

    def test_requirements_are_all_pinned(self):
        lines = [
            line.strip()
            for line in (ADDON / "requirements.txt").read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")
        ]
        assert lines
        for line in lines:
            assert "==" in line, f"{line} is not pinned"


class TestRunScript:
    @pytest.fixture(scope="class")
    def run_sh(self) -> str:
        return (ADDON / "run.sh").read_text(encoding="utf-8")

    def test_it_uses_bashio(self, run_sh):
        assert run_sh.startswith("#!/usr/bin/with-contenv bashio")

    def test_the_broker_credentials_come_from_supervisor(self, run_sh):
        for field in ("host", "port", "username", "password"):
            assert f'bashio::services mqtt "{field}"' in run_sh

    def test_it_execs_so_signals_arrive(self, run_sh):
        assert "exec python3 -m ha_smartmeter run" in run_sh

    def test_line_endings_are_unix(self):
        # A CRLF here makes s6 report "no such file or directory" for a file
        # that is plainly there.
        assert b"\r\n" not in (ADDON / "run.sh").read_bytes()


class TestTranslations:
    LANGUAGES = ("en", "de")

    @pytest.mark.parametrize("language", LANGUAGES)
    def test_every_option_is_described(self, config, language):
        translation = yaml.safe_load(
            (ADDON / "translations" / f"{language}.yaml").read_text(encoding="utf-8")
        )
        described = set(translation["configuration"])
        options = set(config["schema"])
        assert options - described == set(), "options with no description"
        assert described - options == set(), "descriptions for options that do not exist"

    @pytest.mark.parametrize("language", LANGUAGES)
    def test_every_description_says_something(self, language):
        translation = yaml.safe_load(
            (ADDON / "translations" / f"{language}.yaml").read_text(encoding="utf-8")
        )
        for key, entry in translation["configuration"].items():
            assert entry["name"], key
            assert len(entry["description"]) > 30, f"{key} is described too thinly"


class TestPresentation:
    def test_the_icon_is_a_square_png(self):
        width, height = _png_size(ADDON / "icon.png")
        assert width == height == 128

    def test_the_logo_is_the_recommended_shape(self):
        width, height = _png_size(ADDON / "logo.png")
        assert (width, height) == (250, 100)

    def test_the_documentation_tab_has_content(self):
        assert len((ADDON / "DOCS.md").read_text(encoding="utf-8")) > 1000

    def test_apparmor_allows_serial_devices(self):
        profile = (ADDON / "apparmor.txt").read_text(encoding="utf-8")
        assert "/dev/tty[A-Z]* rw," in profile
        assert "/config/** rw," in profile


def _png_size(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
