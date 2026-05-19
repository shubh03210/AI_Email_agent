"""
LLM Service
────────────
Provider abstraction layer for LLM calls.

Design:
  - BaseLLMProvider: abstract interface (swap Groq → OpenAI → Claude with zero agent changes)
  - GroqProvider: Groq implementation via langchain-groq (Llama 3 / Mixtral)
  - Structured output schemas: typed Pydantic v2 models for every agent decision
  - get_llm_service(): singleton accessor used throughout the codebase
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Any, Optional, Type, TypeVar

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_groq import ChatGroq
from pydantic import BaseModel, Field
from tenacity import retry, stop_after_attempt, wait_exponential

from app.core.config import settings
from app.core.logging import logger

T = TypeVar("T", bound=BaseModel)


# ── Structured Output Schemas ─────────────────────────────────────────────────

class IntentClassification(BaseModel):
    """Output of the classify_intent node."""
    intent: str = Field(
        description=(
            "Detected intent of the prospect's latest email. "
            "Must be one of: interested, curious, negotiating, declined, "
            "ambiguous, reschedule, unavailable."
        )
    )
    confidence: float = Field(
        ge=0.0, le=1.0,
        description="Confidence score between 0.0 and 1.0.",
    )
    reasoning: str = Field(
        description="One-sentence explanation of why this intent was chosen."
    )


class NegotiationDecision(BaseModel):
    """Output of the negotiation node."""
    action: str = Field(
        description=(
            "Next negotiation action. "
            "Must be one of: accept, counteroffer, walkaway, hold."
        )
    )
    proposed_amount: Optional[float] = Field(
        default=None,
        description="Counter-offer amount in USD. Required if action is 'counteroffer'.",
    )
    reasoning: str = Field(
        description="Brief explanation of the negotiation decision."
    )
    walkaway_reason: Optional[str] = Field(
        default=None,
        description="Reason for walking away. Required if action is 'walkaway'.",
    )


class SchedulingDecision(BaseModel):
    """Output of the scheduling node."""
    selected_slot_index: int = Field(
        description="0-based index of the chosen slot from the provided list.",
    )
    message_to_prospect: str = Field(
        description="Natural language message proposing the selected time slot.",
    )


class ReschedulingDecision(BaseModel):
    """Output of the rescheduling node."""
    acknowledged: bool = Field(
        description="Whether the agent acknowledged the prospect's reschedule request.",
    )
    apology_message: str = Field(
        description="Short empathetic acknowledgement of the reschedule request.",
    )
    new_slot_index: int = Field(
        description="0-based index of the new proposed slot from the provided list.",
    )


class EmailReply(BaseModel):
    """Final polished email reply ready to send."""
    subject: str = Field(description="Email subject line.")
    body: str = Field(description="Full email body — plain text, professional tone.")
    tone_used: str = Field(description="Tone applied: professional, friendly, formal, or casual.")


class EmailDraft(BaseModel):
    """Initial outreach email draft."""
    subject: str = Field(description="Compelling email subject line.")
    body: str = Field(description="Full cold outreach email body.")


# ── Provider Abstraction ──────────────────────────────────────────────────────

class BaseLLMProvider(ABC):
    """Abstract base for all LLM providers. Add OpenAI/Claude by subclassing this."""

    @abstractmethod
    def get_model(self) -> BaseChatModel:
        """Return the underlying LangChain chat model instance."""
        ...

    @abstractmethod
    def generate(self, system_prompt: str, user_message: str) -> str:
        """
        Run a plain text generation.

        Args:
            system_prompt: Instructions / persona for the model.
            user_message:  The actual user/task input.

        Returns:
            The model's text response as a string.
        """
        ...

    @abstractmethod
    def generate_structured(
        self,
        system_prompt: str,
        user_message: str,
        output_schema: Type[T],
    ) -> T:
        """
        Run a generation and parse output into a typed Pydantic model.

        Args:
            system_prompt:  Instructions for the model.
            user_message:   The task input.
            output_schema:  A Pydantic BaseModel class to parse the response into.

        Returns:
            An instance of output_schema populated with the model's response.
        """
        ...


# ── Groq Implementation ───────────────────────────────────────────────────────

class GroqProvider(BaseLLMProvider):
    """
    Groq LLM provider using langchain-groq.
    Default model: llama3-70b-8192.
    Supports structured outputs via LangChain's .with_structured_output().
    """

    def __init__(self):
        if not settings.GROQ_API_KEY:
            raise ValueError(
                "GROQ_API_KEY is not set. Add it to your .env file. "
                "Get a free key at https://console.groq.com/"
            )
        self._model = ChatGroq(
            api_key=settings.GROQ_API_KEY,
            model=settings.GROQ_MODEL,
            temperature=settings.LLM_TEMPERATURE,
            max_tokens=settings.LLM_MAX_TOKENS,
        )
        logger.info(f"GroqProvider initialised | model={settings.GROQ_MODEL}")

    def get_model(self) -> BaseChatModel:
        return self._model

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=8),
        reraise=True,
    )
    def generate(self, system_prompt: str, user_message: str) -> str:
        """Plain text generation with automatic retry on transient errors."""
        t0 = time.perf_counter()
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_message),
        ]
        response = self._model.invoke(messages)
        latency_ms = int((time.perf_counter() - t0) * 1000)
        content = str(response.content)
        logger.debug(
            f"LLM generate | model={settings.GROQ_MODEL} "
            f"latency={latency_ms}ms | chars={len(content)}"
        )
        return content

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=8),
        reraise=True,
    )
    def generate_structured(
        self,
        system_prompt: str,
        user_message: str,
        output_schema: Type[T],
    ) -> T:
        """
        Structured generation — returns a validated Pydantic model instance.
        Uses LangChain's .with_structured_output() for reliable JSON parsing.
        """
        t0 = time.perf_counter()
        structured_model = self._model.with_structured_output(output_schema)
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_message),
        ]
        result = structured_model.invoke(messages)
        latency_ms = int((time.perf_counter() - t0) * 1000)
        logger.debug(
            f"LLM structured | schema={output_schema.__name__} "
            f"model={settings.GROQ_MODEL} latency={latency_ms}ms"
        )
        return result


# ── Singleton Accessor ────────────────────────────────────────────────────────

_llm_instance: Optional[BaseLLMProvider] = None


def get_llm_service() -> BaseLLMProvider:
    """
    Returns the singleton LLM provider.
    Swap provider here when migrating from Groq → OpenAI → Claude.
    """
    global _llm_instance
    if _llm_instance is None:
        _llm_instance = GroqProvider()
    return _llm_instance


# ── Convenience Wrappers ──────────────────────────────────────────────────────

def classify_intent(conversation_history: str) -> IntentClassification:
    """Classify the intent of the latest email in a conversation."""
    llm = get_llm_service()
    return llm.generate_structured(
        system_prompt=(
            "You are an expert email analyst for a recruitment/sales team. "
            "Your job is to classify the intent of the prospect's latest reply "
            "in an ongoing email conversation.\n\n"
            "Possible intents:\n"
            "- interested: prospect is interested in moving forward\n"
            "- curious: prospect wants more information\n"
            "- negotiating: prospect is discussing budget, terms, or conditions\n"
            "- declined: prospect has clearly said no\n"
            "- ambiguous: intent is unclear\n"
            "- reschedule: prospect wants to change an existing meeting time\n"
            "- unavailable: prospect says they are unavailable at the proposed time\n\n"
            "Be precise. Read the full thread before deciding."
        ),
        user_message=f"Conversation thread:\n\n{conversation_history}",
        output_schema=IntentClassification,
    )


def decide_negotiation(
    conversation_history: str,
    current_offer: float,
    max_budget: float,
    negotiation_stage: str,
) -> NegotiationDecision:
    """Decide the next negotiation move given offer and budget constraints."""
    llm = get_llm_service()
    return llm.generate_structured(
        system_prompt=(
            "You are a professional recruiter negotiating a contract rate with a candidate. "
            "Your goal is to reach an agreement within budget without revealing the ceiling.\n\n"
            "Rules:\n"
            "1. NEVER exceed the maximum budget.\n"
            "2. Counter-offer intelligently — split the difference or anchor lower first.\n"
            "3. Walk away politely if the prospect's demand exceeds max budget after 2 counters.\n"
            "4. If the offer is acceptable, accept it.\n"
            "5. Keep tone professional and respectful throughout.\n"
        ),
        user_message=(
            f"Conversation:\n{conversation_history}\n\n"
            f"Current prospect offer: ${current_offer}\n"
            f"Maximum budget: ${max_budget}\n"
            f"Current negotiation stage: {negotiation_stage}"
        ),
        output_schema=NegotiationDecision,
    )


def generate_reply(
    conversation_history: str,
    instruction: str,
    tone: str = "professional",
    agent_name: str = "Alex",
) -> EmailReply:
    """Generate a final polished email reply."""
    llm = get_llm_service()
    return llm.generate_structured(
        system_prompt=(
            f"You are {agent_name}, a professional recruiter writing on behalf of your company. "
            f"Write emails in a {tone} tone. "
            "Keep replies concise, clear, and human — never robotic. "
            "Do NOT use generic filler phrases like 'I hope this email finds you well'. "
            "Always end with a clear call to action."
        ),
        user_message=(
            f"Conversation history:\n{conversation_history}\n\n"
            f"Your task: {instruction}"
        ),
        output_schema=EmailReply,
    )


def generate_outreach_email(
    prospect_name: str,
    gig_description: str,
    tone: str = "professional",
    agent_name: str = "Alex",
) -> EmailDraft:
    """Generate the first cold outreach email for a prospect."""
    llm = get_llm_service()
    return llm.generate_structured(
        system_prompt=(
            f"You are {agent_name}, a professional recruiter reaching out to potential candidates. "
            f"Write a compelling cold outreach email in a {tone} tone. "
            "The email must be:\n"
            "- Short (under 150 words)\n"
            "- Personalised to the recipient's name\n"
            "- Clear about the opportunity\n"
            "- Ending with a soft call to action (e.g. 'Would you be open to a quick chat?')\n"
            "Do NOT use generic openers or buzzwords."
        ),
        user_message=(
            f"Prospect name: {prospect_name}\n"
            f"Gig/Role description: {gig_description}"
        ),
        output_schema=EmailDraft,
    )
