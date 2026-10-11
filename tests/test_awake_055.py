"""0.55: a running job holds Windows' sleep back; it is let go afterwards, also after an error."""
import pytest

from lunelis import awake


def test_sleep_is_held_back_only_while_the_block_runs(monkeypatch):
    calls = []
    monkeypatch.setattr(awake, "_set", calls.append)
    with awake.awake():
        assert calls == [awake.ES_CONTINUOUS | awake.ES_SYSTEM_REQUIRED]
    assert calls[-1] == awake.ES_CONTINUOUS
    with pytest.raises(ValueError):
        with awake.awake():
            raise ValueError("a job's bug")
    assert calls[-1] == awake.ES_CONTINUOUS and len(calls) == 4


def test_the_job_runner_uses_it():
    import inspect
    from lunelis.ui import jobs
    assert "with awake():" in inspect.getsource(jobs.JobRunner.run)
    awake._set(awake.ES_CONTINUOUS)            # the real call doesn't raise
