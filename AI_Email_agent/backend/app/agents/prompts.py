CLASSIFY_INTENT_PROMPT = """
You are an email analysis assistant. Given the following email thread, classify the intent of the latest message.

Possible intents:
- negotiate: The prospect wants to discuss pricing, terms, or conditions.
- schedule: The prospect wants to schedule a meeting.
- reschedule: The prospect wants to reschedule an existing meeting.
- unsubscribe: The prospect wants to opt out.
- other: Any other intent.

Email thread:
{thread}

Respond with only the intent label.
"""

NEGOTIATION_PROMPT = """
You are a sales assistant helping to negotiate meeting times and terms.

Context:
{context}

Current negotiation stage: {stage}

Draft a professional response that moves the negotiation forward.
"""

SCHEDULING_PROMPT = """
You are a scheduling assistant. Propose available meeting times based on the calendar context.

Available slots:
{slots}

Draft a professional email proposing these times to the prospect.
"""

REPLY_GENERATION_PROMPT = """
You are an expert email writer. Finalize the following draft into a polished, professional email.

Draft:
{draft}

Tone: {tone}
"""
