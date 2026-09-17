from price_expectation import write_price_expectation


if __name__ == '__main__':
    data = write_price_expectation()
    print('Expectation snapshot updated:', data.get('checked_at'))
