from abc import ABC, abstractmethod


class WSSDataConsumer(ABC):
    def __init__(self, name, interval, symbols) -> None:
        self.name = name
        self.interval = interval
        self.symbols = symbols

    @abstractmethod
    def need_process(self, interval, symbol):
        pass
    
    @abstractmethod
    def process(self, symbol, data, interval):
        pass

