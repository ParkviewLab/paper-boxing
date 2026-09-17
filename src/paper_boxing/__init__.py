# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""paper-boxing: a small local site manager for a home lab.

One distribution, three components (`backend`, `frontend`, `mcp`) and the
`common` package they share. Each component is its own image and process; see
docs/architecture.md.
"""

from paper_boxing.common.config import VERSION

__version__ = VERSION
__all__ = ["VERSION", "__version__"]
