from abc import ABC, abstractmethod


class PrivateDataConsumer(ABC):
    def __init__(self, user_id) -> None:
        self.user_id = user_id

    
    @abstractmethod
    def private_data_process(self, data):
        pass

