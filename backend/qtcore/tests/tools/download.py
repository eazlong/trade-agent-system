import requests,argparse,zipfile,os, datetime,time
from dateutil.relativedelta import relativedelta

def make_url(token, inteval, datestr, daily=None):
    filename = f'{token}-{inteval}-{datestr}.zip'
    duration = 'daily' if daily else 'monthly'
    print(f'https://data.binance.vision/data/futures/um/{duration}/klines/{token}/{inteval}/{filename}')
    return filename, f'https://data.binance.vision/data/futures/um/{duration}/klines/{token}/{inteval}/{filename}'
    # https://data.binance.vision/data/futures/um/daily/klines/0GUSDT/15m/0GUSDT-15m-2025-09-27.zip
    #https://data.binance.vision/data/spot/monthly/klines/BTCBUSD/15m/BTCBUSD-15m-2022-04.zip
    #https://data.binance.vision/?prefix=data/spot/monthly/klines/BTCBUSD/15m/BTCBUSD-15m-2021-01.zip

# 根据开始日期、结束日期返回这段时间里所有天的集合
def getDatesByTimes(sDateStr, eDateStr):
    list = []
    datestart = datetime.datetime.strptime(sDateStr, '%Y-%m-%d')
    dateend = datetime.datetime.strptime(eDateStr, '%Y-%m-%d')
    list.append(datestart.strftime('%Y-%m-%d'))
    while datestart < dateend:
        datestart += datetime.timedelta(days=1)
        list.append(datestart.strftime('%Y-%m-%d'))
    return list

def getMonthsByTimes(sDateStr, eDateStr):
    list = []
    datestart = datetime.datetime.strptime(sDateStr, '%Y-%m')
    dateend = datetime.datetime.strptime(eDateStr, '%Y-%m')
    list.append(datestart.strftime('%Y-%m'))
    while datestart < dateend:
        datestart += relativedelta(months=1)
        list.append(datestart.strftime('%Y-%m'))
    return list

def download(t, i, s, e):
    print(s.split('-'))
    daily = True if len(s.split('-'))==3 else False
   
    if daily:
        datalist = getDatesByTimes(s, e)
        folder = "./qtcore/tests/data/daily/"
    else:
        datalist = getMonthsByTimes(s, e)
        folder = "./qtcore/tests/data/monthly/"

    for d in datalist:
        f = None
        try:
            f, url = make_url(t, i, d, daily)
            r = requests.get(url)
            with open(f, "wb") as code:
                code.write(r.content)
            print(f)
            z = zipfile.ZipFile(f'./{f}', 'r')
            z.extractall(folder)
            time.sleep(1)
        except Exception as e:
            print(e)
        finally:
            os.remove(f'./{f}')


if __name__ == "__main__":
    aps = argparse.ArgumentParser()
    aps.add_argument("--token", "-t", help='<Required> Token', required=True)
    aps.add_argument("--inteval", "-i", help='<Required> inteval', required=True)
    aps.add_argument("--start", "-s", default='2023-01-01')
    aps.add_argument("--end", "-e", default='2023-05-01')
    args = aps.parse_args()

    download(args.token, args.inteval, args.start, args.end)