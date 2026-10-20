import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from price_expectation import _fuel, _pump_realized
from price_expectation_state import enrich_with_pump_realization
from send_push import message_for

NOW = datetime.now(timezone.utc).isoformat()

def change(fuel, delta):
    return {'fuel': fuel, 'delta': delta, 'detected_at': NOW,
            'old_price': 50.0, 'new_price': 50.0 + delta,
            'source': 'Petrol Ofisi', 'source_url': 'https://www.petrolofisi.com.tr/'}

def items():
    return [{'fuel_key': k, 'fuel': name, 'status': 'none'}
            for k, name in [('diesel', 'MOTORİN'), ('gasoline', 'BENZİN'), ('lpg', 'LPG')]]

class FuelPriceAlertTests(unittest.TestCase):
    def test_three_independent_fuels(self):
        for sentence, expected in [('Benzine indirim geldi', 'gasoline'),
                                   ('Motorine indirim geldi', 'diesel'),
                                   ('Otogaza zam geldi', 'lpg')]:
            self.assertEqual(_fuel(sentence), expected)

    def test_mixed_headline_with_lpg_does_not_crash(self):
        self.assertEqual(_fuel('Benzin ve LPG fiyatları: otogaza zam geldi'), 'lpg')
        self.assertEqual(_fuel('Motorin ve LPG fiyatları: motorine indirim geldi'), 'diesel')

    def test_benzin_down_motorin_unchanged(self):
        result = enrich_with_pump_realization({'items': items()},
                    {'changes': [change('Benzin 95', -1.25)]})
        by_key = {x['fuel_key']: x for x in result['items']}
        self.assertEqual(by_key['gasoline']['status'], 'realized_down')
        self.assertTrue(by_key['gasoline']['pump_verified'])
        self.assertEqual(by_key['diesel']['status'], 'none')
        self.assertEqual(by_key['lpg']['status'], 'none')

    def test_lpg_separate(self):
        result = enrich_with_pump_realization({'items': items()},
                    {'changes': [change('LPG/Otogaz', 0.80)]})
        self.assertEqual(result['items'][2]['status'], 'realized_up')
        self.assertEqual(result['items'][0]['status'], 'none')

    def test_news_only_does_not_become_realized(self):
        data = {'items': [{'fuel_key':'diesel','fuel':'MOTORİN',
                 'status':'realized_down','label':'Gerçekleşen fiyat hareketi'}]}
        result = enrich_with_pump_realization(data, {'changes': []})
        self.assertEqual(result['items'][0]['status'], 'down')
        self.assertIn('BEKLENİYOR', result['items'][0]['line'])

    def test_change_must_match_fuel(self):
        changes = {'changes':[change('Benzin 95', -1.0)]}
        self.assertIsNone(_pump_realized('diesel', 'down', None, changes))

    def test_small_or_old_change_is_not_realization(self):
        old = change('Motorin', -1.0)
        old['detected_at'] = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        self.assertIsNone(_pump_realized('diesel', 'down', None,
                          {'changes': [old, change('Motorin', -0.1)]}))

    def test_conflicting_pump_prices_do_not_confirm(self):
        result = enrich_with_pump_realization({'items': items()},
                {'changes':[change('Motorin', 1.0), change('Motorin', -1.0)]})
        self.assertEqual(result['items'][0]['status'], 'none')

    def test_lpg_push_name(self):
        self.assertEqual(message_for({'fuel_key':'lpg','status':'down'})[0], 'LPG')

if __name__ == '__main__':
    unittest.main()
