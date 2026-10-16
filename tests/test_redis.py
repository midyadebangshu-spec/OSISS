"""REDIS_URL: answer cache and rate limits shared by API copies, against a real redis-server (skipped if missing)."""

import os
import shutil
import socket
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import numpy as np
import pytest

redis = pytest.importorskip("redis")
pytest.importorskip("torch")

import load_control
from config import settings


@pytest.fixture(scope="module")
def redis_url():
    binary = shutil.which("redis-server")
    if not binary:
        pytest.skip("redis-server not installed")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    process = subprocess.Popen([binary, "--port", str(port), "--save", "", "--appendonly", "no"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f"redis://127.0.0.1:{port}/0"
    client = redis.Redis.from_url(url)
    for _ in range(50):
        try:
            client.ping()
            break
        except redis.ConnectionError:
            time.sleep(0.1)
    yield url
    process.terminate()
    process.wait(5)


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


@pytest.fixture
def client(redis_url):
    connection = redis.Redis.from_url(redis_url)
    connection.flushdb()
    return connection


def test_rate_limit_is_exact_across_copies(client):
    clock = [1000.0]
    copy_a = load_control.RedisRateLimiter(client, clock=lambda: clock[0])
    copy_b = load_control.RedisRateLimiter(redis.Redis(connection_pool=client.connection_pool), clock=lambda: clock[0])
    results = [limiter.check("ip:1", 4) for limiter in (copy_a, copy_b, copy_a, copy_b)]
    assert results == [0.0, 0.0, 0.0, 0.0]
    wait = copy_b.check("ip:1", 4)
    assert 59 < wait <= 60
    assert copy_a.check("ip:2", 4) == 0.0
    clock[0] += 60.5
    assert copy_a.check("ip:1", 4) == 0.0
    assert copy_a.check("ip:1", 0) == 0.0  # 0 = no limit


def test_rate_limit_lets_requests_through_when_redis_is_down():
    dead = redis.Redis(host="127.0.0.1", port=1, socket_connect_timeout=0.2, socket_timeout=0.2)
    limiter = load_control.RedisRateLimiter(dead)
    assert limiter.check("ip:1", 1) == 0.0 and limiter.check("ip:1", 1) == 0.0


def test_answer_cache_is_shared_and_expires(client, override):
    override(search_cache_size=100, search_cache_ttl_seconds=1, state_backend="local")
    copy_a, copy_b = load_control.RedisSearchCache(client), load_control.RedisSearchCache(client)
    key = copy_a.key("what is light?", 3)
    result = {"query": "what is light?", "results": [{"qa_score": np.float32(0.5), "rank": np.int64(1)}]}
    copy_a.put(key, result)
    assert copy_b.get(key) == {"query": "what is light?", "results": [{"qa_score": 0.5, "rank": 1}]}
    assert len(copy_b) == 1
    time.sleep(1.2)
    assert copy_b.get(key) is None


def test_answer_cache_off_and_redis_down(client, override):
    override(search_cache_size=0, search_cache_ttl_seconds=600)
    cache = load_control.RedisSearchCache(client)
    cache.put("k", {"a": 1})
    assert cache.get("k") is None
    override(search_cache_size=100)
    dead = load_control.RedisSearchCache(redis.Redis(host="127.0.0.1", port=1, socket_connect_timeout=0.2))
    dead.put("k", {"a": 1})
    assert dead.get("k") is None


def test_configure_picks_redis(redis_url, override, monkeypatch):
    monkeypatch.setattr(load_control, "cache", load_control.cache)
    monkeypatch.setattr(load_control, "limiter", load_control.limiter)
    override(redis_url=redis_url, state_backend="local")
    notes = load_control.configure()
    assert isinstance(load_control.cache, load_control.RedisSearchCache)
    assert isinstance(load_control.limiter, load_control.RedisRateLimiter)
    assert "Redis" in notes[0]
