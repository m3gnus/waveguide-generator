"""WG's server-side Onshape adapter.

Unlike Fusion, Onshape has no local client: there is nothing to install and no
workspace folder to watch.  This package is WG's own outbound HTTPS client
talking to ``cad.onshape.com``, materialising the same ``wglink`` bundle the
Fusion add-in consumes (CAD-LINK-PLAN.md section 8).
"""

from .credentials import (
    OnshapeCredentials,
    OnshapeCredentialsError,
    credentials_path,
    load_credentials,
)
from .client import (
    OnshapeClient,
    OnshapeError,
    OnshapeHttpError,
    OnshapeTransportError,
)

__all__ = [
    "OnshapeClient",
    "OnshapeCredentials",
    "OnshapeCredentialsError",
    "OnshapeError",
    "OnshapeHttpError",
    "OnshapeTransportError",
    "credentials_path",
    "load_credentials",
]
