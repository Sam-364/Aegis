"""Workflow tests run against Temporal's time-skipping test server (downloaded on first use)."""

from __future__ import annotations

import pytest_asyncio
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def temporal_env():  # type: ignore[no-untyped-def]
    env = await WorkflowEnvironment.start_time_skipping(data_converter=pydantic_data_converter)
    try:
        yield env
    finally:
        await env.shutdown()
