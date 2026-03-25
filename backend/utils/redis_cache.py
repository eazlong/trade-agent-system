import redis
import functools
import hashlib
import pickle
import logging

# 初始化 Redis 连接
redis_client = redis.StrictRedis(host='localhost', port=6379, db=0, decode_responses=False)

def redis_cache(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        # 1. 生成 Redis 的 key，基于函数名和参数
        raw_key = f"{func.__name__}:{args}:{kwargs}"
        key = hashlib.md5(raw_key.encode()).hexdigest()  # 使用MD5生成固定长度的key
        
        # 2. 检查 Redis 中是否已有值
        cached_result = redis_client.get(key)
        if cached_result is not None:
            print(f"从 Redis 获取缓存: key={key}")
            return pickle.loads(cached_result)  # 反序列化返回结果
        
        # 3. Redis 中不存在，调用原函数
        result = func(*args, **kwargs)
        
        # 4. 将结果保存到 Redis
        redis_client.set(key, pickle.dumps(result), ex=5*60*1000)  # 序列化结果并保存
        print(f"函数执行并缓存结果: key={key}")
        
        return result

    return wrapper


from collections import UserDict

class RedisDict(UserDict):
    def __init__(self, name, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = name  # Redis key
        self.redis = redis_client
        cached_result = redis_client.get(name)
        if cached_result is not None:
            self.data = pickle.loads(cached_result)
        else:
            self._sync_to_redis()
    
    def _sync_to_redis(self):
        # 使用 JSON 序列化同步到 Redis
        self.redis.set(self.name, pickle.dumps(self.data))

    def load_from_redis(self):
        # 从 Redis 读取并反序列化
        raw_data = self.redis.get(self.name)
        if raw_data:
            self.data = pickle.loads(raw_data)

    def __setitem__(self, key, value):
        super().__setitem__(key, value)
        self._sync_to_redis()  # 同步到 Redis

    def __delitem__(self, key):
        super().__delitem__(key)
        self._sync_to_redis()  # 同步到 Redis
    
    def clear(self):
        # 清除 Redis 中的数据
        self.redis.delete(self.name)
        self.data.clear()
        logging.info(f'Cleared data for key: {self.name}')