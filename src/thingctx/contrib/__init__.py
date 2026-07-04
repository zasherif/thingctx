# Copyright 2026 The thingctx Authors
# SPDX-License-Identifier: Apache-2.0
"""Optional helpers built on ThingClient. Import only what you use.

from thingctx.contrib import LLMHost   # text tool-calling loop
"""

from thingctx.contrib.llm import LLMHost

__all__ = ["LLMHost"]
