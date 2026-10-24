"""calculations.executor

Where a module's pure `run()` executes (BE-33.1, BE-33.3, D-59 §1, §4). One `RunExecutor` per
app (`app.state.run_executor`), built from the settings:

- `CALC_EXECUTION_MODE=inline` runs the module on the event loop, as before Phase 33. Nothing
  is bounded and nothing times out (a module cannot be interrupted in-process).
- `CALC_EXECUTION_MODE=pool` runs it in a bounded process pool: `CALC_WORKERS` worker
  processes, each its own single-process `ProcessPoolExecutor` (a "slot"), so a worker that
  crashes or overruns is replaced alone and the other runs carry on. At most
  `CALC_WORKERS + CALC_WORKER_QUEUE_MAX` runs are admitted at once (executing, waiting for a
  worker, or being pinned); one more is refused 503 `workers_busy` before anything is
  written (`admit`). A run not ended `CALC_RUN_TIMEOUT_SECONDS` after it was written has its
  worker killed and replaced (`WorkerTimeout`); a worker that dies gives `WorkerCrashed`.
  The engine ends the run FAILED with ENGINE_ERROR for both.

Only the module's function, its Decimal context, the frozen inputs and the pinned parameters
cross to the worker (pickled), and only its `ModuleResult` comes back. Pins, every DB read and
write, the post-checks and the hashes stay in the API process, so inline and pool give equal
hashes (D-59 §5). The worker applies the same Decimal context the inline path does.

Workers start with `forkserver` where it exists (a clean server process, not a fork of the
threaded API process), else `spawn`.

The executor also owns the background tasks of `Prefer: respond-async` runs (`spawn`), so
that shutdown can wait for them and none is garbage-collected mid-run.
"""

import asyncio
import contextlib
import logging
import multiprocessing
import time
from collections import deque
from collections.abc import Callable, Coroutine, Mapping
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass, field
from decimal import Context, localcontext
from types import MappingProxyType
from typing import Any

from app.calculations.modules.base import (
    ModuleInputs,
    ModuleResult,
    PinnedParameters,
    PinnedRule,
    RegisteredModule,
)
from app.core import metrics
from app.core.config import ExecutionMode, Settings

logger = logging.getLogger("app.calculations.executor")

# Whole seconds a refused client waits before retrying (FE backs off up to 5 s, FE-33.1).
WORKERS_BUSY_RETRY_AFTER_SECONDS = 5
# Seconds shutdown waits for background runs before cancelling them (each then ends FAILED).
SHUTDOWN_GRACE_SECONDS = 30
# Deadlines are integer monotonic nanoseconds: no binary fraction on the calculation path.
NS = 1_000_000_000

ModuleFunction = Callable[[ModuleInputs, PinnedParameters], ModuleResult]


class WorkersBusy(Exception):
    """Every worker is busy and the queue is full (CALC_WORKER_QUEUE_MAX)."""

    def __init__(self, retry_after: int = WORKERS_BUSY_RETRY_AFTER_SECONDS) -> None:
        super().__init__("workers_busy")
        self.retry_after = retry_after


class WorkerCrashed(Exception):
    """The worker process died (or exited) while running the module; it was replaced."""


class WorkerTimeout(Exception):
    """The run passed CALC_RUN_TIMEOUT_SECONDS; its worker was killed and replaced."""


def _work(
    function: ModuleFunction,
    context: Callable[[], Context],
    inputs: ModuleInputs,
    parameters: Mapping[str, tuple[Any, dict[str, Any]]],
) -> ModuleResult:
    """In the worker: the module under its Decimal context, on parameters re-frozen exactly as
    the engine freezes them (MappingProxyType itself does not pickle)."""
    pinned = {
        code: PinnedRule(rule_version_id, MappingProxyType(values))
        for code, (rule_version_id, values) in parameters.items()
    }
    with localcontext(context()):
        return function(inputs, pinned)


def _plain(parameters: PinnedParameters) -> dict[str, tuple[Any, dict[str, Any]]]:
    return {
        code: (rule.rule_version_id, dict(rule.parameters)) for code, rule in parameters.items()
    }


def _mp_context() -> Any:
    methods = multiprocessing.get_all_start_methods()
    if "forkserver" in methods:
        ctx = multiprocessing.get_context("forkserver")
        # The server imports the modules once; each worker forks from it ready to run.
        ctx.set_forkserver_preload(["app.calculations.modules"])
        return ctx
    return multiprocessing.get_context("spawn")


class _Slot:
    """One worker process (a single-process executor), started on first use."""

    def __init__(self, index: int, mp_context: Any) -> None:
        self.index = index
        self._mp_context = mp_context
        self._pool: ProcessPoolExecutor | None = None

    def pool(self) -> ProcessPoolExecutor:
        if self._pool is None:
            self._pool = ProcessPoolExecutor(max_workers=1, mp_context=self._mp_context)
        return self._pool

    def replace(self) -> None:
        """Kill the worker (if any) and drop its executor; the next use starts a fresh one."""
        pool, self._pool = self._pool, None
        if pool is None:
            return
        # Python 3.12 has no public way to stop a busy worker (3.14 adds kill_workers()), so
        # the private `_processes` is read (a recorded layering exception). Should a Python
        # release drop it, say so: a busy worker then runs on until its module returns.
        if not hasattr(pool, "_processes"):
            logger.warning("calculation_worker_kill_unavailable")
        processes: dict[int, Any] = dict(getattr(pool, "_processes", None) or {})
        for process in processes.values():
            with contextlib.suppress(Exception):
                process.kill()
        pool.shutdown(wait=False, cancel_futures=True)


@dataclass
class Admission:
    """A run admitted to the executor: held from before the run is written until it ends."""

    executor: "RunExecutor | None" = None
    released: bool = False

    def release(self) -> None:
        if not self.released:
            self.released = True
            if self.executor is not None:
                self.executor._admitted -= 1


@dataclass
class RunExecutor:
    mode: ExecutionMode = "inline"
    workers: int = 1
    queue_max: int = 0
    timeout_seconds: int = 600
    _admitted: int = 0
    _slots: list[_Slot] = field(default_factory=list)
    _free: deque[_Slot] = field(default_factory=deque)
    _waiters: deque["asyncio.Future[_Slot]"] = field(default_factory=deque)
    _tasks: set["asyncio.Task[None]"] = field(default_factory=set)
    _closing: bool = False

    def __post_init__(self) -> None:
        if self.pooled:
            context = _mp_context()
            self._slots = [_Slot(i, context) for i in range(self.workers)]
            self._free = deque(self._slots)

    @classmethod
    def from_settings(cls, settings: Settings) -> "RunExecutor":
        return cls(
            mode=settings.CALC_EXECUTION_MODE,
            workers=settings.CALC_WORKERS,
            queue_max=settings.CALC_WORKER_QUEUE_MAX,
            timeout_seconds=settings.CALC_RUN_TIMEOUT_SECONDS,
        )

    @property
    def pooled(self) -> bool:
        return self.mode == "pool"

    @property
    def capacity(self) -> int:
        """Runs admitted at once: one per worker plus the queue."""
        return self.workers + self.queue_max

    @property
    def admitted(self) -> int:
        return self._admitted

    def ready(self) -> bool:
        """The readiness `workers` check (BE-34.5): the pool takes runs until it shuts down.
        A full queue is not "not ready": that run is refused with 503 `workers_busy`."""
        return not self._closing

    def _queue_changed(self) -> None:
        metrics.set_worker_queue(len(self._waiters))  # BE-34.4: runs waiting for a worker

    # --- admission (503 workers_busy) ------------------------------------------------------

    def admit(self) -> Admission:
        """Admit one run, or raise `WorkersBusy` (pool mode only; inline admits all). Never
        awaits, so the count is exact on the event loop."""
        if not self.pooled:
            return Admission()
        if self._admitted >= self.capacity:
            raise WorkersBusy()
        self._admitted += 1
        return Admission(self)

    def deadline(self) -> int | None:
        """The monotonic time (ns) by which a run written now must end (pool mode only)."""
        return time.monotonic_ns() + self.timeout_seconds * NS if self.pooled else None

    # --- execution -------------------------------------------------------------------------

    async def run(
        self,
        module: RegisteredModule,
        inputs: ModuleInputs,
        parameters: PinnedParameters,
        context: Callable[[], Context],
        deadline: int | None = None,
    ) -> ModuleResult:
        """`module.run(inputs, parameters)` under `context`: inline, or in a worker by
        `deadline` (raises `WorkerTimeout` or `WorkerCrashed`; the module's own exception
        is raised as it is)."""
        if not self.pooled:
            with localcontext(context()):
                return module.run(inputs, parameters)
        slot = await self._acquire(deadline)
        healthy = False
        try:
            future = slot.pool().submit(_work, module.function, context, inputs, _plain(parameters))
            try:
                result: ModuleResult = await asyncio.wait_for(
                    asyncio.wrap_future(future), timeout=self._remaining(deadline)
                )
            except TimeoutError:
                raise WorkerTimeout() from None
            except BrokenProcessPool:
                raise WorkerCrashed() from None
            except Exception:
                healthy = True  # the module raised; its worker is fine
                raise
            except asyncio.CancelledError:
                raise  # the request or shutdown: the worker is replaced below
            except BaseException:  # e.g. SystemExit raised in the worker
                raise WorkerCrashed() from None
            healthy = True
            return result
        finally:
            if not healthy:
                slot.replace()
            self._release(slot)

    @staticmethod
    def _remaining(deadline: int | None) -> Any:
        """Seconds left before `deadline` (None: no deadline), for asyncio.wait_for."""
        if deadline is None:
            return None
        return max(0, deadline - time.monotonic_ns()) / NS

    async def _acquire(self, deadline: int | None) -> _Slot:
        """A free worker, in arrival order; waiting counts against the run's deadline."""
        if self._free and not self._waiters:
            return self._free.popleft()
        waiter: asyncio.Future[_Slot] = asyncio.get_running_loop().create_future()
        self._waiters.append(waiter)
        self._queue_changed()
        try:
            # shield: a timeout must not cancel a waiter that was just handed a slot.
            return await asyncio.wait_for(asyncio.shield(waiter), self._remaining(deadline))
        except BaseException as exc:
            if waiter.done() and not waiter.cancelled():
                self._release(waiter.result())  # handed a slot it will not use: pass it on
            else:
                waiter.cancel()
                with contextlib.suppress(ValueError):
                    self._waiters.remove(waiter)
                self._queue_changed()
            if isinstance(exc, TimeoutError):
                raise WorkerTimeout() from None
            raise

    def _release(self, slot: _Slot) -> None:
        while self._waiters:
            waiter = self._waiters.popleft()
            if not waiter.done():
                waiter.set_result(slot)
                self._queue_changed()
                return
        self._queue_changed()
        self._free.append(slot)

    # --- background runs (Prefer: respond-async) -----------------------------------------

    def spawn(self, coroutine: Coroutine[Any, Any, None]) -> None:
        """Run `coroutine` in the background, kept until done (shutdown waits for it)."""
        task = asyncio.get_running_loop().create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    @property
    def background(self) -> frozenset["asyncio.Task[None]"]:
        return frozenset(self._tasks)

    async def aclose(self, grace: int = SHUTDOWN_GRACE_SECONDS) -> None:
        """Wait up to `grace` seconds for background runs, cancel the rest (each ends FAILED
        with ENGINE_ERROR as it unwinds), then stop every worker."""
        self._closing = True
        tasks = list(self._tasks)
        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=grace)
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
        for slot in self._slots:
            slot.replace()


# The executor of services built without one (reads, scripts): inline, as before Phase 33.
INLINE = RunExecutor()
