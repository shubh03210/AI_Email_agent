"""Quick DB state check after E2E validation."""
import asyncio, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
import app.db.init_db  # noqa
from app.db.session import AsyncSessionLocal
from sqlalchemy import select, text


async def check():
    async with AsyncSessionLocal() as db:

        # TEST 1 - Prospect 323 threads
        r = await db.execute(text(
            "SELECT id, gmail_thread_id, status, follow_up_count FROM email_threads "
            "WHERE prospect_id=323 ORDER BY id DESC LIMIT 5"
        ))
        threads = r.fetchall()
        print("\n=== TEST 1 - Prospect 323 (John Doe) threads ===")
        for t in threads:
            print(f"  thread_id={t[0]} gmail_thread_id={t[1][:40]} status={t[2]} follow_up={t[3]}")

        # Messages for prospect 323 threads
        if threads:
            tid = threads[0][0]
            r = await db.execute(text(
                f"SELECT sender, intent, SUBSTRING(body, 1, 80) FROM email_messages "
                f"WHERE thread_id={tid} ORDER BY id"
            ))
            for m in r.fetchall():
                print(f"  msg: sender={m[0]} intent={m[1]} body={repr(m[2])}")

        # TEST 3 - Negotiation thread 1206
        print("\n=== TEST 3 - Thread 1206 (Negotiation) ===")
        r = await db.execute(text(
            "SELECT sender, intent, SUBSTRING(body,1,100) FROM email_messages "
            "WHERE thread_id=1206 ORDER BY id"
        ))
        for m in r.fetchall():
            print(f"  msg: sender={m[0]} intent={m[1]}")
            print(f"    body: {repr(m[2])}")

        r = await db.execute(text(
            "SELECT node_name, status, latency_ms FROM agent_runs WHERE thread_id=1206 ORDER BY id"
        ))
        print("  Agent runs:")
        for run in r.fetchall():
            print(f"    node={run[0]} status={run[1]} latency={run[2]}ms")

        r = await db.execute(text(
            "SELECT status, max_budget, current_offer, counter_round FROM negotiations WHERE thread_id=1206"
        ))
        neg = r.fetchone()
        if neg:
            print(f"  Negotiation: status={neg[0]} max_budget={neg[1]} current_offer={neg[2]} counter_round={neg[3]}")

        # TEST 4 - Walkaway thread 1207
        print("\n=== TEST 4 - Thread 1207 (Walkaway) ===")
        r = await db.execute(text("SELECT id, status FROM email_threads WHERE id=1207"))
        th = r.fetchone()
        print(f"  Thread status: {th[1] if th else 'NOT FOUND'}")

        r = await db.execute(text(
            "SELECT sender, intent, SUBSTRING(body,1,100) FROM email_messages "
            "WHERE thread_id=1207 ORDER BY id"
        ))
        for m in r.fetchall():
            print(f"  msg: sender={m[0]} intent={m[1]} body={repr(m[2])}")

        r = await db.execute(text(
            "SELECT node_name, status FROM agent_runs WHERE thread_id=1207 ORDER BY id"
        ))
        print("  Agent runs:")
        for run in r.fetchall():
            print(f"    node={run[0]} status={run[1]}")

        # TEST 5 - Scheduling thread 1208
        print("\n=== TEST 5 - Thread 1208 (Scheduling) ===")
        r = await db.execute(text(
            "SELECT id, thread_id, status, google_event_id, scheduled_at, reschedule_count "
            "FROM meetings WHERE thread_id=1208"
        ))
        m = r.fetchone()
        if m:
            print(f"  Meeting: id={m[0]} status={m[2]} event_id={m[3]} at={m[4]} reschedule_count={m[5]}")

        r = await db.execute(text(
            "SELECT sender, intent, SUBSTRING(body,1,100) FROM email_messages "
            "WHERE thread_id=1208 ORDER BY id"
        ))
        print("  Messages:")
        for msg in r.fetchall():
            print(f"    sender={msg[0]} intent={msg[1]} body={repr(msg[2])}")

        r = await db.execute(text(
            "SELECT node_name, status, latency_ms FROM agent_runs WHERE thread_id=1208 ORDER BY id"
        ))
        print("  Agent runs:")
        for run in r.fetchall():
            print(f"    node={run[0]} status={run[1]} latency={run[2]}ms")

        # TEST 6 - Reschedule thread 1209
        print("\n=== TEST 6 - Thread 1209 (Reschedule Loop) ===")
        r = await db.execute(text(
            "SELECT id, thread_id, status, google_event_id, reschedule_count, needs_human_review "
            "FROM meetings WHERE thread_id=1209"
        ))
        m = r.fetchone()
        if m:
            print(f"  Meeting: id={m[0]} status={m[2]} event_id={m[3]}")
            print(f"  reschedule_count={m[4]} needs_human_review={m[5]}")

        r = await db.execute(text(
            "SELECT sender, intent, SUBSTRING(body,1,80) FROM email_messages "
            "WHERE thread_id=1209 ORDER BY id"
        ))
        print("  Messages:")
        for msg in r.fetchall():
            print(f"    sender={msg[0]} intent={msg[1]} body={repr(msg[2])}")

        r = await db.execute(text(
            "SELECT node_name, status, latency_ms FROM agent_runs WHERE thread_id=1209 ORDER BY id"
        ))
        print("  Agent runs:")
        for run in r.fetchall():
            print(f"    node={run[0]} status={run[1]} latency={run[2]}ms")


asyncio.run(check())
