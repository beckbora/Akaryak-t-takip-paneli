import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from price_expectation import _fuel, _pump_realized, _fuel_keys_for_event, _consensus_event, _status
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

    def test_generic_discount_with_price_list_has_no_fuel(self):
        headline = ('Akaryakıt fiyatlarına 4,30 TL indirim geldi! '
                    'Güncel LPG, motorin ve benzin fiyatları ne kadar?')
        self.assertIsNone(_fuel(headline))

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

    def test_two_fuels_share_discount_forecast(self):
        self.assertEqual(
            _fuel_keys_for_event('Benzin ve motorine dev indirim bekleniyor', 'down'),
            ['gasoline', 'diesel'])
        self.assertEqual(
            _fuel_keys_for_event('Motorin ve benzine zam bekleniyor', 'up'),
            ['diesel', 'gasoline'])

    def test_generic_price_list_is_not_independent_evidence(self):
        line = 'LPG, motorin ve benzin fiyatları ne kadar? İndirim bekleniyor'
        self.assertEqual(_fuel_keys_for_event(line, 'down'), [])

    def test_forecast_wording_ongoruluyor(self):
        self.assertEqual(_status('Motorine 4 TL indirim öngörülüyor'), 'down')
        self.assertEqual(_status('Benzine 3 TL zam öngörülüyor'), 'up')

    def test_discount_majority_beats_lone_latest_increase(self):
        now = datetime.now(timezone.utc)
        def article(domain, status, minutes_ago):
            return {'domain':domain, 'source':domain, 'status':status, 'amount':4.0,
                    'published':now - timedelta(minutes=minutes_ago),
                    'url':'https://'+domain+'/article'}
        rows = [
            article('older-zam.example', 'up', 0),
            article('indirim1.example', 'down', 20),
            article('indirim2.example', 'down', 40),
            article('indirim3.example', 'down', 60),
        ]
        selected, group = _consensus_event(rows)
        self.assertEqual(selected['status'], 'down')
        self.assertEqual(selected['confirmation_count'], 3)

    def test_conflicting_single_reports_not_decided(self):
        now = datetime.now(timezone.utc)
        rows = [{'domain':'a.example','status':'up','published':now,'amount':2.0},
                {'domain':'b.example','status':'down',
                 'published':now-timedelta(minutes=30),'amount':2.0}]
        selected, _ = _consensus_event(rows)
        self.assertIsNone(selected)

    def test_lpg_push_name(self):
        self.assertEqual(message_for({'fuel_key':'lpg','status':'down'})[0], 'LPG')

if __name__ == '__main__':
    unittest.main()
