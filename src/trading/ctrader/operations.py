from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from trading.models import SystemEvent

CTRADER_WORKER_EVENT = "ctrader_worker_run"


class CTraderOperationsRepository:
    """Append-only worker reports and retry state stored in existing system events."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def recent_runs(self, limit: int = 100) -> list[dict[str, Any]]:
        with Session(self.engine) as session:
            events = session.scalars(
                select(SystemEvent)
                .where(SystemEvent.event_type == CTRADER_WORKER_EVENT)
                .order_by(SystemEvent.occurred_at.desc(), SystemEvent.id.desc())
                .limit(limit)
            )
            return [event.payload for event in events]

    def latest_run(self) -> dict[str, Any] | None:
        runs = self.recent_runs(limit=1)
        return runs[0] if runs else None

    def latest_successful_run(self) -> dict[str, Any] | None:
        with Session(self.engine) as session:
            return session.scalar(
                select(SystemEvent.payload)
                .where(
                    SystemEvent.event_type == CTRADER_WORKER_EVENT,
                    or_(
                        SystemEvent.payload["report"]["execution_status"].astext == "succeeded",
                        and_(
                            SystemEvent.payload["report"]["execution_status"].astext.is_(None),
                            SystemEvent.payload["report"]["synchronization_status"].astext
                            == "complete",
                        ),
                    ),
                )
                .order_by(SystemEvent.occurred_at.desc(), SystemEvent.id.desc())
                .limit(1)
            )

    def load_retry_state(self) -> dict[str, dict[str, Any]]:
        latest = self.latest_run()
        if latest is None or not isinstance(latest.get("retry_state"), dict):
            return {}
        return latest["retry_state"]

    def record_run(
        self,
        report: dict[str, Any],
        retry_state: dict[str, dict[str, Any]],
    ) -> None:
        event = SystemEvent(
            event_type=CTRADER_WORKER_EVENT,
            occurred_at=datetime.now(UTC),
            payload={"report": report, "retry_state": retry_state},
        )
        with Session(self.engine) as session, session.begin():
            session.add(event)
