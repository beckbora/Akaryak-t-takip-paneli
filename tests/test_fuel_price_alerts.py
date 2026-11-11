import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from price_expectation import _fuel, _pump_realized, _fuel_keys_for_event, _consensus_event, _status, _amount_for_fuel, _line, scan_price_expectation, _verified_news_amount
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

    def test_real_november_discount_headlines(self):
        examples = [
            ('Benzin ve motorine indirim geleceği tarih açıklandı', ['gasoline', 'diesel']),
            ('Akaryakıtta indirim beklentisi: Benzin ve motorinde fiyatlar düşecek', ['gasoline', 'diesel']),
            ('Benzin ve motorinde farklı tarihlerde indirim yapılması bekleniyor', ['gasoline', 'diesel']),
            ('Motorine 4 TL indirim yapılması öngörülüyor', ['diesel']),
            ('Motorinin litre fiyatında 4 TL düşüş öngörülüyor', ['diesel']),
        ]
        for headline, expected in examples:
            with self.subTest(headline=headline):
                status = _status(headline)
                self.assertEqual(status, 'down')
                self.assertEqual(_fuel_keys_for_event(headline, status), expected)

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

    def test_independent_amounts_in_same_article(self):
        headline = 'Benzine 3 TL, motorine 4 TL indirim bekleniyor'
        self.assertEqual(_fuel_keys_for_event(headline, 'down'),
                         ['gasoline', 'diesel'])
        self.assertEqual(_amount_for_fuel(headline, 'gasoline'), 3.0)
        self.assertEqual(_amount_for_fuel(headline, 'diesel'), 4.0)
        self.assertIsNone(_amount_for_fuel(headline, 'lpg'))

    def test_amount_before_fuel_and_lpg(self):
        self.assertEqual(_amount_for_fuel('4 TL motorine, 3 TL benzine indirim', 'diesel'), 4.0)
        self.assertEqual(_amount_for_fuel('4 TL motorine, 3 TL benzine indirim', 'gasoline'), 3.0)
        self.assertEqual(_amount_for_fuel('Otogaza 85 kuruş zam bekleniyor', 'lpg'), 0.85)

    def test_explicitly_shared_amount(self):
        headline = 'Benzin ve motorine 4 TL indirim bekleniyor'
        self.assertEqual(_amount_for_fuel(headline, 'gasoline'), 4.0)
        self.assertEqual(_amount_for_fuel(headline, 'diesel'), 4.0)

    def test_no_amount_leak_between_fuels(self):
        headline = 'Benzin ve motorine indirim bekleniyor; motorine 4 TL indirim uygulanacak'
        self.assertIsNone(_amount_for_fuel(headline, 'gasoline'))
        self.assertEqual(_amount_for_fuel(headline, 'diesel'), 4.0)
        self.assertIsNone(_amount_for_fuel('Benzin 55 TL, motorine 4 TL indirim', 'gasoline'))

    def test_line_renders_only_known_amount(self):
        self.assertIn('3,00 TL İNDİRİM BEKLENİYOR',
                      _line('BENZİN', 'down', 3.0))
        self.assertIn('4,00 TL ZAM BEKLENİYOR',
                      _line('MOTORİN', 'up', 4.0))
        self.assertIn('0,85 TL İNDİRİM BEKLENİYOR',
                      _line('LPG', 'down', 0.85))
        self.assertNotIn('TL', _line('LPG', 'none', None))

    def test_scan_keeps_amounts_separate_for_each_fuel(self):
        row = {
            'title': 'Benzine 3 TL, motorine 4 TL indirim bekleniyor',
            'description': '', 'domain': 'ekonomim.com',
            'url': 'https://www.ekonomim.com/ekonomi/ornek-haber',
            'published': datetime.now(timezone.utc), 'source': 'Ekonomim',
        }
        with patch('price_expectation._rss', return_value=[row]), \
             patch('price_expectation._google_news_rss', return_value=[]), \
             patch('price_expectation._article_text', return_value=''):
            data = scan_price_expectation(saved={'items': []},
                                          price_data={'changes': []})
        items = {item['fuel_key']: item for item in data['items']}
        self.assertEqual(items['gasoline']['amount'], 3.0)
        self.assertEqual(items['diesel']['amount'], 4.0)
        self.assertIn('3,00 TL', items['gasoline']['line'])
        self.assertIn('4,00 TL', items['diesel']['line'])
        self.assertEqual(items['lpg']['status'], 'none')


    def test_dated_verified_amount_is_fuel_specific(self):
        checked_at = datetime(2026, 11, 11, 10, 0, tzinfo=timezone.utc)
        source = [{'domain': 'dunya.com'}]
        motorin = _verified_news_amount('diesel', 'down', checked_at, source)
        benzin = _verified_news_amount('gasoline', 'down', checked_at, source)
        self.assertEqual(motorin['amount'], 4.0)
        self.assertEqual(benzin['amount'], 3.0)
        self.assertEqual(motorin['effective_date'], '2026-11-14')
        self.assertEqual(benzin['effective_date'], '2026-11-12')
        self.assertIsNone(_verified_news_amount('lpg', 'down', checked_at, source))
        self.assertIsNone(_verified_news_amount('diesel', 'up', checked_at, source))
        self.assertIsNone(_verified_news_amount('diesel', 'down', checked_at, [{'domain':'sozcu.com.tr'}]))

    def test_verified_amount_automatically_expires(self):
        after = datetime(2026, 11, 14, 0, 5, tzinfo=timezone.utc)
        self.assertIsNone(_verified_news_amount('diesel', 'down', after, [{'domain':'dunya.com'}]))
        self.assertIsNone(_verified_news_amount('gasoline', 'down', after, [{'domain':'dunya.com'}]))

    def test_headline_without_number_uses_verified_amount_only_with_consensus(self):
        now = datetime.now(timezone.utc)
        first = {'title': 'Benzin ve motorine indirim bekleniyor',
                 'description': '', 'domain': 'dunya.com',
                 'url': 'https://news.google.com/rss/articles/example1',
                 'published': now, 'source': 'Dünya'}
        second = dict(first, domain='sozcu.com.tr',
                      url='https://news.google.com/rss/articles/example2',
                      source='Sözcü')
        evidence = {'amount': 4.0, 'approximate': True,
                    'source': 'Dünya', 'url':'https://www.dunya.com/test',
                    'effective_date': '2026-11-14'}
        with patch('price_expectation._rss', return_value=[first, second]), \
             patch('price_expectation._google_news_rss', return_value=[]), \
             patch('price_expectation._article_text', return_value=''), \
             patch('price_expectation._verified_news_amount', return_value=evidence):
            data = scan_price_expectation(saved={'items': []}, price_data={'changes': []})
        by_fuel = {row['fuel_key']: row for row in data['items']}
        self.assertIn('≈ 4,00 TL İNDİRİM BEKLENİYOR', by_fuel['diesel']['line'])
        self.assertTrue(by_fuel['diesel']['amount_approximate'])
        self.assertEqual(by_fuel['diesel']['amount_source'], 'Dünya')
        self.assertEqual(by_fuel['lpg']['status'], 'none')

    def test_lpg_push_name(self):
        self.assertEqual(message_for({'fuel_key':'lpg','status':'down'})[0], 'LPG')

if __name__ == '__main__':
    unittest.main()
