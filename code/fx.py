"""Currency conversion using dated exchange_rates.csv entries.

All output amounts are in the user's home_currency. Rates are fixed and
dated (monthly, on the 15th); every foreign-currency cash event in the
dataset settles exactly on a date that has a rate row, so exact-date
matching is used (with a nearest-prior fallback as a safety net).
"""

from __future__ import annotations

from datetime import date


class RateTable:
    def __init__(self, rates):
        self._by_key = {}
        for r in rates:
            key = (r.rate_date, r.from_currency, r.to_currency)
            self._by_key[key] = r.rate
            self._by_key.setdefault(
                (r.rate_date, r.to_currency, r.from_currency), 1.0 / r.rate
            )
        self._by_pair = {}
        for (d, fc, tc), rate in self._by_key.items():
            self._by_pair.setdefault((fc, tc), []).append((d, rate))
        for pair in self._by_pair.values():
            pair.sort()

    def rate(self, on_date: date, from_ccy: str, to_ccy: str) -> float:
        if from_ccy == to_ccy:
            return 1.0
        key = (on_date, from_ccy, to_ccy)
        if key in self._by_key:
            return self._by_key[key]
        # nearest prior dated rate for the pair (same direction)
        prior = [r for d, r in self._by_pair.get((from_ccy, to_ccy), []) if d <= on_date]
        if prior:
            return prior[-1]
        raise KeyError(f"No exchange rate for {from_ccy}->{to_ccy} on {on_date}")

    def convert(self, amount: float, on_date: date, from_ccy: str, to_ccy: str) -> float:
        return amount * self.rate(on_date, from_ccy, to_ccy)