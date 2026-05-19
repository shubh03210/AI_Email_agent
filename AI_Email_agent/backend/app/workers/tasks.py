from app.workers.celery_app import celery_app


@celery_app.task(name="process_email_thread")
def process_email_thread(thread_id: str, prospect_id: int):
    # TODO: invoke agent_graph for the given thread
    pass


@celery_app.task(name="poll_inbox")
def poll_inbox():
    # TODO: fetch new emails via gmail_service and enqueue process_email_thread tasks
    pass
