"""Harbor Daytona environment with run labels, a TTL safety net, and per-phase domain allowlists.

Load with:  harbor run ... -e daytona --environment-import-path labeled_daytona:LabeledDaytonaEnv
(this file's directory must be on PYTHONPATH).

Stock DaytonaEnvironment (harbor 3de07a0e) advertises no allowlist support and creates unlabeled
sandboxes. The pinned Daytona SDK (0.220.0) supports both, verified empirically on our org:
  * `sandbox.update_network_settings(domain_allow_list="*.trycloudflare.com")` blocks everything
    except the listed domains (leading-`*.` wildcards accepted; bare `*` rejected).
  * Restoring public access needs `network_block_all=False, domain_allow_list=""`:
    `network_block_all=False` alone leaves the domain allowlist in force.
Harbor wraps only `agent.run()` in the agent-phase policy, so agent install happens before the
block, and the verifier runs after the restore (public network, which ~18% of verifiers need).

Env vars:
  MIMO_RUN_ID           label "run" (default "mimo-qwen36")
  MIMO_TTL_MINUTES      wall-clock lifetime cap per sandbox (default 360; >= build 30 + agent 60
                        + verifier 30 min + Harbor retries/queueing, with margin)
  MIMO_NET_SETTLE_SEC   wait after each policy switch (default 2)
"""

from __future__ import annotations

import asyncio
import os

from harbor.environments.capabilities import EnvironmentCapabilities
from harbor.environments.daytona import DaytonaEnvironment
from harbor.models.task.config import NetworkMode, NetworkPolicy

OWNER = "charlieruan"
PURPOSE = "mimo-harbor-eval"


def run_labels() -> dict[str, str]:
    return {"owner": OWNER, "run": os.environ.get("MIMO_RUN_ID", "mimo-qwen36"), "purpose": PURPOSE}


class LabeledDaytonaEnv(DaytonaEnvironment):
    @property
    def capabilities(self) -> EnvironmentCapabilities:
        caps = super().capabilities
        return caps.model_copy(update={"network_allowlist": True, "dynamic_network_policy": True})

    def _extra_create_fields(self) -> dict:
        return {"labels": run_labels(), "ttl_minutes": int(os.environ.get("MIMO_TTL_MINUTES", "360"))}

    def _image_sandbox_params(self, **kwargs):
        params = super()._image_sandbox_params(**kwargs)
        return params.model_copy(update=self._extra_create_fields())

    def _snapshot_sandbox_params(self, snapshot_name: str):
        params = super()._snapshot_sandbox_params(snapshot_name)
        return params.model_copy(update=self._extra_create_fields())

    async def _apply_network_policy(self, network_policy: NetworkPolicy) -> None:
        if not self._sandbox:
            raise RuntimeError("Sandbox not found. Please start the environment first.")
        mode = network_policy.network_mode
        if mode == NetworkMode.ALLOWLIST:
            kw = {"domain_allow_list": ",".join(network_policy.allowed_hosts)}
        elif mode == NetworkMode.NO_NETWORK:
            kw = {"network_block_all": True}
        else:  # PUBLIC: clearing the domain list is required; block_all=False alone keeps it
            kw = {"network_block_all": False, "domain_allow_list": ""}
        last: Exception | None = None
        for attempt in range(3):
            try:
                await self._sandbox.update_network_settings(**kw)
                break
            except Exception as e:  # transient API errors; fail the trial if it never applies
                last = e
                await asyncio.sleep(2 * (attempt + 1))
        else:
            raise RuntimeError(f"update_network_settings({kw}) failed: {last}")
        self.logger.debug("network policy -> %s %s", mode.value, kw)
        await asyncio.sleep(float(os.environ.get("MIMO_NET_SETTLE_SEC", "2")))
