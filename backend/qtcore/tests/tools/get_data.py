import numpy as np
import pandas as pd

def get_data(symbol='ETHUSDT', interval='15m', start='2025-09', path="./qtcore/tests/data/daily"):
    import glob
    all_files = sorted(glob.glob(f"{path}/{symbol}-{interval}-{start}-*.csv"))

    dataframe_list = (pd.read_csv(file,
                                # names=['timestamp', 'Open', 'High', 'Low', 'Close', 'Volume', 'close_time', 'quote_asset_volume', 'number', 'taker', 'quo', 'i'],
                                names=['open_time', 'open', 'high', 'low', 'close', 'volume', 'close_time', 'quote_asset_volume', 'number', 'taker_buy_volume', 'taker_buy_quote_volume', 'ignore'],
                                header=0,
                                parse_dates=True,
                                index_col=None) for file in all_files)
    
    
    dataframe = pd.concat(dataframe_list)  
    dataframe['timestamp'] = pd.to_datetime(dataframe['open_time'], unit='ms')
    dataframe.set_index('timestamp', inplace=True)
    # print(dataframe.columns)  # You can remove this print statement if not needed
    return dataframe