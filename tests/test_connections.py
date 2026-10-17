"""Connection options for managed services (Elastic Cloud, RDS): no services needed."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest

pytest.importorskip("psycopg2")
pytest.importorskip("elasticsearch")

import clients
from config import settings


@pytest.fixture
def override():
    saved = {}

    def apply(**values):
        for key, value in values.items():
            saved.setdefault(key, getattr(settings, key))
            object.__setattr__(settings, key, value)

    yield apply
    for key, value in saved.items():
        object.__setattr__(settings, key, value)


def test_elasticsearch_defaults_add_nothing(override):
    override(elasticsearch_api_key="", elasticsearch_username="", elasticsearch_ca_certs="", elasticsearch_verify_certs=True)
    assert clients.elasticsearch_kwargs() == {}


def test_elasticsearch_api_key_wins_over_basic_auth(override):
    override(elasticsearch_api_key="abc", elasticsearch_username="elastic", elasticsearch_password="pw",
             elasticsearch_ca_certs="/etc/ca.pem", elasticsearch_verify_certs=False)
    assert clients.elasticsearch_kwargs() == {"api_key": "abc", "ca_certs": "/etc/ca.pem", "verify_certs": False}
    override(elasticsearch_api_key="")
    assert clients.elasticsearch_kwargs()["basic_auth"] == ("elastic", "pw")


def test_postgres_ssl_options(override):
    override(postgres_sslmode="", postgres_sslrootcert="")
    assert "sslmode" not in clients.postgres_params()
    override(postgres_sslmode="verify-full", postgres_sslrootcert="/etc/rds-ca.pem")
    params = clients.postgres_params()
    assert params["sslmode"] == "verify-full" and params["sslrootcert"] == "/etc/rds-ca.pem"
