"""测试公共夹具。"""
import datetime as dt
from pathlib import Path

import pytest

from src.config import Config
from src.mcp_client import McpClient
from src.notify import Notify, NotifyState
from src.state import JsonStore

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def make_config(tmp_path: Path) -> Config:
    return Config.load(
        offline=True,
        fixtures_dir=FIXTURES,
        state_dir=tmp_path,
        log_dir=tmp_path,
    )


@pytest.fixture()
def cfg(tmp_path):
    return make_config(tmp_path)


@pytest.fixture()
def client(cfg):
    return McpClient.create(cfg)


@pytest.fixture()
def notify(tmp_path):
    return Notify(use_color=False, state=NotifyState(JsonStore(tmp_path / "notify.json")))


@pytest.fixture()
def store(tmp_path):
    return JsonStore(tmp_path / "state.json")


@pytest.fixture()
def today():
    return dt.date(2026, 10, 14)
