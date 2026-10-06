"""Compatibility for the original dictionary-based internal Service API.

New callers should import BacktestService from am_backtesting or its application module.
"""

from .application import BacktestService
from .models import CorrelationRequest


class Service(BacktestService):
    def create(self, request):
        return super().create(request).model_dump(mode="json")

    def run(self, request):
        return super().run(request).model_dump(mode="json")

    def retention(self, run_ids):
        request = (
            run_ids
            if isinstance(run_ids, CorrelationRequest)
            else CorrelationRequest(run_ids=run_ids)
        )
        return super().retention(request).model_dump(mode="json")
