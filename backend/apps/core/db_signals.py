"""Celery signal handlers for automatic Django DB connection cleanup.

Ensures:
1. Forked worker processes don't reuse parent connections
2. Each task starts with a clean connection
3. Connections are closed after each task (not recycled across tasks)
"""
from celery.signals import task_prerun, task_postrun, worker_process_init
from django.db import close_old_connections, connection


@worker_process_init.connect
def init_worker(**kwargs):
    """Clean up connections after fork — prevents child reusing parent's connections."""
    close_old_connections()


@task_prerun.connect
def pre_task_cleanup(**kwargs):
    """Clean stale connections before each task executes."""
    close_old_connections()


@task_postrun.connect
def post_task_cleanup(**kwargs):
    """Close connection after task — Celery tasks don't share connections."""
    connection.close()
