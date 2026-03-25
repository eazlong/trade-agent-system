# utils/thread_pool.py
from concurrent.futures import ThreadPoolExecutor

# 创建一个全局的线程池实例
thread_pool = ThreadPoolExecutor(max_workers=128)

def submit_task(func, *args, **kwargs):
    """提交任务到线程池"""
    return thread_pool.submit(func, *args, **kwargs)