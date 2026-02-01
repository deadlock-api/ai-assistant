# Deadlock AI Assistant - Wiki Tools Unit Tests

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from packages.api.models import ChatToolEndEvent, ChatToolStartEvent
from packages.tools.wiki import (
    WikiConnectionError,
    WikiGetPageTool,
    WikiSearchTool,
    get_wiki_site,
)


class MockPage:
    """Mock pywikibot Page object."""

    def __init__(self, title: str, exists: bool = True, text: str = "") -> None:
        self._title = title
        self._exists = exists
        self._text = text

    def title(self) -> str:
        return self._title

    def exists(self) -> bool:
        return self._exists

    @property
    def text(self) -> str:
        return self._text


class MockSite:
    """Mock pywikibot Site object."""

    def __init__(self, search_results: list[MockPage] | None = None) -> None:
        self._search_results = search_results or []

    def search(self, query: str, total: int = 10) -> list[MockPage]:  # noqa: ARG002
        return self._search_results[:total]


class TestGetWikiSite:
    """Tests for get_wiki_site function."""

    def test_get_wiki_site_success(self) -> None:
        """Test successful wiki site connection."""
        mock_site = MagicMock()
        with patch("packages.tools.wiki.pywikibot.Site", return_value=mock_site):
            site = get_wiki_site()
            assert site == mock_site

    def test_get_wiki_site_uses_correct_url(self) -> None:
        """Test that correct wiki URL is used."""
        mock_site = MagicMock()
        with patch("packages.tools.wiki.pywikibot.Site", return_value=mock_site) as mock:
            get_wiki_site()
            mock.assert_called_once_with(url="https://deadlock.wiki/api.php")

    def test_get_wiki_site_site_definition_error(self) -> None:
        """Test handling of SiteDefinitionError."""
        from pywikibot.exceptions import SiteDefinitionError

        with patch("packages.tools.wiki.pywikibot.Site", side_effect=SiteDefinitionError("Bad config")):
            with pytest.raises(WikiConnectionError) as exc_info:
                get_wiki_site()
            assert "Failed to configure wiki site" in str(exc_info.value)

    def test_get_wiki_site_unexpected_error(self) -> None:
        """Test handling of unexpected errors."""
        with patch("packages.tools.wiki.pywikibot.Site", side_effect=RuntimeError("Network error")):
            with pytest.raises(WikiConnectionError) as exc_info:
                get_wiki_site()
            assert "Unexpected error" in str(exc_info.value)


class TestWikiSearchTool:
    """Tests for WikiSearchTool."""

    @pytest.fixture
    def sse_events(self) -> list[ChatToolStartEvent | ChatToolEndEvent]:
        """Capture SSE events."""
        return []

    @pytest.fixture
    def sse_callback(self, sse_events: list[ChatToolStartEvent | ChatToolEndEvent]) -> Any:
        """Create SSE callback that captures events."""

        def callback(event: ChatToolStartEvent | ChatToolEndEvent) -> None:
            sse_events.append(event)

        return callback

    @pytest.fixture
    def search_tool(self, sse_callback: Any) -> WikiSearchTool:
        """Create WikiSearchTool instance."""
        return WikiSearchTool(sse_callback=sse_callback, timeout=10.0)

    @pytest.mark.asyncio
    async def test_search_returns_page_titles(self, search_tool: WikiSearchTool) -> None:
        """Test search returns list of page titles."""
        mock_pages = [MockPage("Abrams"), MockPage("Bebop"), MockPage("Calico")]
        mock_site = MockSite(search_results=mock_pages)

        with patch.object(search_tool, "_get_site", return_value=mock_site):
            result = await search_tool._run(query="hero")

        assert result == ["Abrams", "Bebop", "Calico"]

    @pytest.mark.asyncio
    async def test_search_limits_to_10_results(self, search_tool: WikiSearchTool) -> None:
        """Test search limits results to 10."""
        mock_pages = [MockPage(f"Page{i}") for i in range(15)]
        mock_site = MockSite(search_results=mock_pages)

        with patch.object(search_tool, "_get_site", return_value=mock_site):
            result = await search_tool._run(query="test")

        assert len(result) == 10

    @pytest.mark.asyncio
    async def test_search_empty_query_raises_error(self, search_tool: WikiSearchTool) -> None:
        """Test search raises ValueError for empty query."""
        with pytest.raises(ValueError) as exc_info:
            await search_tool._run(query="")
        assert "cannot be empty" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_search_whitespace_query_raises_error(self, search_tool: WikiSearchTool) -> None:
        """Test search raises ValueError for whitespace-only query."""
        with pytest.raises(ValueError) as exc_info:
            await search_tool._run(query="   ")
        assert "cannot be empty" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_search_returns_empty_for_no_results(self, search_tool: WikiSearchTool) -> None:
        """Test search returns empty list when no results found."""
        mock_site = MockSite(search_results=[])

        with patch.object(search_tool, "_get_site", return_value=mock_site):
            result = await search_tool._run(query="nonexistent")

        assert result == []

    @pytest.mark.asyncio
    async def test_execute_emits_sse_events(
        self, search_tool: WikiSearchTool, sse_events: list[ChatToolStartEvent | ChatToolEndEvent]
    ) -> None:
        """Test execute emits start and end SSE events."""
        mock_site = MockSite(search_results=[MockPage("TestPage")])

        with patch.object(search_tool, "_get_site", return_value=mock_site):
            await search_tool.execute(query="test")

        assert len(sse_events) == 2

        start_event = sse_events[0]
        assert isinstance(start_event, ChatToolStartEvent)
        assert start_event.tool_name == "wiki_search"
        assert start_event.arguments == {"query": "test"}

        end_event = sse_events[1]
        assert isinstance(end_event, ChatToolEndEvent)
        assert end_event.tool_name == "wiki_search"
        assert end_event.success is True
        assert "1 matching pages" in end_event.result_summary

    @pytest.mark.asyncio
    async def test_execute_emits_error_event_on_failure(
        self, search_tool: WikiSearchTool, sse_events: list[ChatToolStartEvent | ChatToolEndEvent]
    ) -> None:
        """Test execute emits error end event on failure."""
        with pytest.raises(ValueError):
            await search_tool.execute(query="")

        assert len(sse_events) == 2

        end_event = sse_events[1]
        assert isinstance(end_event, ChatToolEndEvent)
        assert end_event.success is False
        assert "Error:" in end_event.result_summary

    def test_name_property(self, search_tool: WikiSearchTool) -> None:
        """Test name property returns correct name."""
        assert search_tool.name == "wiki_search"

    def test_result_summary_with_results(self, search_tool: WikiSearchTool) -> None:
        """Test result summary formatting with results."""
        summary = search_tool._create_result_summary(["Page1", "Page2", "Page3"])
        assert summary == "Found 3 matching pages"

    def test_result_summary_empty_results(self, search_tool: WikiSearchTool) -> None:
        """Test result summary formatting with no results."""
        summary = search_tool._create_result_summary([])
        assert summary == "Found 0 matching pages"


class TestWikiGetPageTool:
    """Tests for WikiGetPageTool."""

    @pytest.fixture
    def sse_events(self) -> list[ChatToolStartEvent | ChatToolEndEvent]:
        """Capture SSE events."""
        return []

    @pytest.fixture
    def sse_callback(self, sse_events: list[ChatToolStartEvent | ChatToolEndEvent]) -> Any:
        """Create SSE callback that captures events."""

        def callback(event: ChatToolStartEvent | ChatToolEndEvent) -> None:
            sse_events.append(event)

        return callback

    @pytest.fixture
    def get_page_tool(self, sse_callback: Any) -> WikiGetPageTool:
        """Create WikiGetPageTool instance."""
        return WikiGetPageTool(sse_callback=sse_callback, timeout=10.0)

    @pytest.mark.asyncio
    async def test_get_page_returns_content(self, get_page_tool: WikiGetPageTool) -> None:
        """Test get page returns page content."""
        mock_page = MockPage("Abrams", exists=True, text="'''Abrams''' is a hero.")

        with (
            patch.object(get_page_tool, "_get_site", return_value=MagicMock()),
            patch("packages.tools.wiki.pywikibot.Page", return_value=mock_page),
        ):
            result = await get_page_tool._run(title="Abrams")

        assert "Abrams is a hero." in result
        assert "https://deadlock.wiki/w/Abrams" in result

    @pytest.mark.asyncio
    async def test_get_page_converts_wikitext_to_plain_text(self, get_page_tool: WikiGetPageTool) -> None:
        """Test wikitext is converted to plain text."""
        wikitext = "'''Bold''' and [[Link|text]] with {{Template}}"
        mock_page = MockPage("TestPage", exists=True, text=wikitext)

        with (
            patch.object(get_page_tool, "_get_site", return_value=MagicMock()),
            patch("packages.tools.wiki.pywikibot.Page", return_value=mock_page),
        ):
            result = await get_page_tool._run(title="TestPage")

        # mwparserfromhell.parse().strip_code() removes wikitext formatting
        assert "Bold" in result
        assert "[[" not in result
        assert "{{" not in result

    @pytest.mark.asyncio
    async def test_get_page_appends_reference_url(self, get_page_tool: WikiGetPageTool) -> None:
        """Test page content includes reference URL."""
        mock_page = MockPage("Test Page", exists=True, text="Content")

        with (
            patch.object(get_page_tool, "_get_site", return_value=MagicMock()),
            patch("packages.tools.wiki.pywikibot.Page", return_value=mock_page),
        ):
            result = await get_page_tool._run(title="Test Page")

        # URL should have underscores instead of spaces
        assert "https://deadlock.wiki/w/Test_Page" in result
        assert "Reference:" in result

    @pytest.mark.asyncio
    async def test_get_page_not_found(self, get_page_tool: WikiGetPageTool) -> None:
        """Test error message returned for missing page."""
        mock_page = MockPage("NonExistent", exists=False)

        with (
            patch.object(get_page_tool, "_get_site", return_value=MagicMock()),
            patch("packages.tools.wiki.pywikibot.Page", return_value=mock_page),
        ):
            result = await get_page_tool._run(title="NonExistent")

        assert "Error:" in result
        assert "not found" in result
        assert "NonExistent" in result

    @pytest.mark.asyncio
    async def test_get_page_empty_title_raises_error(self, get_page_tool: WikiGetPageTool) -> None:
        """Test get page raises ValueError for empty title."""
        with pytest.raises(ValueError) as exc_info:
            await get_page_tool._run(title="")
        assert "cannot be empty" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_get_page_whitespace_title_raises_error(self, get_page_tool: WikiGetPageTool) -> None:
        """Test get page raises ValueError for whitespace-only title."""
        with pytest.raises(ValueError) as exc_info:
            await get_page_tool._run(title="   ")
        assert "cannot be empty" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_execute_emits_sse_events(
        self, get_page_tool: WikiGetPageTool, sse_events: list[ChatToolStartEvent | ChatToolEndEvent]
    ) -> None:
        """Test execute emits start and end SSE events."""
        mock_page = MockPage("TestPage", exists=True, text="Test content")

        with (
            patch.object(get_page_tool, "_get_site", return_value=MagicMock()),
            patch("packages.tools.wiki.pywikibot.Page", return_value=mock_page),
        ):
            await get_page_tool.execute(title="TestPage")

        assert len(sse_events) == 2

        start_event = sse_events[0]
        assert isinstance(start_event, ChatToolStartEvent)
        assert start_event.tool_name == "wiki_get_page"
        assert start_event.arguments == {"title": "TestPage"}

        end_event = sse_events[1]
        assert isinstance(end_event, ChatToolEndEvent)
        assert end_event.tool_name == "wiki_get_page"
        assert end_event.success is True
        assert "characters" in end_event.result_summary

    @pytest.mark.asyncio
    async def test_execute_emits_error_event_on_failure(
        self, get_page_tool: WikiGetPageTool, sse_events: list[ChatToolStartEvent | ChatToolEndEvent]
    ) -> None:
        """Test execute emits error end event on failure."""
        with pytest.raises(ValueError):
            await get_page_tool.execute(title="")

        assert len(sse_events) == 2

        end_event = sse_events[1]
        assert isinstance(end_event, ChatToolEndEvent)
        assert end_event.success is False
        assert "Error:" in end_event.result_summary

    def test_name_property(self, get_page_tool: WikiGetPageTool) -> None:
        """Test name property returns correct name."""
        assert get_page_tool.name == "wiki_get_page"

    def test_result_summary_formatting(self, get_page_tool: WikiGetPageTool) -> None:
        """Test result summary formatting."""
        content = "Some page content with 50 characters total..."
        summary = get_page_tool._create_result_summary(content)
        assert "characters" in summary
        assert str(len(content)) in summary


class TestWikiToolsIntegration:
    """Integration tests for wiki tools."""

    @pytest.fixture
    def sse_events(self) -> list[ChatToolStartEvent | ChatToolEndEvent]:
        """Capture SSE events."""
        return []

    @pytest.fixture
    def sse_callback(self, sse_events: list[ChatToolStartEvent | ChatToolEndEvent]) -> Any:
        """Create SSE callback that captures events."""

        def callback(event: ChatToolStartEvent | ChatToolEndEvent) -> None:
            sse_events.append(event)

        return callback

    @pytest.mark.asyncio
    async def test_search_then_get_page_workflow(self, sse_callback: Any) -> None:
        """Test typical workflow: search then get page content."""
        search_tool = WikiSearchTool(sse_callback=sse_callback, timeout=10.0)
        get_page_tool = WikiGetPageTool(sse_callback=sse_callback, timeout=10.0)

        mock_search_site = MockSite(search_results=[MockPage("Abrams")])
        mock_page = MockPage("Abrams", exists=True, text="'''Abrams''' is a tank hero.")

        # Search for hero
        with patch.object(search_tool, "_get_site", return_value=mock_search_site):
            search_results = await search_tool.execute(query="Abrams")
        assert "Abrams" in search_results

        # Get page content
        with (
            patch.object(get_page_tool, "_get_site", return_value=MagicMock()),
            patch("packages.tools.wiki.pywikibot.Page", return_value=mock_page),
        ):
            content = await get_page_tool.execute(title=search_results[0])
        assert "tank hero" in content
