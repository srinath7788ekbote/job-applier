"""
tests/test_main_pipeline.py
Unit tests for pipeline config loading and run guard logic.
"""
import sys
from datetime import date
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from main_pipeline import load_config, already_ran_today, mark_ran_today, Config


MINIMAL_CONFIG = {
    "pipeline": {
        "target_role": ["SRE", "DevOps Engineer"],
        "target_location": ["Dubai"],
        "target_platforms": ["linkedin"],
        "max_jobs_per_run": 5,
        "min_match_score": 60,
    },
    "paths": {
        "base_resume": "data/base_resume.pdf",
        "excel_tracker": "data/jobs_tracker.xlsx",
        "tailored_resumes_dir": "data/tailored",
        "scraper_vendor": "vendor/job-scraper",
        "resume_skill_vendor": "vendor/resume-skill",
        "logs_dir": "logs",
    },
    "playwright": {
        "headless": True,
        "slow_mo": 50,
    },
}


@pytest.fixture
def config_file(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.dump(MINIMAL_CONFIG), encoding="utf-8")
    return path


def test_load_config_returns_config_object(config_file):
    cfg = load_config(config_file)
    assert isinstance(cfg, Config)
    assert cfg.target_role == ["SRE", "DevOps Engineer"]
    assert cfg.target_location == ["Dubai"]
    assert cfg.max_jobs_per_run == 5
    assert cfg.min_match_score == 60
    assert cfg.headless is True


def test_load_config_string_role_becomes_list(tmp_path):
    data = {**MINIMAL_CONFIG}
    data["pipeline"] = {**data["pipeline"], "target_role": "SRE"}
    path = tmp_path / "config.yaml"
    path.write_text(yaml.dump(data), encoding="utf-8")

    cfg = load_config(path)
    assert cfg.target_role == ["SRE"]


def test_load_config_string_location_becomes_list(tmp_path):
    data = {**MINIMAL_CONFIG}
    data["pipeline"] = {**data["pipeline"], "target_location": "Dubai"}
    path = tmp_path / "config.yaml"
    path.write_text(yaml.dump(data), encoding="utf-8")

    cfg = load_config(path)
    assert cfg.target_location == ["Dubai"]


def test_load_config_uses_defaults(config_file):
    cfg = load_config(config_file)
    assert cfg.days_back == 7  # default
    assert cfg.resume_template == "professional"  # default
    assert cfg.min_delay == 1.5  # default
    assert cfg.max_delay == 4.0  # default


def test_already_ran_today_false_no_file(tmp_path):
    assert already_ran_today(tmp_path) is False


def test_already_ran_today_true_when_today_in_file(tmp_path):
    ran_file = tmp_path / "ran_dates.txt"
    ran_file.write_text(str(date.today()) + "\n", encoding="utf-8")
    assert already_ran_today(tmp_path) is True


def test_already_ran_today_false_when_different_date(tmp_path):
    ran_file = tmp_path / "ran_dates.txt"
    ran_file.write_text("2020-01-01\n", encoding="utf-8")
    assert already_ran_today(tmp_path) is False


def test_mark_ran_today_creates_file(tmp_path):
    logs_dir = tmp_path / "logs"
    mark_ran_today(logs_dir)
    ran_file = logs_dir / "ran_dates.txt"
    assert ran_file.exists()
    assert str(date.today()) in ran_file.read_text(encoding="utf-8")


def test_mark_ran_today_appends(tmp_path):
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    ran_file = logs_dir / "ran_dates.txt"
    ran_file.write_text("2020-01-01\n", encoding="utf-8")

    mark_ran_today(logs_dir)
    content = ran_file.read_text(encoding="utf-8")
    assert "2020-01-01" in content
    assert str(date.today()) in content
