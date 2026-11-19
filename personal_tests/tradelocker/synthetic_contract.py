"""Invented public-safe contract-shape fixture. Not an account observation.

All identifiers and numbers here are synthetic; nulls, EQUITY_CFD and the zero
 tick-cost shape deliberately exercise unknown economics, never qualify sizing.
"""
from copy import deepcopy


def contract_fixture():
    return deepcopy({
        'symbol': 'XAUUSD.R', 'tradableInstrumentId': 654,
        'routes': {'INFO': 41, 'TRADE': 42},
        'contract': {
            'name': 'XAUUSD.R', 'type': 'EQUITY_CFD',
            'lotSize': 10, 'lotStep': 0.02, 'minLot': 0.02, 'maxLot': 7,
            'baseCurrency': None, 'quotingCurrency': 'USD',
            'tickSize': [{'leftRangeLimit': None, 'tickSize': 0.02}],
            'tickCost': [{'leftRangeLimit': None, 'tickCost': 0.0}],
            'tradeSessionId': None, 'tradeSessionStatusId': None,
        },
    })
