from app.core.ratelimit import FailureLimiter, SlidingWindow


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def test_requests_up_to_the_limit_pass_and_the_next_one_waits():
    clock = Clock()
    window = SlidingWindow(60, clock=clock)
    assert [window.hit("u", 3) for _ in range(3)] == [None, None, None]
    wait = window.hit("u", 3)
    assert wait is not None and 0 < wait <= 60


def test_the_wait_shrinks_as_time_passes_and_clears_when_the_oldest_request_ages_out():
    clock = Clock()
    window = SlidingWindow(60, clock=clock)
    window.hit("u", 2)
    clock.advance(20)
    window.hit("u", 2)
    assert round(window.hit("u", 2)) == 40                      # the first request leaves the window in 40s
    clock.advance(41)
    assert window.hit("u", 2) is None


def test_a_refused_request_does_not_extend_the_wait():
    clock = Clock()
    window = SlidingWindow(60, clock=clock)
    window.hit("u", 1)
    for _ in range(50):
        window.hit("u", 1)                                      # hammering must not push the release time out
    clock.advance(61)
    assert window.hit("u", 1) is None


def test_each_key_has_its_own_budget():
    window = SlidingWindow(60, clock=Clock())
    assert window.hit("a", 1) is None and window.hit("b", 1) is None
    assert window.hit("a", 1) is not None


def test_the_limit_is_read_on_each_call_so_a_changed_setting_applies_at_once():
    window = SlidingWindow(60, clock=Clock())
    window.hit("u", 1)
    assert window.hit("u", 1) is not None
    assert window.hit("u", 5) is None


def test_a_limit_of_zero_or_less_means_no_limit():
    window = SlidingWindow(60, clock=Clock())
    assert all(window.hit("u", 0) is None for _ in range(100))


def test_reset_clears_one_key_or_everything():
    window = SlidingWindow(60, clock=Clock())
    window.hit("a", 1), window.hit("b", 1)
    window.reset("a")
    assert window.hit("a", 1) is None and window.hit("b", 1) is not None
    window.reset()
    assert window.hit("b", 1) is None


def test_idle_keys_are_forgotten_so_memory_stays_bounded():
    clock = Clock()
    window = SlidingWindow(60, clock=clock, max_keys=50)
    for i in range(200):
        window.hit(f"k{i}", 5)
        clock.advance(1)
    assert len(window) <= 110                                    # only keys seen within the window survive


# ---------------------------------------------------------------- failures

def test_failures_lock_a_key_out_after_the_limit():
    clock = Clock()
    failures = FailureLimiter(3, 900, clock=clock)
    for _ in range(2):
        failures.fail("alice")
    assert failures.locked_for("alice") == 0
    failures.fail("alice")
    assert 0 < failures.locked_for("alice") <= 900
    assert failures.locked_for("bob") == 0


def test_the_lock_lifts_when_the_window_passes():
    clock = Clock()
    failures = FailureLimiter(2, 900, clock=clock)
    failures.fail("alice"), failures.fail("alice")
    clock.advance(901)
    assert failures.locked_for("alice") == 0


def test_a_success_clears_the_count():
    failures = FailureLimiter(3, 900, clock=Clock())
    failures.fail("alice"), failures.fail("alice")
    failures.succeed("alice")
    failures.fail("alice"), failures.fail("alice")
    assert failures.locked_for("alice") == 0


def test_failures_do_not_pile_up_a_longer_lock_while_locked():
    clock = Clock()
    failures = FailureLimiter(2, 900, clock=clock)
    failures.fail("a"), failures.fail("a")
    for _ in range(100):
        failures.fail("a")
    clock.advance(901)
    assert failures.locked_for("a") == 0
