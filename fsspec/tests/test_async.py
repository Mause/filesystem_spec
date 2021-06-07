import asyncio
import inspect
import os
import sys
import time

import pytest

import fsspec
import fsspec.asyn
from fsspec.asyn import _throttled_gather


def test_sync_methods():
    inst = fsspec.asyn.AsyncFileSystem()
    assert inspect.iscoroutinefunction(inst._info)
    assert hasattr(inst, "info")
    assert not inspect.iscoroutinefunction(inst.info)


@pytest.mark.skipif(fsspec.asyn.PY36, reason="missing asyncio features o py36")
def test_interrupt():
    loop = fsspec.asyn.get_loop()

    async def f():
        await asyncio.sleep(1000000)
        return True

    fut = asyncio.run_coroutine_threadsafe(f(), loop)
    time.sleep(0.01)  # task launches
    out = fsspec.asyn._dump_running_tasks(with_task=True)
    task = out[0]["task"]
    assert task.done() and fut.done()
    assert isinstance(fut.exception(), fsspec.asyn.FSSpecCoroutineCancel)


class _DummyAsyncKlass:
    def __init__(self):
        self.loop = fsspec.asyn.get_loop()

    async def _dummy_async_func(self):
        # Sleep 1 second function to test timeout
        await asyncio.sleep(1)
        return True

    dummy_func = fsspec.asyn.sync_wrapper(_dummy_async_func)


@pytest.mark.skipif(sys.version_info < (3, 7), reason="no asyncio.run in <3.7")
def test_sync_wrapper_timeout_on_less_than_expected_wait_time_not_finish_function():
    test_obj = _DummyAsyncKlass()
    with pytest.raises(fsspec.FSTimeoutError):
        test_obj.dummy_func(timeout=0.1)


@pytest.mark.skipif(sys.version_info < (3, 7), reason="no asyncio.run in <3.7")
def test_sync_wrapper_timeout_on_more_than_expected_wait_time_will_finish_function():
    test_obj = _DummyAsyncKlass()
    assert test_obj.dummy_func(timeout=5)


@pytest.mark.skipif(sys.version_info < (3, 7), reason="no asyncio.run in <3.7")
def test_sync_wrapper_timeout_none_will_wait_func_finished():
    test_obj = _DummyAsyncKlass()
    assert test_obj.dummy_func(timeout=None)


@pytest.mark.skipif(sys.version_info < (3, 7), reason="no asyncio.run in <3.7")
def test_sync_wrapper_treat_timeout_0_as_none():
    test_obj = _DummyAsyncKlass()
    assert test_obj.dummy_func(timeout=0)


@pytest.mark.skipif(sys.version_info < (3, 7), reason="no asyncio.run in <3.7")
def test_throttled_gather(monkeypatch):
    total_running = 0

    async def runner():
        nonlocal total_running

        total_running += 1
        await asyncio.sleep(0)
        if total_running > 4:
            raise ValueError("More than 4 coroutines are running together")
        total_running -= 1
        return 1

    async def main(**kwargs):
        nonlocal total_running

        total_running = 0
        coros = [runner() for _ in range(32)]
        results = await _throttled_gather(coros, **kwargs)
        for result in results:
            if isinstance(result, Exception):
                raise result
        return results

    assert sum(asyncio.run(main(batch_size=4))) == 32

    with pytest.raises(ValueError):
        asyncio.run(main(batch_size=5, return_exceptions=True))

    with pytest.raises(ValueError):
        asyncio.run(main(batch_size=-1, return_exceptions=True))

    assert sum(asyncio.run(main(batch_size=4))) == 32

    monkeypatch.setitem(fsspec.config.conf, "gather_batch_size", 5)
    with pytest.raises(ValueError):
        asyncio.run(main(return_exceptions=True))
    assert sum(asyncio.run(main(batch_size=4))) == 32  # override

    monkeypatch.setitem(fsspec.config.conf, "gather_batch_size", 4)
    assert sum(asyncio.run(main())) == 32  # override


@pytest.mark.skipif(os.name != "nt", reason="only for windows")
def test_windows_policy():
    from asyncio.windows_events import SelectorEventLoop

    loop = fsspec.asyn.get_loop()
    policy = asyncio.get_event_loop_policy()

    # Ensure that the created loop always uses selector policy
    assert isinstance(loop, SelectorEventLoop)

    # Ensure that the global policy is not changed and it is
    # set to the default one. This is important since the
    # get_loop() method will temporarily override the policy
    # with the one which uses selectors on windows, so this
    # check ensures that we are restoring the old policy back
    # after our change.
    assert isinstance(policy, asyncio.DefaultEventLoopPolicy)
