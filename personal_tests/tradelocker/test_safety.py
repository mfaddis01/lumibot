"""All policy numbers are SYNTHETIC, not broker economics or owner choices."""
from dataclasses import replace
from decimal import Decimal as D
from pathlib import Path
import sys
import unittest
from personal_trading.tradelocker.tl_candidate import Identity, ENVIRONMENT, SERVER, SYMBOL, ReadOnlyError


def policy_fixture():
    from personal_trading.tradelocker.safety import RiskPolicy, Qualification, RateLimits
    identity = Identity(ENVIRONMENT, SERVER, 123, 7, 'USD', SYMBOL, 456, '17', '18')
    qualification = Qualification(identity, D('.01'), D('1'), D('.01'), D('.01'),
        D('10'), D('3000'), D('20'), D('2'), True, True, True, True, True,
        'SYNTHETIC economics/permissions/protection mapping', True,
        True, True, True, 'synthetic_sdk_quantity_equals_fixture_lots', int(__import__('time').time())+300,
        RateLimits(200,D('60'),30,30,'SYNTHETIC fixture aggregate budget',True))
    policy = RiskPolicy(identity, 'buy', D('.02'), D('2490'), D('2520'),
        D('100'), D('10'), D('1'), D('1'), D('5'), D('5'), D('60'))
    return policy, qualification


class SafetyTests(unittest.TestCase):
    def test_qualification_requires_explicit_cancel_protection_session_and_unit_gates(self):
        from personal_trading.tradelocker.safety import Qualification
        for field in ('cancel_allowed','protective_orders_allowed','session_open',
                      'quantity_unit','valid_until'):
            self.assertIn(field,Qualification.__dataclass_fields__)

    def test_boolean_claims_cannot_authorize_unqualified_actual_execution(self):
        from personal_trading.tradelocker.safety import validate_policy
        p,q=policy_fixture()
        # All numeric values and boolean assertions supplied is still NOT
        # broker/account-qualified REST quantity, economics or permission proof.
        with self.assertRaises(ReadOnlyError):
            validate_policy(p,replace(q,synthetic=False,evidence='caller says qualified'))

    def test_explicit_policy_validates_without_gold_unit_assumptions(self):
        from personal_trading.tradelocker.safety import validate_policy, validate_market
        policy, q = policy_fixture()
        validate_policy(policy, q)
        validate_market(policy, q, bid=D('2500'), ask=D('2500.5'),
                        quote_age=D('0'), snapshot_age=D('0'), free_margin=D('1000'))
        for field, value in [('quantity', D('.015')), ('max_loss_usd', D('0')),
                             ('max_exposure_usd', D('0')), ('stop_loss', D('2490.005')),
                             ('side', None), ('quantity', .02)]:
            with self.subTest(field=field), self.assertRaises(ReadOnlyError):
                validate_policy(replace(policy, **{field: value}), q)
        for field, value in [('usd_per_price_per_lot', None), ('usd_per_price_per_lot', D('0')),
                             ('open_allowed', None), ('close_allowed', False),
                             ('demo_eligible', 1), ('protection_mapping_qualified', None),
                             ('evidence', '')]:
            with self.subTest(field=field), self.assertRaises(ReadOnlyError):
                validate_policy(policy, replace(q, **{field: value}))
        with self.assertRaises(ReadOnlyError):
            validate_policy(replace(policy, binding=replace(policy.binding, symbol='XAUUSD')), q)
        for changes in [dict(quote_age=D('6')), dict(snapshot_age=D('6')),
                        dict(bid=D('2500'), ask=D('2502')), dict(free_margin=D('0'))]:
            args = dict(bid=D('2500'), ask=D('2500.5'), quote_age=D('0'),
                        snapshot_age=D('0'), free_margin=D('1000'))
            args.update(changes)
            with self.assertRaises(ReadOnlyError):
                validate_market(policy, q, **args)


if __name__ == '__main__':
    unittest.main()
