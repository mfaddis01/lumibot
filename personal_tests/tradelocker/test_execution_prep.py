import inspect
import sys
import unittest
from pathlib import Path
from personal_trading.tradelocker.tl_candidate import Identity, ReadOnlyError, ENVIRONMENT, SERVER, SYMBOL
from personal_trading.tradelocker.execution_prep import prepare_market_translation


class PreparationTests(unittest.TestCase):
    def setUp(self):
        self.identity = Identity(ENVIRONMENT, SERVER, 123, 7, 'USD', SYMBOL, 456, '17', '18')
        # Explicit SYNTHETIC values only. They are NOT XAUUSD.R contract facts.
        self.details = {'name': SYMBOL, 'quotingCurrency': 'USD', 'lotSize': '100', 'minLot': '0.01', 'maxLot': '1.00', 'lotStep': '0.01'}

    def test_offline_reconciliation_never_equates_acknowledgement_with_fill(self):
        from personal_trading.tradelocker.execution_prep import reconcile_open_observation, reconcile_flat_observation
        plan = prepare_market_translation(self.identity, self.details, quantity='0.02', side='sell')
        self.assertEqual(reconcile_open_observation(plan, '901', [], []), 'WAIT_RECONCILE_NO_REPLAY')
        history = [{'id': '901', 'positionId': '789', 'tradableInstrumentId': 456, 'status': 'Filled', 'filledQty': '0.02', 'side': 'sell'}]
        position = {'id': '789', 'tradableInstrumentId': 456, 'routeId': 18, 'qty': '0.02', 'side': 'sell'}
        self.assertEqual(reconcile_open_observation(plan, '901', history, []), 'WAIT_RECONCILE_NO_REPLAY')
        self.assertEqual(reconcile_open_observation(plan, '901', history, [position]), 'OPEN_OBSERVED')
        history[0]['filledQty'] = '0.01'
        self.assertEqual(reconcile_open_observation(plan, '901', history, [position]), 'WAIT_RECONCILE_NO_REPLAY')
        self.assertEqual(reconcile_flat_observation([], []), 'FLAT_SNAPSHOT_ONLY')
        self.assertEqual(reconcile_flat_observation([position], []), 'WAIT_RECONCILE_NO_REPLAY')
        self.assertEqual(reconcile_flat_observation([], [{'id': '902'}]), 'WAIT_RECONCILE_NO_REPLAY')

    def test_quantity_and_sdk_signature_translation_is_always_offline(self):
        from tradelocker.tradelocker_api import TLAPI
        plan = prepare_market_translation(self.identity, self.details, quantity='0.02', side='sell')
        self.assertFalse(plan.execution_enabled)
        self.assertEqual(plan.quantity_unit, 'lots_from_supplied_metadata_unqualified')
        self.assertEqual(plan.sdk_kwargs['quantity'], 0.02)
        self.assertEqual(plan.sdk_kwargs['side'], 'sell')
        self.assertEqual(plan.sdk_kwargs['type_'], 'market')
        self.assertEqual(plan.sdk_kwargs['validity'], 'IOC')
        self.assertIs(plan.sdk_kwargs['position_netting'], False)
        inspect.signature(TLAPI.create_order).bind(None, **plan.sdk_kwargs)
        for value in ('0', '-0.01', '0.001', '0.015', '1.01', 'NaN', 'Infinity', True, 0.02):
            with self.subTest(kind=type(value).__name__), self.assertRaises(ReadOnlyError):
                prepare_market_translation(self.identity, self.details, quantity=value, side='buy')
        with self.assertRaises(ReadOnlyError):
            prepare_market_translation(self.identity, {}, quantity='0.02', side='buy')
        with self.assertRaises(ReadOnlyError):
            prepare_market_translation(self.identity, self.details, quantity='0.02', side=None)

if __name__ == '__main__':
    unittest.main()
