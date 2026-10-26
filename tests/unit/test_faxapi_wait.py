"""FaxApi.wait: the status of the previous fax stays visible until the next one starts."""
import pytest

from fritzfax.core import faxapi
from fritzfax.core.errors import FaxFailed


def api_with(statuses):
    seq = iter(statuses)
    api = faxapi.FaxApi(None)
    api.status = lambda: (lambda s: faxapi.FaxStatus(s[0], faxapi.STATUS_TEXT[s[0]], s[1], s[2]))(next(seq))
    return api


def run(api, **kw):
    clock = iter(range(0, 100000))
    return api.wait(poll_interval=0, sleep=lambda s: None, clock=lambda: next(clock), **kw)


@pytest.mark.parametrize("leftover", [(-1, 0, 0), (0, 0, 0), (1, 100, 0x3319), (5, 100, 0)])
def test_a_leftover_status_before_the_start_is_not_the_result(leftover):
    st = run(api_with([leftover, (2, 0, 0), (3, 0, 0), (4, 100, 0), (1, 100, 0)]))
    assert st.status == 1 and st.reason == 0


def test_error_status_after_the_start_fails():
    with pytest.raises(FaxFailed, match="unavailable"):
        run(api_with([(2, 0, 0), (0, 0, 0)]))


def test_box_that_stays_unavailable_is_reported_as_such():
    with pytest.raises(FaxFailed, match="unavailable"):
        run(api_with([(-1, 0, 0)] * 200), start_timeout=50)


def test_box_that_never_starts():
    with pytest.raises(FaxFailed, match="did not start"):
        run(api_with([(1, 100, 0)] * 200), start_timeout=50)
