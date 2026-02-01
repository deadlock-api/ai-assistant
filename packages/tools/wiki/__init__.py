# Deadlock AI Assistant - Wiki tools package
# Tools for interacting with the Deadlock Wiki using pywikibot

from typing import Any

import mwparserfromhell
import pywikibot
import pywikibot.config
from pywikibot.exceptions import SiteDefinitionError
from pywikibot.site import BaseSite

from packages.tools.base import BaseTool, SSECallback, retry

# Suppress pywikibot logging
pywikibot.config.put_throttle = 0


class WikiConnectionError(Exception):
    """Raised when unable to connect to the wiki."""


def get_wiki_site() -> BaseSite:
    """Get the configured Deadlock Wiki site.

    Returns:
        Configured pywikibot Site instance

    Raises:
        WikiConnectionError: If unable to connect to the wiki
    """
    try:
        site: BaseSite = pywikibot.Site(url="https://deadlock.wiki/api.php")
        return site
    except SiteDefinitionError as e:
        raise WikiConnectionError(f"Failed to configure wiki site: {e}") from e
    except Exception as e:
        raise WikiConnectionError(f"Unexpected error connecting to wiki: {e}") from e


class WikiSearchTool(BaseTool):
    """Tool to search for pages on the Deadlock Wiki."""

    def __init__(self, sse_callback: SSECallback, timeout: float = 30.0) -> None:
        super().__init__(sse_callback, timeout)
        self._site: BaseSite | None = None

    @property
    def name(self) -> str:
        return "wiki_search"

    def _get_site(self) -> BaseSite:
        """Lazy initialization of wiki site connection."""
        if self._site is None:
            self._site = get_wiki_site()
        return self._site

    @retry(max_attempts=3, base_delay=1.0)
    async def _run(self, query: str) -> list[str]:
        """Search for wiki pages matching the query.

        Args:
            query: Search query string (must be non-empty)

        Returns:
            List of matching page titles (max 10 results)

        Raises:
            ValueError: If query is empty
            WikiConnectionError: If unable to connect to wiki
        """
        if not query or not query.strip():
            raise ValueError("Search query cannot be empty")

        site = self._get_site()
        results: list[str] = []

        # Use pywikibot search generator
        search_results = site.search(query, total=10)
        for page in search_results:
            results.append(page.title())

        return results

    def _create_result_summary(self, result: list[str]) -> str:
        return f"Found {len(result)} matching pages"

    def get_definition(self) -> dict[str, Any]:
        """Get the tool definition for agent configuration.

        Returns:
            Tool definition dictionary
        """
        return {
            "name": self.name,
            "description": (
                "Search for pages on the Deadlock Wiki. "
                "Returns a list of page titles matching the search query (max 10 results). "
                "Use this to find relevant wiki pages before retrieving their content."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query string to find wiki pages",
                    }
                },
                "required": ["query"],
            },
        }


class WikiGetPageTool(BaseTool):
    """Tool to retrieve page content from the Deadlock Wiki."""

    def __init__(self, sse_callback: SSECallback, timeout: float = 30.0) -> None:
        super().__init__(sse_callback, timeout)
        self._site: BaseSite | None = None

    @property
    def name(self) -> str:
        return "wiki_get_page"

    def _get_site(self) -> BaseSite:
        """Lazy initialization of wiki site connection."""
        if self._site is None:
            self._site = get_wiki_site()
        return self._site

    @retry(max_attempts=3, base_delay=1.0)
    async def _run(self, title: str) -> str:
        """Get the content of a wiki page.

        Args:
            title: Page title to fetch

        Returns:
            Page content as plain text with wiki reference URL appended,
            or error message if page not found

        Raises:
            ValueError: If title is empty
            WikiConnectionError: If unable to connect to wiki
        """
        if not title or not title.strip():
            raise ValueError("Page title cannot be empty")

        site = self._get_site()
        page = pywikibot.Page(site, title)

        if not page.exists():
            return f"Error: Page '{title}' not found on the Deadlock Wiki."

        # Get the raw wikitext content
        wikitext = page.text

        # Parse wikitext to plain text using mwparserfromhell
        wikicode = mwparserfromhell.parse(wikitext)
        plain_text = wikicode.strip_code()

        # Create wiki reference URL
        # URL-encode the title for the wiki link
        url_title = title.replace(" ", "_")
        wiki_url = f"https://deadlock.wiki/w/{url_title}"

        # Append reference to content
        return f"{plain_text}\n\n---\nReference: {wiki_url}"

    def _create_result_summary(self, result: str) -> str:
        return f"Retrieved page content ({len(result)} characters)"

    def get_definition(self) -> dict[str, Any]:
        """Get the tool definition for agent configuration.

        Returns:
            Tool definition dictionary
        """
        return {
            "name": self.name,
            "description": (
                "Retrieve the content of a page from the Deadlock Wiki. "
                "Returns the page content as plain text with a reference URL. "
                "Use wiki_search first to find the exact page title."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Exact title of the wiki page to retrieve",
                    }
                },
                "required": ["title"],
            },
        }


__all__ = [
    "WikiConnectionError",
    "WikiSearchTool",
    "WikiGetPageTool",
    "get_wiki_site",
]
