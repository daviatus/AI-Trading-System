import asyncio
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tools.ctrader_copy_exporter.exporter import ScrollState
from tools.ctrader_copy_exporter.schedule import ExportInterval


@dataclass(frozen=True)
class SelectorConfig:
    history_container: str
    rows: str
    trade_id: str
    timestamp: str
    start_date: str
    end_date: str
    apply_button: str
    fields: Mapping[str, str]
    loading: str | None = None
    date_format: str = "%Y-%m-%d"
    timestamp_format: str | None = None

    @classmethod
    def from_file(cls, path: Path) -> "SelectorConfig":
        content = json.loads(path.read_text(encoding="utf-8"))
        required = (
            "history_container",
            "rows",
            "trade_id",
            "timestamp",
            "start_date",
            "end_date",
            "apply_button",
        )
        missing = [name for name in required if not content.get(name)]
        if missing:
            raise ValueError(f"Selector configuration missing: {', '.join(missing)}")
        return cls(
            history_container=content["history_container"],
            rows=content["rows"],
            trade_id=content["trade_id"],
            timestamp=content["timestamp"],
            start_date=content["start_date"],
            end_date=content["end_date"],
            apply_button=content["apply_button"],
            fields=content.get("fields", {}),
            loading=content.get("loading"),
            date_format=content.get("date_format", "%Y-%m-%d"),
            timestamp_format=content.get("timestamp_format"),
        )


class PlaywrightHistoryAdapter:
    """Generic adapter driven only by operator-validated, visible UI selectors."""

    def __init__(self, page: Any, selectors: SelectorConfig) -> None:
        self.page = page
        self.selectors = selectors
        self._filter: dict[str, str] = {}
        self._container = page.locator(selectors.history_container)

    async def apply_date_filter(self, interval: ExportInterval) -> dict[str, str]:
        self._filter = {
            "start": _format_ui_date(interval.ui_start, self.selectors.date_format),
            "end": _format_ui_date(interval.ui_end, self.selectors.date_format),
        }
        await self.page.locator(self.selectors.start_date).fill(self._filter["start"])
        await self.page.locator(self.selectors.end_date).fill(self._filter["end"])
        await self.page.locator(self.selectors.apply_button).click()
        if not await self.wait_settled():
            raise RuntimeError("History widget did not settle after applying its date filter")
        await self._install_refresh_observer()
        return dict(self._filter)

    async def confirm_date_filter(self, interval: ExportInterval) -> bool:
        try:
            start_value = await self.page.locator(self.selectors.start_date).input_value()
            end_value = await self.page.locator(self.selectors.end_date).input_value()
        except Exception:
            return False
        expected_start = _format_ui_date(interval.ui_start, self.selectors.date_format)
        expected_end = _format_ui_date(interval.ui_end, self.selectors.date_format)
        return (
            start_value == expected_start == self._filter.get("start")
            and end_value == expected_end == self._filter.get("end")
            and await self._container.count() == 1
        )

    async def visible_rows(self) -> list[dict[str, str]]:
        rows = self._container.locator(self.selectors.rows)
        count = await rows.count()
        result = []
        fields = {
            "trade_id": self.selectors.trade_id,
            "timestamp": self.selectors.timestamp,
            **self.selectors.fields,
        }
        for index in range(count):
            row = rows.nth(index)
            extracted: dict[str, str] = {}
            for name, selector in fields.items():
                locator = row.locator(selector)
                if await locator.count():
                    extracted[name] = (await locator.first.inner_text()).strip()
                else:
                    extracted[name] = ""
            result.append(extracted)
        return result

    async def scroll_state(self) -> ScrollState:
        state = await self._container.evaluate(
            """element => {
                const top = element.scrollTop;
                const height = element.clientHeight;
                const total = element.scrollHeight;
                const revision = Number(element.dataset.copyExportRevision || 0);
                return {top, height, total, revision};
            }"""
        )
        return ScrollState(
            top=float(state["top"]),
            height=float(state["height"]),
            total=float(state["total"]),
            revision=int(state["revision"]),
        )

    async def scroll_down(self) -> None:
        await self._container.evaluate(
            "element => element.scrollTo({top: element.scrollTop + element.clientHeight * 0.8})"
        )

    async def scroll_top(self) -> None:
        await self._container.evaluate("element => element.scrollTo({top: 0})")

    async def wait_settled(self) -> bool:
        try:
            if self.selectors.loading:
                await self.page.locator(self.selectors.loading).wait_for(
                    state="hidden", timeout=10000
                )
            await self.page.wait_for_timeout(400)
            return await self._container.count() == 1
        except Exception:
            return False

    async def _install_refresh_observer(self) -> None:
        await self._container.evaluate(
            """element => {
                if (element.__copyExportObserver) {
                    element.__copyExportObserver.disconnect();
                }
                element.dataset.copyExportRevision = "0";
                element.__copyExportObserver = new MutationObserver(() => {
                    const current = Number(element.dataset.copyExportRevision || 0);
                    element.dataset.copyExportRevision = String(current + 1);
                });
                element.__copyExportObserver.observe(element, {
                    childList: true, subtree: true, characterData: true
                });
            }"""
        )


async def launch_persistent_browser(profile: Path, headless: bool = False) -> tuple[Any, Any, Any]:
    try:
        from playwright.async_api import async_playwright
    except ImportError as error:
        raise RuntimeError(
            'Install browser support with: python -m pip install -e ".[ctrader-export]" '
            "and python -m playwright install chromium"
        ) from error
    profile.mkdir(parents=True, exist_ok=True)
    playwright = await async_playwright().start()
    try:
        context = await playwright.chromium.launch_persistent_context(
            str(profile),
            headless=headless,
            viewport={"width": 1440, "height": 1000},
        )
    except Exception:
        await playwright.stop()
        raise
    page = context.pages[0] if context.pages else await context.new_page()
    return playwright, context, page


async def run_interactive_diagnostic(url: str, profile: Path) -> dict[str, Any]:
    playwright, context, page = await launch_persistent_browser(profile)
    try:
        await page.goto(url, wait_until="domcontentloaded")
        print(
            "Browser opened using a local persistent profile. Sign in yourself if required, "
            "navigate to the authorized Copy History view, then press Enter here."
        )
        await asyncio.to_thread(input)
        await page.wait_for_timeout(500)
        snapshot = await page.evaluate(
            """() => {
                const summarize = element => ({
                    tag: element.tagName.toLowerCase(),
                    type: element.getAttribute('type'),
                    id: element.id || null,
                    name: element.getAttribute('name'),
                    ariaLabel: element.getAttribute('aria-label'),
                    placeholder: element.getAttribute('placeholder'),
                    role: element.getAttribute('role'),
                    className: typeof element.className === 'string'
                        ? element.className.slice(0, 120) : null
                });
                const inputs = Array.from(document.querySelectorAll(
                    'input,button,[role="button"],[role="grid"],[role="table"]'
                )).filter(el => el.getClientRects().length).slice(0, 100).map(summarize);
                const scrollables = Array.from(document.querySelectorAll('*'))
                    .filter(el => el.getClientRects().length &&
                        el.scrollHeight > el.clientHeight + 40 &&
                        getComputedStyle(el).overflowY !== 'visible')
                    .slice(0, 30).map(el => ({
                        ...summarize(el), clientHeight: el.clientHeight,
                        scrollHeight: el.scrollHeight
                    }));
                return {
                    pageTitle: document.title,
                    currentUrl: location.origin + location.pathname,
                    visibleControls: inputs,
                    scrollableCandidates: scrollables
                };
            }"""
        )
        return snapshot
    finally:
        await context.close()
        await playwright.stop()


def _format_ui_date(value: str, date_format: str) -> str:
    from datetime import datetime

    try:
        return datetime.strptime(value, "%Y-%m-%d").strftime(date_format)
    except ValueError as error:
        raise ValueError(f"Invalid date filter format: {date_format}") from error
