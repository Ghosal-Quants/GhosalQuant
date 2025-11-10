# file: company.py

import yfinance as yf

"""
Creates a Company object with only the selected attributes 
(i.e. non-zero weight attributes).
"""

class Company:
    def __init__(self, ticker, attributes):

        self._ticker_str = ticker.upper()
        yf_ticker = yf.Ticker(self._ticker_str)
        info = yf_ticker.info

        # Dynamically set each attribute that the user cares about
        for attr in attributes:
            setattr(self, attr, info.get(attr, None))

    @property
    def ticker(self):
        return self._ticker_str
