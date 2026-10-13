"""HTTP service: REST API for modernization runs plus a static dashboard."""

from modernize.api.app import create_app

__all__ = ["create_app"]
