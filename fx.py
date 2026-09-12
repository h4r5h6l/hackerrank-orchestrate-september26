"""Currency conversion using dated exchange_rates.csv entries.

All output amounts must be in the user's home_currency. Rates are fixed
and dated (not live), so conversion picks the rate for the specific date
(and pair) rather than any 'latest known rate' fallback -- if the exact
date/pair isn't present, that's a data-completeness issue worth surfacing,
not silently approximating.
"""

from __future__ import annotations

from datetime import date

from models import ExchangeRate


class RateTable:
	def __init__(self, rates: list[ExchangeRate]):
		self._by_key: dict[tuple[date, str, str], float] = {}
		for r in rates:
			self._by_key[(r.rate_date, r.from_currency, r.to_currency)] = r.rate
			# also index the inverse so lookups work either direction
			self._by_key.setdefault(
				(r.rate_date, r.to_currency, r.from_currency), 1.0 / r.rate
			)

	def rate(self, on_date: date, from_ccy: str, to_ccy: str) -> float:
		if from_ccy == to_ccy:
			return 1.0
		key = (on_date, from_ccy, to_ccy)
		if key in self._by_key:
			return self._by_key[key]
		raise KeyError(
			f"No exchange rate for {from_ccy}->{to_ccy} on {on_date}. "
			"Check whether the nearest prior dated rate should be used "
			"instead -- confirm against sample_requests.csv behavior."
		)

	def convert(self, amount: float, on_date: date, from_ccy: str, to_ccy: str) -> float:
		return amount * self.rate(on_date, from_ccy, to_ccy)
