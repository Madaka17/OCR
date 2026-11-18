import threading

from server.limiter import LoginLimiter


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def fail(lim, key, times=1):
    for _ in range(times):
        assert lim.allowed(key)
        lim.record_failure(key)


def test_fifth_failure_blocks_the_key():
    lim = LoginLimiter(clock=Clock())

    fail(lim, "ip-a", 5)

    assert not lim.allowed("ip-a")


def test_four_failures_still_allowed():
    lim = LoginLimiter(clock=Clock())

    fail(lim, "ip-a", 4)

    assert lim.allowed("ip-a")


def test_key_block_expires_after_fifteen_minutes():
    clock = Clock()
    lim = LoginLimiter(clock=clock)
    fail(lim, "ip-a", 5)

    clock.now += 899.9
    assert not lim.allowed("ip-a")
    clock.now += 0.1
    assert lim.allowed("ip-a")


def test_other_keys_unaffected():
    lim = LoginLimiter(clock=Clock())
    fail(lim, "ip-a", 5)

    assert lim.allowed("ip-b")


def test_success_clears_the_key():
    lim = LoginLimiter(clock=Clock())
    fail(lim, "ip-a", 4)

    lim.record_success("ip-a")
    fail(lim, "ip-a", 4)

    assert lim.allowed("ip-a")


def test_global_cap_blocks_everyone_above_thirty_in_a_minute():
    clock = Clock()
    lim = LoginLimiter(clock=clock)
    for i in range(30):
        lim.record_failure(f"ip-{i}")
    assert lim.allowed("fresh")

    lim.record_failure("ip-30")

    assert not lim.allowed("fresh")
    clock.now += 60
    assert lim.allowed("fresh")


def test_concurrent_failures_are_all_counted():
    lim = LoginLimiter()

    def hammer(n):
        for _ in range(20):
            lim.record_failure(f"ip-{n}")

    threads = [threading.Thread(target=hammer, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not lim.allowed("anyone")  # 80 failures > 30 in the window
